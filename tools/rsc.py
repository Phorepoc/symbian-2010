"""rsc -- compile a Symbian resource source (.rss) with the REAL rcomp.

What abld's resource step does: cpp-preprocess the .rss (LANGUAGE_SC, the mmp's
include paths, the product-variant hrh) then `rcomp -u` -> .rsc (+ .rsg header).
rcomp is the release's own resource compiler (the toolchain image carries it, with uidcrc, on PATH).

    python3 tools/rsc.py <in.rss> <out.rsc> [--rsg out.rsg] [-I dir ...] [--uid2 N --uid3 N]
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "emitter"))
import mmp as MMP  # noqa: E402

RCOMP = "rcomp"


def compile_rss(rss: str, rsc: str, rsg: str | None = None, incs=(), uids=(), lang="SC",
                defs=()) -> tuple[bool, str]:
    """-> (ok, log)."""
    os.makedirs(os.path.dirname(rsc), exist_ok=True)
    rpp = rsc + ".rpp"
    inc_args = []
    for d in [os.path.dirname(rss), *incs, os.path.join(MMP.EPOC32, "include")]:
        inc_args += ["-I", d]
    r = subprocess.run([MMP.CPP, "-undef", "-nostdinc", "-x", "c", "-w",
                        f"-DLANGUAGE_{lang}", "-D_UNICODE", *[f"-D{d}" for d in defs],
                        *inc_args, "-include", MMP.variant_for(rss), rss, "-o", rpp],
                       capture_output=True, text=True, errors="replace")
    if r.returncode:
        return False, "cpp: " + r.stderr[-1500:]
    args = [RCOMP, "-u", f"-o{rsc}", f"-s{rpp}", f"-i{rss}"]
    if rsg:
        os.makedirs(os.path.dirname(rsg), exist_ok=True)
        args.insert(2, f"-h{rsg}")
    if uids:
        args.insert(2, "-{" + ",".join(f"0x{u:08x}" for u in uids[:2]) + "}")
    r = subprocess.run(args, capture_output=True, text=True, errors="replace")
    ok = r.returncode == 0 and os.path.isfile(rsc)
    return ok, (r.stdout + r.stderr)[-1500:]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("rss"); ap.add_argument("rsc"); ap.add_argument("--rsg")
    ap.add_argument("-I", action="append", default=[])
    a = ap.parse_args()
    ok, log = compile_rss(os.path.abspath(a.rss), os.path.abspath(a.rsc),
                          a.rsg and os.path.abspath(a.rsg), a.I)
    print(log if not ok else f"ok {os.path.getsize(a.rsc)} B")
    raise SystemExit(0 if ok else 1)
