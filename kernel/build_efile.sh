#!/usr/bin/env bash
# build_efile.sh -- compile the unmodified 2010 F32 file server (EFILE / efile.exe) with toolchain/symcc.
# EFILE is USERLAND (efile.mmp TARGETTYPE exexp), so it is compiled WITHOUT -k. This compiles each TU to an object
# in .efile-build/; emitter/build_efile_exe.py links them into the E32Image.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO/src/oss.FCL.sf.os.kernelhwsrv"
FS="$SRC/userlibandfileserver/fileserver"     # the fileserver component
KARM="$SRC/kernel/eka/kernel/arm"             # byte_pair.cia lives here
OUT="${OUT:-$REPO/.efile-build}"; mkdir -p "$OUT"

# Include/define notes (discovered against sfile/efile.mmp + efile.mmh):
#
# (1) SEPARATE-ARG -iquote for the fileserver's own include dirs. EFILE does #include "common.h" / "message.h"
#     (quote form); symcc's default -I on the KERNEL tree (kernel/eka/common) otherwise shadows them, pulling the
#     kernel's common.h -> e32panic.h -> `enum TBasePanic`, which then collides with the duplicate enumerators in
#     sfile/sf_std.h's `enum TFsFault`. -iquote is searched before every -I for quote includes, so the fileserver's
#     own inc/common.h (which does NOT pull e32panic.h) wins. The glued form (-iquote"dir") is silently ignored by
#     g++ 14; it must be two array elements.
#
# (2) -DSYMBIAN_ENABLE_64_BIT_FILE_SERVER_API : a variant-wide macro the real build sets; without it
#     KCurrentPosition64 (and the 64-bit RFile API) is undeclared.
#
# (3) -fno-access-control : sf_func.h declares TFsAddCompositeMount::AddFsToCompositeMountL private *unless*
#     __GCC32__ is defined, and sf_std.h then befriends that private member -> modern g++ rejects naming a private
#     member in a friend declaration. We deliberately do NOT define __GCC32__ (it pulls the __declspec spellings
#     that break e32def.h). -fno-access-control relaxes only the compile-time access check; no layout/codegen change.
#
# (4) halservices/hal/inc on the include path for <hal.h>.
# (5) sfat32/ on the path for <filesystem_fat.h> (sf_debug.cpp); sfat32 owns and exports it (sfat/ is obsolete).
HALINC="$SRC/halservices/hal/inc"
EXTRA=(-DSYMBIAN_ENABLE_64_BIT_FILE_SERVER_API -DSYMBIAN_DISTINCT_LOCALE_MODEL
       ${EFILE_DEBUG:+-D_DEBUG}
       -fno-access-control
       -iquote "$FS/inc" -iquote "$FS/sfile" -iquote "$FS/sfsrv" -iquote "$FS/ftrace"
       -I"$FS/sfsrv" -I"$FS/sfile" -I"$FS/inc" -I"$FS/ftrace"
       -I"$FS/sfat32"
       -I"$SRC/kernel/eka/include"
       -I"$HALINC")

# TU list from sfile/efile.mmp + efile.mmh (userland exexp; MARM/device #else branch). Skipped by design:
#   sf_lwins.cpp     -- WINS emulator only
#   sf_notifier.cpp  -- gated on SYMBIAN_F32_ENHANCED_CHANGE_NOTIFICATION (not set for this variant)
#   byte_pair.cpp / sr_rom(WINS) -- the non-MARM #else arms
TUS="
$FS/sfsrv/cl_file.cpp $FS/sfsrv/cl_cli.cpp $FS/sfsrv/cl_dir.cpp
$FS/sfile/sf_amh.cpp $FS/sfile/sf_dat.cpp $FS/sfile/sf_debug.cpp $FS/sfile/sf_dir.cpp
$FS/sfile/sf_drv.cpp $FS/sfile/sf_file.cpp $FS/sfile/sf_fmt.cpp
$FS/sfile/sf_ldr.cpp $FS/sfile/sf_main.cpp $FS/sfile/sf_nbs.cpp $FS/sfile/sf_ses.cpp
$FS/sfile/sf_svr.cpp $FS/sfile/sf_sys.cpp $FS/sfile/sf_utl.cpp $FS/sfile/sf_mnt.cpp
$FS/sfile/sf_raw.cpp $FS/sfile/sf_prel.cpp $FS/sfile/sf_disk.cpp
$FS/sfile/sf_request.cpp $FS/sfile/sf_ops.cpp $FS/sfile/sf_notify.cpp
$FS/sfile/sf_thread.cpp $FS/sfile/sf_local.cpp $FS/sfile/sf_obj.cpp
$FS/sfile/sf_ext.cpp $FS/sfile/sf_hash.cpp
$FS/sfile/sf_plugin.cpp $FS/sfile/sf_plugin_ops.cpp $FS/sfile/sf_plugin_man.cpp
$FS/sfile/Sf_plugin_util.cpp
$FS/sfile/sf_cache_man.cpp $FS/sfile/sf_cache_client.cpp
$FS/sfile/sf_file_cache.cpp
$FS/sfile/sf_memory_man.cpp $FS/sfile/sf_memory_client.cpp
$FS/sfile/sf_plugin_shim.cpp
$FS/sfile/sf_notifier_handlers.cpp
$FS/sfile/sf_pool.cpp
$FS/sfile/sf_lepoc.cpp $FS/sfile/sf_inflate.cpp $FS/sfile/sf_cache.cpp
$FS/sfile/sf_decomp.cpp $FS/sfile/sf_pgcompr.cpp
$KARM/byte_pair.cia
$FS/srom/sr_rom.cpp
"

pass=0; fail=0; failed=""
for tu in $TUS; do
  [ -f "$tu" ] || { echo "MISSING $(basename "$tu")"; fail=$((fail+1)); failed="$failed $(basename "$tu")"; continue; }
  base="$(basename "$tu")"
  # byte_pair.cia is hand-written ARM naked-asm; symcc detects .cia (-marm, __CIA__, gnu++03). Everything else is
  # userland C++ -- compiled WITHOUT -k, forced to ARM so the objects match byte_pair.cia's ARM code.
  if SYMCC_ARM=1 bash "$REPO/toolchain/symcc" "$tu" -O2 \
        -fno-function-sections -fno-data-sections "${EXTRA[@]}" \
        -o "$OUT/${base}.o" >/dev/null 2>"$OUT/err.log"; then
    pass=$((pass+1))
  else
    fail=$((fail+1)); failed="$failed $base"
    echo "FAIL $base"
    grep -iE "error:|No such|not fixed|fatal" "$OUT/err.log" | head -15
  fi
done
echo "=== efile: $pass compiled, $fail failed ($((pass+fail)) total) ==="
[ -n "$failed" ] && echo "FAILED:$failed"
[ "$fail" -eq 0 ]
