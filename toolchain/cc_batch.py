#!/usr/bin/env python3
"""cc_batch.py -- the kernel build's compile batch: many TUs, one process, only the stale ones compiled.

    python3 toolchain/cc_batch.py <jobfile> [-j N]

The job file holds NUL-separated records, each a NUL-separated argv terminated by an empty field (bash:
`printf '%s\\0' "$@"; printf '\\0'`):
    -k <src> <g++ args...> -o <obj>             = toolchain/symcc's arguments (kernel TU; .cia = ARM asm TU)
    --raw <compiler> <args...> -o <obj>         = a plain compiler command line
A TU is compiled only when its object is missing, the recorded command line (<obj>.cmd) differs, or the source /
a header it included (<obj>.d, -MMD) is newer than the object. Compiles run in parallel straight into g++ (no
shell start per TU). <obj>.log = the compiler's output + "rc=N". Exit 1 if any TU fails (its output printed).
Environment: SYMCC_* and SYMBIAN_GXX as for symcc.
"""
import os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMCC = os.path.join(REPO, "toolchain", "symcc")
_PREFIX = {}


def symcc_prefix(kernel, cia):
    """the g++ command line symcc would run (before the source), one bash start per (kernel, kind)"""
    key = (kernel, cia)
    if key not in _PREFIX:
        args = ["bash", SYMCC] + (["-k"] if kernel else []) + ["x.cia" if cia else "x.cpp"]
        out = subprocess.run(args, capture_output=True, env=dict(os.environ, SYMCC_PRINT="1", SYMCC_ARM="1"),
                             cwd=REPO, check=True).stdout
        _PREFIX[key] = [a for a in out.decode().split("\0") if a]
    return _PREFIX[key]


def deps_newer(obj):
    """True when a file listed in <obj>.d is missing or newer than obj (the "\\ " escape = a space in a path)"""
    d = obj + ".d"
    if not os.path.exists(d):
        return True
    t = os.path.getmtime(obj)
    text = open(d, encoding="utf-8", errors="replace").read().replace("\r", "").replace("\\\n", " ")
    text = text.replace("\\ ", "\x01")
    text = text.split(": ", 1)[1] if ": " in text else text
    for f in text.split():
        f = f.replace("\x01", " ")
        if not os.path.exists(f) or os.path.getmtime(f) > t:
            return True
    return False


def parse_jobs(path):
    fields = open(path, "rb").read().decode("utf-8").split("\0")
    jobs, cur = [], []
    for f in fields:
        if f == "":
            if cur:
                jobs.append(cur)
            cur = []
        else:
            cur.append(f)
    if cur:
        jobs.append(cur)
    return jobs


def obj_of(argv):
    for i, a in enumerate(argv):
        if a == "-o" and i + 1 < len(argv):
            return argv[i + 1]
    raise SystemExit("cc_batch: a job without -o: %r" % argv)


def command(argv):
    if argv[0] == "--raw":
        return list(argv[1:])
    kernel = argv[0] == "-k"
    rest = argv[1:] if kernel else argv
    src, rest = rest[0], rest[1:]
    return symcc_prefix(kernel, src.endswith(".cia")) + [src] + rest


def stale(argv, obj, cmdline):
    if not os.path.exists(obj):
        return "new"
    c = obj + ".cmd"
    if not os.path.exists(c) or open(c, encoding="utf-8").read() != cmdline:
        return "flags"
    if deps_newer(obj):
        return "source"
    return None


def compile_one(argv, obj, cmd, cmdline):
    for f in (obj, obj + ".cmd"):
        if os.path.exists(f):
            os.remove(f)
    r = subprocess.run(cmd + ["-MMD", "-MF", obj + ".d"], capture_output=True, text=True, errors="replace", cwd=REPO)
    out = r.stdout + r.stderr
    open(obj + ".log", "w", encoding="utf-8").write("=== %s -> %s  rc=%d\n%s" % (
        os.path.basename(argv[1] if argv[0] in ("-k",) else obj), os.path.basename(obj), r.returncode, out))
    if r.returncode == 0 and os.path.exists(obj):
        open(obj + ".cmd", "w", encoding="utf-8").write(cmdline)
    return r.returncode, out


def main():
    path = sys.argv[1]
    jobs_n = 12
    if "-j" in sys.argv:
        jobs_n = int(sys.argv[sys.argv.index("-j") + 1])
    t0 = time.time()
    jobs = parse_jobs(path)
    todo, cached = [], 0
    for argv in jobs:
        obj = obj_of(argv)
        cmdline = "\0".join(argv)
        why = stale(argv, obj, cmdline)
        if why is None:
            cached += 1
            if not os.path.exists(obj + ".log"):
                open(obj + ".log", "w").write("=== cached -> %s  rc=0\n" % os.path.basename(obj))
            continue
        todo.append((argv, obj, cmdline, why))
    failed = 0
    if todo:
        for argv, obj, cmdline, why in todo:
            print("cc_batch: compile %s (%s)" % (os.path.relpath(obj, REPO), why))
        cmds = [(argv, obj, command(argv), cmdline) for argv, obj, cmdline, why in todo]
        with ThreadPoolExecutor(max_workers=jobs_n) as ex:
            for (argv, obj, cmd, cmdline), (rc, out) in zip(cmds, ex.map(lambda c: compile_one(*c), cmds)):
                if rc != 0:
                    failed += 1
                    print("cc_batch: FAILED %s (rc=%d)\n%s" % (obj, rc, out[-4000:]))
    print("cc_batch: %d TUs: %d cached, %d compiled, %d failed, %.1f s" % (
        len(jobs), cached, len(todo) - failed, failed, time.time() - t0))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
