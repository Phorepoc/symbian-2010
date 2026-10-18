#!/usr/bin/env bash
# build_rom.sh -- wrap the from-source ekern.elf as an XIP image, add the bootstrap and the userland images, write
# the ROM: out/minigui.rom.
#
# The kernel is NOT position-independent, so it is linked at its exact ROM address: the TRomImageHeader sits at
# PLACE_AT and the code at PLACE_AT+120 (= code base). The linker script splits .data/.bss to run at the kernel data
# RAM base (KERN_DATA_BASE); the bootstrap copies .data (ROM LMA) -> that RAM and zeroes .bss using the header fields.
set -eu
cd "$(dirname "$0")/.."

# The kernel .text is 256-byte aligned, so the code base must be 256-aligned or the linker inserts a gap that breaks
# the ROM offset math. CODE_BASE must also be 4 KB (page) aligned: the exception vector table is the first thing in
# .text (nk_entry.cia), so it starts at the code base, and the bootstrap ("Map vectors", bootmain.s) maps the 4 KB PAGE
# containing it to the HIVECS base 0xFFFF0000. With a base that is only 256-aligned the swi vector lands at
# 0xFFFF0000+(CODE_BASE&0xFFF) and a user-mode swi jumps to a zero vector. The 120-byte TRomImageHeader sits right
# before it (PLACE_AT = CODE_BASE - 120).
CODE_BASE="${CODE_BASE:-0x80021000}"
PLACE_AT=$(printf '0x%x' $(( CODE_BASE - 120 )))
KERN_DATA_BASE="${KERN_DATA_BASE:-0xC8000000}"     # multiple memory model KKernelDataBase
O=.ekern-build
G=src/oss.FCL.sf.os.graphics
mkdir -p out

echo "[1/4] link ekern @ code_base=$CODE_BASE (data/bss @ $KERN_DATA_BASE)"
CODE_BASE="$CODE_BASE" KERN_DATA_BASE="$KERN_DATA_BASE" bash kernel/link_ekern.sh >/dev/null
sym() { arm-none-eabi-nm "$O/ekern.elf" | awk -v s="$1" '$3==s{print "0x"$1}'; }
ENTRY=$(sym _ekern_entry); CODE_SIZE=$(sym __code_size__); DATA_LMA=$(sym __data_lma__)
DATA_SIZE=$(sym __data_size__); BSS_SIZE=$(sym __bss_size__)
echo "      entry=$ENTRY code_size=$CODE_SIZE data_lma=$DATA_LMA data_size=$DATA_SIZE bss_size=$BSS_SIZE"
arm-none-eabi-objcopy -O binary "$O/ekern.elf" "$O/ekern.bin"
echo "      ekern.bin = $(wc -c < "$O/ekern.bin") bytes"

# CFG_DebugBootRom makes the bootstrap dump ~2000+ verbose lines before the kernel starts; the default is quiet.
echo "[2/4] bootstrap"
bash tools/armasm2gas/build_bootstrap.sh

echo "[3/4] rombuild: place ekern @ $PLACE_AT + variant extension + userland images"
# The variant's code is statically in ekern; the variant "file" is a TRomImageHeader whose export table (ordinals
# 1..9, kasyborg.def) points at these ekern addresses.
V_ENTRY=$(sym _variant_e32dll)
EXPORTS=""
for s in _Z17VariantInitialisev _Z14AsicInitialisev _ZN6Syborg11DebugOutputEj \
         _ZN6Syborg12MsTickPeriodEv _ZN6Syborg13StartupReasonEv \
         _ZN6Syborg27SystemTimeInSecondsFrom2000ERi _ZN6Syborg30SetSystemTimeInSecondsFrom2000Ei \
         _ZN6Syborg5Init1Ev _ZN6Syborg5Init3Ev; do
  EXPORTS="$EXPORTS:$(sym $s)"
done
EXPORTS="${EXPORTS#:}"

# The userland: the file server (EFILE is the ROM's secondary process) and the libraries it needs, ESTART with its
# drive map (syborg's: D: = IRAM, C: = NVMemory), the FAT file system plug-in, the keymap, the shell, the 3-DLL locale,
# then every built registry component (graphical window server in the EWSRV.EXE slot, econswserv.dll in the ECONS.DLL
# slot, fbserv, the gdi/bitgdi/fntstore stack ...) and its resources.
ARGS=.ekern-build/rombuild.args
{
  printf '%s\n' \
    --xip 'Sys\Bin\euser.dll=emitter/euser_dev.dll' \
    --xip 'Sys\Bin\efsrv.dll=emitter/efsrv_dev.dll' \
    --xip 'Sys\Bin\hal.dll=emitter/hal_dev.dll' \
    --xip 'Sys\Bin\efile.exe=emitter/efile.exe' \
    --xip 'Sys\Bin\ESTART.EXE=emitter/estart.exe' \
    --xip 'Sys\Bin\ELOCAL.FSY=emitter/elocal.fsy' \
    --xip 'Sys\Bin\EKDATA.DLL=emitter/ekdata.dll' \
    --xip 'Sys\Bin\ESHELL.EXE=emitter/eshell.exe' \
    --data 'Sys\Data\estart.txt=src/oss.FCL.sf.adapt.Qemu/baseport/syborg/estart.txt' \
    --data 'System\Data\wsini.ini='$G'/windowing/windowserver/minigui/group/wsini_minigui-stripped.ini' \
    --xinfo 'databss=0x00400000,cap1=0xFFFFFFFF,cap2=0xFFFFFFFF'
  python3 kernel/rom_args.py
} > "$ARGS"
python3 tools/rombuild.py .bootstrap-build/boot.bin out/minigui.rom --primary "$O/ekern.bin" \
  --kinfo "place_at=$PLACE_AT,entry=$ENTRY,code_size=$CODE_SIZE,data_lma=$DATA_LMA,data_size=$DATA_SIZE,bss_size=$BSS_SIZE,kern_data_base=$KERN_DATA_BASE,trace_mask=0,uid1=0x1000007A" \
  --variant "entry=$V_ENTRY,exports=$EXPORTS" \
  "@$ARGS"
echo "[4/4] out/minigui.rom ($(wc -c < out/minigui.rom) bytes)"
