#!/usr/bin/env bash
# build_nkern.sh -- compile the unmodified 2010 nanokernel (nkern + its ARM CPU layer) with toolchain/symcc -k
# (arm-none-eabi-g++). Objects go to .nkern-build/; kernel/link_ekern.sh links them.
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
B="$REPO/src/oss.FCL.sf.os.kernelhwsrv/kernel/eka"
MM="$B/memmodel"
OUT="${OUT:-$REPO/.nkern-build}"; mkdir -p "$OUT"

# The nanokernel MUST be compiled with __MEMMODEL_MULTIPLE__ (the same memory model as ekern). ncsched.cia's
# reschedule tail gates its address-space switch (load the user process page directory into TTBR0 via
# TScheduler::iProcessHandler == DoProcessSwitch) behind `#if defined(__MEMMODEL_MULTIPLE__) ||
# defined(__MEMMODEL_FLEXIBLE__)`; without it a user process is never made addressable and its first thread faults
# the instant EpocThreadFunction touches the (unmapped) user stack.
#
# The ARMv7/Cortex-A8 CPU defines ekern uses are deliberately NOT added here: they would enable the
# SET_CAR/VFP/DACR coprocessor-save macros in the reschedule frame, several of which expand without a trailing ';'
# under our GCC .cia path, and would change the nkern exception-frame layout. The nkern frame is self-consistent at
# the symcc-default architecture and interoperates with ekern.
EXTRA=(-D__MEMMODEL_MULTIPLE__ -DMM_MULTIPLE
       -I"$B/include/memmodel/epoc" -I"$B/include/memmodel/epoc/multiple"
       -I"$B/include/memmodel/epoc/multiple/arm" -I"$B/include/memmodel/epoc/mmubase"
       -I"$MM/epoc/multiple" -I"$MM/epoc/multiple/arm" -I"$MM/epoc/mmubase" -I"$MM/epoc"
       -I"$B/klib" -I"$B/common"
       -I"$REPO/src/oss.FCL.sf.os.kernelhwsrv/halservices/hal/inc")

# (nkern/arm/nk_entry.cia is part of ekern: build_ekern.sh)
CPP="nkern/nklib.cpp nkern/nkern.cpp nkern/sched.cpp nkern/dfcs.cpp nkern/nk_timer.cpp \
     nkern/nkerns.cpp nkern/arm/ncsched.cpp nkern/arm/ncutils.cpp nkern/arm/ncthrd.cpp \
     nkern/arm/ncglob.cpp"
CIA="nkern/arm/ncsched.cia nkern/arm/nctimer.cia nkern/arm/ncutils.cia nkern/arm/ncutilf.cia \
     nkern/arm/vectors.cia nkern/arm/ncmonitor.cia nkern/arm/nklib.cia common/arm/atomics.cia"

pass=0; fail=0
for tu in $CPP $CIA; do
  # -mno-unaligned-access: the kernel runs with SCTLR.A=1; GCC must never emit unaligned halfword/word accesses
  # for misaligned fields. .cia TUs need -fno-{function,data}-sections (PC-relative literal pools in hand asm).
  if SYMCC_ARM=1 bash "$REPO/toolchain/symcc" -k "$B/$tu" -O2 -mno-unaligned-access \
        -fno-function-sections -fno-data-sections "${EXTRA[@]}" \
        -o "$OUT/$(basename "$tu").o" >/dev/null 2>"$OUT/err.log"; then
    pass=$((pass+1))
  else
    fail=$((fail+1)); echo "FAIL $tu"; grep -iE "error:|No such|not fixed" "$OUT/err.log" | head -2
  fi
done
echo "=== nkern: $pass compiled, $fail failed ==="
[ "$fail" -eq 0 ]
