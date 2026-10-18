#!/usr/bin/env bash
# genexec.sh -- generate the executive headers (exec_enum.h, exec_user.h, exec_kernel.h) into toolchain/generated/ with
# the release's own kernel/eka/kernel/genexec.pl over execs.txt. genexec.pl pipes its input through
# `cpp -I- -I. -lang-c++`, flags of a 2004 GCC; a one-line `cpp` stand-in on PATH maps them to the current compiler.
set -eu
cd "$(dirname "$0")/.."
K=src/oss.FCL.sf.os.kernelhwsrv/kernel/eka/kernel
OUT="$PWD/toolchain/generated"
SHIM="$(mktemp -d)"
trap 'rm -rf "$SHIM"' EXIT
cat > "$SHIM/cpp" <<'XX'
#!/bin/sh
args=""
for a in "$@"; do case "$a" in -I-|-lang-c++) ;; *) args="$args $a";; esac; done
exec arm-none-eabi-gcc -E -x c++ $args
XX
chmod +x "$SHIM/cpp"
mkdir -p "$OUT"
cd "$K"
PATH="$SHIM:$PATH" perl genexec.pl -i execs.txt -e "$OUT/exec_enum.h" -u "$OUT/exec_user.h" -k "$OUT/exec_kernel.h"
