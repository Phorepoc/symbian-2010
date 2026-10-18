#!/usr/bin/env bash
# build_ekern.sh -- compile the unmodified 2010 EKA2 kernel (ekern.exe, multiple memory model) with toolchain/symcc -k
# (arm-none-eabi-g++). Objects go to .ekern-build/; kernel/link_ekern.sh links them.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
B="$REPO/src/oss.FCL.sf.os.kernelhwsrv/kernel/eka"
OUT="${OUT:-$REPO/.ekern-build}"; mkdir -p "$OUT"
MM="$B/memmodel"

# Extra flags beyond symcc -k. Cortex-A8/ARMv7 (the Syborg CPU): the cache-maintenance and CPS-instruction (.cia)
# paths need it; symcc defaults to ARMv5. -mno-unaligned-access: the kernel runs with SCTLR.A=1 (strict alignment).
# Modern GCC otherwise emits a single unaligned strh/ldrh/str for misaligned fields (e.g. DThread::iMState, a TUint16
# at odd offset 0x16d) -> a silent data abort during initial-thread bring-up. RVCT emitted byte accesses there.
EXTRA=(-march=armv7-a -mno-unaligned-access -U__MARM_ARMV5__ -D__MARM_ARMV7__ -D__CPU_CORTEX_A8N__
       -D__DEMAND_PAGING__
       -D__IN_KERNEL__ -D__EXPORT_MEMCPY__
       -D__MEMMODEL_MULTIPLE__ -DMM_MULTIPLE
       -I"$B/include/memmodel/epoc/multiple" -I"$B/include/memmodel/epoc/multiple/arm"
       -I"$MM/epoc/multiple" -I"$MM/epoc/multiple/arm"
       -I"$B/include/memmodel/epoc/mmubase" -I"$MM/epoc/mmubase" -I"$MM/epoc"
       -I"$B/klib" -I"$B/common"
       -I"$REPO/src/oss.FCL.sf.os.kernelhwsrv/halservices/hal/inc")

# TU list from ekern.mmp + memmodel.mmp (MM_MULTIPLE, MARM/EABI). cgcchelp is GCC32-only (skipped); we are modern
# EABI. Paths are relative to $B.
TUS="
kernel/arm/cglobals.cpp kernel/arm/cexec.cpp kernel/arm/cexec.cia kernel/arm/cinit.cpp
kernel/arm/ckernel.cpp kernel/arm/ckernel.cia kernel/arm/cipc.cpp kernel/arm/cipc.cia
kernel/arm/csched.cia kernel/arm/cutils.cpp kernel/arm/cutils.cia
kernel/arm/cache.cpp kernel/arm/cache_maintenance.cia kernel/arm/cache_maintenance.cpp
kernel/arm/cache_maintenancev7.cia kernel/arm/cache_external.cpp
kernel/array_eabi.cpp nkern/nk_old.cpp nkern/arm/nk_entry.cia kernel/arm/byte_pair.cia
klib/arm/ckdes8.cia klib/arm/cumem.cia klib/arm/cbma.cia
common/arm/carray.cia common/arm/cdes8.cia common/arm/cmem.cia
klib/dbase.cpp klib/kdes8.cpp klib/kheap.cpp klib/bma.cpp
common/alloc.cpp common/array.cpp common/des8.cpp common/mem.cpp
common/heap_hybrid.cpp common/debugfunction.cpp common/secure.cpp common/gcchelp.cpp
kernel/sglobals.cpp kernel/device.cpp kernel/eventq.cpp kernel/sexec.cpp kernel/sinit.cpp
kernel/skernel.cpp kernel/scodeseg.cpp kernel/sipc.cpp kernel/notifier.cpp kernel/object.cpp
kernel/power.cpp kernel/server.cpp kernel/sha256.cpp kernel/securerng.cpp kernel/stimer.cpp
kernel/sutils.cpp kernel/random.cpp kernel/sprocess.cpp kernel/sthread.cpp kernel/smqueue.cpp
kernel/sproperty.cpp kernel/ssharedio.cpp kernel/ssecure.cpp kernel/emi.cpp kernel/objectix.cpp
kernel/sbtrace.cpp kernel/stest.cpp kernel/sshbuf.cpp kernel/smap.cpp kernel/srandombuff.cpp
kernel/kerncorestats.cpp kernel/exstart.cpp
memmodel/epoc/putils.cpp memmodel/epoc/pcodeseg.cpp memmodel/epoc/pprocess.cpp
memmodel/epoc/pglobals.cpp memmodel/epoc/nvram.cpp
memmodel/epoc/mmubase/ramalloc.cpp
memmodel/epoc/mmubase/kblockmap.cpp memmodel/epoc/mmubase/defragbase.cpp
memmodel/epoc/mmubase/mmubase.cpp
memmodel/epoc/multiple/mchunk.cpp memmodel/epoc/multiple/mglobals.cpp
memmodel/epoc/multiple/minit.cpp memmodel/epoc/multiple/mkernel.cpp
memmodel/epoc/multiple/mcodeseg.cpp memmodel/epoc/multiple/mmu.cpp
memmodel/epoc/multiple/mprocess.cpp memmodel/epoc/multiple/mutils.cpp
memmodel/epoc/multiple/mdefrag.cpp
memmodel/epoc/multiple/arm/xglobals.cpp memmodel/epoc/multiple/arm/xinit.cpp
memmodel/epoc/multiple/arm/xipc.cia memmodel/epoc/multiple/arm/xkernel.cpp
memmodel/epoc/multiple/arm/xkernel.cia memmodel/epoc/multiple/arm/xmmu.cpp
memmodel/epoc/multiple/arm/xmmu.cia memmodel/epoc/multiple/arm/xsched.cpp
memmodel/epoc/multiple/arm/xsched.cia
"

pass=0; fail=0; failed=""
for tu in $TUS; do
  [ -f "$B/$tu" ] || { echo "MISSING $tu"; fail=$((fail+1)); failed="$failed $tu"; continue; }
  # cexec.cia declares the exec dispatch tables via asm(".word %a0" :: "i"(&f)); GCC rejects the function-address "i"
  # constraint at -O2 ("impossible constraint") but accepts it at -O0 (the file is hand-written asm anyway).
  OPT=-O2; XD=()
  case "$tu" in
    kernel/arm/cexec.cia) OPT=-O0 ;;
    # nk_entry.cia (the kernel entry _E32Startup) only has __GCC32__/__ARMCC__ branches; the __GCC32__ path is
    # EABI-compatible for this file (needs a linker-provided __CTOR_LIST__ + the exception vectors at link time).
    nk*/arm/nk_entry.cia) OPT=-O0; XD=(-D__GCC32__) ;;
    # the undefined-instruction handler (lazy VFP enable): cpudefs.h gives __SUPPORT_THUMB_INTERWORKING only to
    # ARM4T/INTERWORK builds -- without it a Thumb-2 VFP instruction is fetched as a misaligned ARM word, never seen
    # as VFP, and the thread dies.
    kernel/arm/ckernel.cpp|kernel/arm/ckernel.cia) XD=(-D__MARM_INTERWORK__) ;;
  esac
  if SYMCC_ARM=1 bash "$REPO/toolchain/symcc" -k "$B/$tu" "$OPT" \
        -fno-function-sections -fno-data-sections "${EXTRA[@]}" "${XD[@]}" \
        -o "$OUT/$(echo "$tu" | tr '/' '_').o" >/dev/null 2>"$OUT/err.log"; then
    pass=$((pass+1))
  else
    fail=$((fail+1)); failed="$failed $tu"
    echo "FAIL $tu"; grep -iE "error:|No such|not fixed" "$OUT/err.log" | head -2
  fi
done
echo "=== ekern: $pass compiled, $fail failed ==="
[ -n "$failed" ] && echo "FAILED:$failed"
[ "$fail" -eq 0 ]
