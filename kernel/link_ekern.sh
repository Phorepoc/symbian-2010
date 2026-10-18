#!/usr/bin/env bash
# link_ekern.sh -- link the from-source EKA2 kernel for the Syborg board.
# Prereqs: kernel/build_nkern.sh and kernel/build_ekern.sh (objects in .nkern-build/ and .ekern-build/). This script
# compiles the Syborg variant (the base port's specific/ sources), the built-in kernel extensions and drivers
# (framebuffer, display, local media, keyboard, pointer) and our glue, then links everything to .ekern-build/ekern.elf.
set -eu
cd "$(dirname "$0")/.."

B="src/oss.FCL.sf.os.kernelhwsrv/kernel/eka"
QB="src/oss.FCL.sf.adapt.Qemu/baseport/syborg"
VSPEC="$QB/specific"; VTUS="syborg.cpp interrupts.cpp assp.cpp syborg.cia interrupts.cia"
HAL="$(cd src/oss.FCL.sf.os.kernelhwsrv/halservices/hal/inc && pwd)"
MM="$B/memmodel"
LD="$(command -v arm-none-eabi-ld)"; GCC="$(command -v arm-none-eabi-gcc)"; OBJCOPY="$(command -v arm-none-eabi-objcopy)"
LIBGCC="$(dirname "$("$GCC" -mcpu=cortex-a8 -print-libgcc-file-name 2>/dev/null)")"
KO=.ekern-build; NO=.nkern-build

EXTRA=(-march=armv7-a -mno-unaligned-access -U__MARM_ARMV5__ -D__MARM_ARMV7__ -D__CPU_CORTEX_A8N__ -D__DEMAND_PAGING__
  -D__MEMMODEL_MULTIPLE__ -DMM_MULTIPLE -D__IN_KERNEL__ -D__EXPORT_MEMCPY__
  -I"$B/include/memmodel/epoc" -I"$B/include/memmodel/epoc/multiple" -I"$B/include/memmodel/epoc/multiple/arm"
  -I"$B/include/memmodel/epoc/mmubase" -I"$MM/epoc/multiple" -I"$MM/epoc/mmubase" -I"$B/klib" -I"$B/common"
  -I"$HAL" -I"$VSPEC")

echo "[1/3] compile the Syborg variant (kasyborg: HwInit hooks, interrupts, assp)"
# The objects persist between builds (toolchain/cc_cached): a TU recompiles only when it, a header it includes, or its
# command line changed; FULL=1 = from scratch.
[ "${FULL:-0}" = 1 ] && rm -rf .variant-build
mkdir -p .variant-build
# Every TU below is QUEUED (ccq = symcc's arguments) and compiled by ONE toolchain/cc_batch.py run before the link:
# only the stale ones, in parallel, straight into g++.
CCJOBS=.variant-build/.cc_jobs; : > "$CCJOBS"
ccq() { printf '%s\0' "$@" >> "$CCJOBS"; printf '\0' >> "$CCJOBS"; }
VAROBJS=()
for tu in $VTUS; do
  ccq -k "$VSPEC/$tu" -O2 -fno-function-sections -fno-data-sections \
    "${EXTRA[@]}" -o ".variant-build/$tu.o"
  VAROBJS+=(".variant-build/$tu.o")
done

# objcopy --redefine-sym needs the object to EXIST: ccq only queues the compile, so the renames are queued too and
# run right after cc_batch.
OCQ=(); ocq() { OCQ+=("$*"); }
echo "[2a/3] compile the Syborg framebuffer driver (svpframebuffer.cpp, DECLARE_STANDARD_EXTENSION)"
# The in-source LDD/screen driver, statically linked into ekern and invoked as a kernel extension (its InitExtension
# entry) from the seam. Needs the display LDD + generic driver includes on top of the kernel EXTRA set.
ccq -k "$QB/svpframebuffer/svpframebuffer.cpp" -O2 \
  -fno-function-sections -fno-data-sections "${EXTRA[@]}" \
  -I"$QB/svpframebuffer" -I"$B/include/drivers" -I"$B/include" \
  -o .variant-build/svpframebuffer.o
# The generic RDisplayChannel LOGICAL device (DECLARE_STANDARD_LDD): same include surface as the PDD; installed via
# Kern::InstallLogicalDevice from the glue so RDisplayChannel::Open finds a registered LDD+PDD pair.
ccq -k "$B/drivers/display/display.cpp" -O2 \
  -fno-function-sections -fno-data-sections "${EXTRA[@]}" \
  -I"$B/include/drivers" -I"$B/include" \
  -o .variant-build/display.o
# glue: load the driver as an extension
ccq -k kernel/ekern_fb_glue.cpp -O2 \
  -fno-function-sections -fno-data-sections "${EXTRA[@]}" \
  -I"$QB/svpframebuffer" -I"$B/include/drivers" -I"$B/include" \
  -o .variant-build/ekern_fb_glue.o

echo "[2b/3] compile the local-media stack (locmedia/ELOCD + syborg mednvmemory)"
# Built-in kernel extensions, fb-driver pattern. Per-module entry symbols (InitExtension/KernelModuleEntry/
# Create*Device -- one DLL each in a real ROM) are made unique with --redefine-sym; ekern_media_glue.cpp calls them
# in ROM order and installs the LDD/PDD factories the (absent) ELOCD.LDD/med*.pdd files would have provided.
MEMMODEL_INC=(-I"$B/include/memmodel/epoc" -I"$B/include/memmodel/epoc/multiple"
              -I"$B/include/memmodel/epoc/multiple/arm" -I"$B/include/memmodel/epoc/mmubase")
ccq -k "$B/drivers/locmedia/locmedia.cpp" -O2 \
  -fno-function-sections -fno-data-sections "${EXTRA[@]}" \
  -I"$B/drivers/locmedia" -I"$B/drivers/locmedia/traces" -I"$B/include/drivers" -I"$B/include" \
  -o .variant-build/locmedia.o
ocq \
  --redefine-sym _Z13InitExtensionv=_Z22InitExtension_locmediav \
  --redefine-sym _Z17KernelModuleEntryi=KernelModuleEntry_locmedia \
  --redefine-sym _Z19CreateLogicalDevicev=_Z28CreateLogicalDevice_locmediav \
  .variant-build/locmedia.o
ccq -k "$B/drivers/locmedia/dmasupport.cpp" -O2 \
  -fno-function-sections -fno-data-sections "${EXTRA[@]}" \
  -I"$B/drivers/locmedia" -I"$B/drivers/locmedia/traces" -I"$B/include/drivers" -I"$B/include" \
  -o .variant-build/dmasupport.o
ccq -k "$QB/mednvmemory/syborg_mednvmemory.cpp" -O2 \
  -fno-function-sections -fno-data-sections "${EXTRA[@]}" \
  -I"$QB/mednvmemory" -I"$QB/specific" -I"$B/include/drivers" -I"$B/include" \
  "${MEMMODEL_INC[@]}" \
  -o .variant-build/mednvmemory.o
ocq \
  --redefine-sym _Z13InitExtensionv=_Z21InitExtension_mednvmev \
  --redefine-sym _Z17KernelModuleEntryi=KernelModuleEntry_mednvme \
  --redefine-sym _Z20CreatePhysicalDevicev=_Z28CreatePhysicalDevice_mednvmev \
  .variant-build/mednvmemory.o
ccq -k kernel/ekern_media_glue.cpp -O2 \
  -fno-function-sections -fno-data-sections "${EXTRA[@]}" \
  -I"$B/include/drivers" -I"$B/include" \
  -o .variant-build/ekern_media_glue.o

echo "[2c/3] compile the Syborg input drivers (keyboard + pointer kexts)"
# Built-in kernel extensions (fb/media pattern); ekern_input_glue.cpp calls the per-driver InitExtension, made
# unique here with --redefine-sym.
for drv in keyboard pointer; do
  ccq -k "$QB/$drv/syborg_$drv.cpp" -O2 \
    -fno-function-sections -fno-data-sections "${EXTRA[@]}" \
    -I"$QB/$drv" -I"$QB/specific" -I"$B/include/drivers" -I"$B/include" \
    -o .variant-build/syborg_$drv.o
done
ocq --redefine-sym _Z13InitExtensionv=_Z22InitExtension_keyboardv \
  --redefine-sym _Z17KernelModuleEntryi=KernelModuleEntry_keyboard .variant-build/syborg_keyboard.o
ocq --redefine-sym _Z13InitExtensionv=_Z21InitExtension_pointerv \
  --redefine-sym _Z17KernelModuleEntryi=KernelModuleEntry_pointer .variant-build/syborg_pointer.o
ccq -k kernel/ekern_input_glue.cpp -O2 \
  -fno-function-sections -fno-data-sections "${EXTRA[@]}" \
  -I"$B/include/drivers" -I"$B/include" \
  -o .variant-build/ekern_input_glue.o
VAROBJS+=(.variant-build/svpframebuffer.o .variant-build/display.o .variant-build/ekern_fb_glue.o
  .variant-build/locmedia.o .variant-build/dmasupport.o .variant-build/mednvmemory.o .variant-build/ekern_media_glue.o
  .variant-build/syborg_keyboard.o .variant-build/syborg_pointer.o .variant-build/ekern_input_glue.o)

echo "[2/3] compile the toolchain seam (aliases via --defsym below; stubs here)"
ccq -k kernel/ekern_seam.cpp -O2 -fno-function-sections -fno-data-sections \
  "${EXTRA[@]}" -o $KO/zz_seam.o

python3 toolchain/cc_batch.py "$CCJOBS" -j "${CC_JOBS:-$(nproc)}" || exit 1
for c in "${OCQ[@]:-}"; do [ -n "$c" ] && eval "\"$OBJCOPY\" $c"; done
echo "[3/3] link -> $KO/ekern.elf"
"$LD" -T kernel/ekern.lds --defsym "__code_base__=${CODE_BASE:-0x80000000}" \
  --wrap _ZN1K17StartKernelServerEv --wrap _ZN1P15StartExtensionsEv --wrap _ZNK6TDesC83MidEii   --wrap _ZN8DProcess6CreateEiR18TProcessCreateInfoP5HBuf8 --wrap _ZN7DThread3DieE9TExitTypeiRK6TDesC8   --wrap _ZN1K5FaultENS_6TFaultE --wrap _ZN4Kern5FaultEPKci   --defsym "__kern_data_base__=${KERN_DATA_BASE:-0xC8000000}" \
  --defsym 'Des8PanicBadDesType__Fv=_Z19Des8PanicBadDesTypev' \
  --defsym 'Des8PanicPosOutOfRange__Fv=_Z22Des8PanicPosOutOfRangev' \
  --defsym 'Check__14DBTraceFilter2Ul=_ZN14DBTraceFilter25CheckEm' \
  --defsym '_ZN1K9MsgInfoE=_ZN1K7MsgInfoE' \
  -o $KO/ekern.elf \
  $KO/*.o $NO/*.o \
  "${VAROBJS[@]}" \
  -L"$LIBGCC" -lgcc --allow-multiple-definition

echo "PASS: ekern.elf linked ($(arm-none-eabi-size $KO/ekern.elf 2>/dev/null | tail -1))"
