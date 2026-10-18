#!/usr/bin/env bash
# build_bootstrap.sh -- translate + assemble + link the Symbian Syborg bootstrap (RVCT armasm -> GAS -> ELF) with the
# GNU toolchain.
#
# The bootstrap is four independent assembly units that each INCLUDE bootcpu.inc (which pulls config.inc +
# bootmacro.inc + the four h2inc-generated .inc) and link together: generic bootmain.s / bootcpu.s / bootutils.s + the
# Syborg PSL syborg.s. Config: the multiple memory model (CFG_MMMultiple), Cortex-A8 (config.inc), unicore, no custom
# vectors.
#
# Output: $OUT/boot_merged.o (relocatable ELF, all cross-unit symbols resolved) and the units' objects.
# Env:  OUT=<dir> for the artifacts; EXTRA_DEFS="-D CFG_DebugBootRom" for the verbose page-table trace.
set -euo pipefail
cd "$(dirname "$0")/../.."

AS=arm-none-eabi-as; LD=arm-none-eabi-ld; NM=arm-none-eabi-nm
KH=src/oss.FCL.sf.os.kernelhwsrv/kernel/eka
QB=src/oss.FCL.sf.adapt.Qemu/baseport/syborg

OUT="${OUT:-.bootstrap-build}"; mkdir -p "$OUT"
G="$OUT/inc"
# the includes the original build generates from the C++ headers (h2inc), computed with the same compiler as the kernel
python3 tools/armasm2gas/h2inc.py --out-dir "$G" >/dev/null

INC=(-I "$G" -I "$QB" -I "$QB/bootstrap" -I "$KH/include/kernel/arm" \
     -I "$KH/include/kernel" -I "$KH/include" -I "$KH/include/memmodel/epoc/multiple/arm")

declare -A SRC=(
  [bootmain]="$KH/kernel/arm/bootmain.s"
  [bootcpu]="$KH/kernel/arm/bootcpu.s"
  [bootutils]="$KH/kernel/arm/bootutils.s"
  [psl]="$QB/bootstrap/syborg.s"
)
DEFS="-D CFG_MMMultiple ${EXTRA_DEFS:-}"
OBJS=()
for u in bootmain bootcpu bootutils psl; do
  python3 tools/armasm2gas/armasm2gas.py "${SRC[$u]}" "${INC[@]}" $DEFS -o "$OUT/$u.S"
  "$AS" -mcpu=cortex-a8 "$OUT/$u.S" -o "$OUT/$u.o"
  OBJS+=("$OUT/$u.o")
  echo "  assembled $u"
done

"$LD" -r -o "$OUT/boot_merged.o" "${OBJS[@]}"
undef="$("$NM" "$OUT/boot_merged.o" | grep -E ' U ' | awk '{print $2}' | sort -u || true)"
if [ -n "$undef" ]; then
  echo "FAIL: unresolved symbols after link:"; echo "$undef"; exit 1
fi

# Link the bootstrap at 0 (NOT rom_base): the Symbian boot table is DCD <func> entries that BootCall treats as CODE
# OFFSETS added to iCodeBase (the actual run base, physical early / virtual later). Linking at 0 makes the entries
# offsets; linking at rom_base would make them absolute linked addresses and the first BootCall (a serial print)
# would jump to an unmapped 0x8xxxxxxx. Position-independent code -> same bytes. The ROM header's rom_base
# (0x80000000) is separate, set by rombuild.
"$LD" -Ttext=0x0 -e TheRomHeader -o "$OUT/boot.elf" \
      "$OUT/bootmain.o" "$OUT/bootcpu.o" "$OUT/bootutils.o" "$OUT/psl.o"
arm-none-eabi-objcopy -O binary "$OUT/boot.elf" "$OUT/boot.bin"
echo "PASS: bootstrap assembled and linked -> $OUT/boot.bin ($(wc -c < "$OUT/boot.bin") bytes)"
