#!/bin/sh
# boot.sh -- boot out/minigui.rom on QEMU Syborg (ghcr.io/phorepoc/qemu-syborg), type `dir` into ESHELL, and write
# out/fb.png (the screen) and out/serial.log (the kernel's serial output).
#   SECS     how long the emulator runs (default 90)   DELAY   seconds to wait before typing (default 55)
set -eu
cd "$(dirname "$0")"
[ -f out/minigui.rom ] || { echo "out/minigui.rom is missing: run ./build.sh first" >&2; exit 1; }
QEMU_IMAGE="${QEMU_IMAGE:-ghcr.io/phorepoc/qemu-syborg:latest}"
BUILD_IMAGE="${BUILD_IMAGE:-ghcr.io/phorepoc/base:latest}"
SECS="${SECS:-90}"; DELAY="${DELAY:-55}"
rm -f out/fb.ppm out/fb.png out/serial.log
USER_ARGS=""
[ "$(uname -s)" = Linux ] && USER_ARGS="--user $(id -u):$(id -g)"
# The QEMU monitor is the container's stdin: `sendkey` types into the emulated keyboard. ESHELL needs a while to come up.
# shellcheck disable=SC2086
{ sleep "$DELAY"
  for k in d i r ret; do echo "sendkey $k"; sleep 0.5; done
  sleep $((SECS - DELAY - 3))
} | docker run --rm -i $USER_ARGS -v "$PWD/out:/work" \
	-e SERIAL=/work/serial.log -e FB=/work/fb.ppm -e C_DRIVE=/work/c_drive.img \
	"$QEMU_IMAGE" run-syborg /work/minigui.rom "$SECS" > out/qemu-monitor.log 2>&1 || true
[ -s out/fb.ppm ] || { echo "no framebuffer dump: see out/qemu-monitor.log" >&2; exit 1; }
# shellcheck disable=SC2086
docker run --rm $USER_ARGS -v "$PWD:/work" -w /work "$BUILD_IMAGE" python3 tools/ppm2png.py out/fb.ppm out/fb.png
echo "out/fb.png, out/serial.log"
