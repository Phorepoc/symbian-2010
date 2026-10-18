#!/bin/sh
# build.sh -- build out/minigui.rom (and out/c_drive.img) from src/ inside the Phorepoc build image.
# Needs only Docker and a populated src/ (./fetch.sh).
#
# The build runs in a Docker volume (phorepoc-symbian-2010) rather than on the bind-mounted checkout: the sources are
# ~1 GB in tens of thousands of small files, and a bind mount from a Windows or macOS host is far too slow for a
# compiler. The volume also keeps the objects between runs (incremental builds); FULL=1 starts from scratch.
# Results are copied back to out/.
set -eu
cd "$(dirname "$0")"
IMAGE="${BUILD_IMAGE:-ghcr.io/phorepoc/base:latest}"
[ -d src/oss.FCL.sf.os.kernelhwsrv ] || { echo "src/ is empty: run ./fetch.sh first" >&2; exit 1; }
mkdir -p out
exec docker run --rm -e FULL="${FULL:-}" -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
	-v "$PWD:/work" -v phorepoc-symbian-2010:/build -w /work "$IMAGE" bash scripts/build-in-container.sh
