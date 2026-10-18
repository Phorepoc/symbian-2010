#!/usr/bin/env bash
# build-steps.sh -- the build proper, run from the repository root inside the build image (scripts/build-in-container.sh
# prepares it). Every step logs to out/log/NN-name.log and stops the build on failure.
set -u
cd "$(dirname "$0")/.."
mkdir -p out/log
LOG=out/log

N=0
step() {	# step <name> <command...> : run, log, stop on failure
	N=$((N+1)); local name="$1"; shift
	local log; log=$(printf '%s/%02d-%s.log' "$LOG" "$N" "$name")
	echo "[$N] $name"; local t0=$SECONDS
	if "$@" > "$log" 2>&1; then echo "    ok ($((SECONDS-t0)) s)"; else
		echo "    FAILED (see out/log/$(basename "$log")):"; tail -n 15 "$log" | cut -c1-220; exit 1; fi
}

# The components of Symbian's minigui configuration are listed in emitter/components.py.
KEYS=$(python3 -c "import sys; sys.path.insert(0, 'emitter'); import components; print(' '.join(components.COMPONENTS))")

step executive-headers bash toolchain/genexec.sh
step kernel-nanokernel bash kernel/build_nkern.sh
step kernel-ekern bash kernel/build_ekern.sh
step euser python3 emitter/build_euser_device.py
# the epoc32/include tree the userland mmps #include (abld export equivalent)
step epoc32-export python3 tools/epoc32_export.py
step casefix python3 tools/casefix.py
step fileserver-objects bash kernel/build_efile.sh
step efsrv python3 emitter/build_efsrv_device.py
step hal python3 emitter/build_hal_device.py
step efile python3 emitter/build_efile_exe.py
# the resources first (so every .rsg exists before the components that include them), then the components
step resources python3 emitter/build_mmp.py --rsc $KEYS
step components python3 emitter/build_mmp.py $KEYS
step estart python3 emitter/build_estart_exe.py
step elocal-fsy python3 emitter/build_elocal_fsy.py
step ekdata python3 emitter/build_ekdata_dll.py
step eshell python3 emitter/build_eshell_exe.py
step rom bash kernel/build_rom.sh
# C: carries the bitmap font: fntstore's XIP-ROM font path has an alignment bug real NAND phones never hit (they use the
# RAM path), so the font lives on a non-ROM drive
step c-drive python3 tools/mkfat.py out/c_drive.img \
	"Resource/Fonts/ceurope.gdr=src/oss.FCL.sf.os.graphics/graphicstest/uibench/data/CEUROPE.GDR"
echo "done: out/minigui.rom out/c_drive.img"
