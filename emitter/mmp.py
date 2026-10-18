"""mmp -- parse a Symbian .mmp project file the way the real build does.

The .mmp is run through the C preprocessor (platform_paths.hrh macros, the
product-variant .hrh feature flags, #include'd .mmh files, #ifdef WINS/EPOC32
arms), then the keyword statements are read. Only what our emitter needs:

    TARGET TARGETTYPE UID SOURCEPATH SOURCE USERINCLUDE SYSTEMINCLUDE LIBRARY
    STATICLIBRARY DEFFILE NOSTRICTDEF MACRO EPOCSTACKSIZE EPOCHEAPSIZE

START ... END blocks (RESOURCE / BITMAP / WINS / ARMCC) are skipped.
/epoc32/include paths map to <repo>/.epoc32/include (tools/epoc32_export.py).
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BASE = os.path.join(REPO, "src")
EPOC32 = os.path.join(REPO, ".epoc32")
CEDAR = os.path.join(BASE, "oss.FCL.sf.os.deviceplatformrelease", "symbianosbld", "cedarutils")
VARIANT_HRH = os.path.join(CEDAR, "inc", "Symbian_OS_tb102sf.hrh")
CPP = "arm-none-eabi-cpp"


def variant_for(path: str) -> str:
    """The product-variant .hrh (feature flags) every mmp and every source is preprocessed under."""
    return VARIANT_HRH


PLATFORM_DEFS = ["-DEPOC32", "-DMARM", "-DEABI", "-DGCCE", "-DMARM_ARMV5", "-DARMV5",
                 "-DSBSV2", "-D__GNUC__=3"]


@dataclass
class Mmp:
    path: str
    target: str = ""
    targettype: str = ""
    uids: list[int] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)        # absolute
    userinc: list[str] = field(default_factory=list)        # absolute
    sysinc: list[str] = field(default_factory=list)         # absolute
    libs: list[str] = field(default_factory=list)           # lower, with .lib
    staticlibs: list[str] = field(default_factory=list)
    macros: list[str] = field(default_factory=list)
    deffile: str = ""
    linkas: str = ""
    nostrictdef: bool = False
    allowdlldata: bool = False                              # EPOCALLOWDLLDATA: the DLL may have writable statics
    stack: int = 0
    heap: tuple[int, int] = (0, 0)
    resources: list[str] = field(default_factory=list)      # absolute .rss paths
    # START RESOURCE blocks / RESOURCE lines: dict(rss, target, targetpath, header, uids)
    resource_blocks: list[dict] = field(default_factory=list)
    targetpath: str = ""
    version: int = 0x000A0000                               # VERSION major.minor (the E32 module version)

    @property
    def is_exe(self) -> bool:
        return self.targettype in ("exe", "exexp", "stdexe")

    def def_path(self) -> str | None:
        """Resolve the EABI .def per the abld rules (DEFFILE / default)."""
        hit = self._def_path(self.deffile)
        if hit is None or re.search(r"(^|/)(wins|marm)\w*\.def$", hit.replace("\\", "/"), re.I):
            # `#if defined(ARMCC) deffile ../eabi/eabi.def #elif/#else <the emulator one>`: our cpp is the
            # GCCE line, so that arm is dropped. Read DEFFILE again as an ARMV5 (ARMCC) build would
            armcc = [ln.split()[1] for ln in preprocess(self.path, ("-DARMCC",)).splitlines()
                     if len(ln.split()) > 1 and ln.split()[0].lower() == "deffile"]
            if armcc and armcc[-1] != self.deffile:
                hit = self._def_path(armcc[-1]) or hit
        return hit

    def _def_path(self, deffile: str) -> str | None:
        mdir = os.path.dirname(self.path)
        stem = os.path.splitext(self.target)[0]
        cands = []
        if deffile:
            d = deffile.replace("\\", "/").replace("~", "eabi")
            # unconditional cpp takes the last DEFFILE arm (bwins/bmarm): we want EABI (and -DEABI turns a literal ../EABI/ into ../1/)
            d = re.sub(r"(^|/)(bwins|bwinscw|bmarm|1)(/|$)", r"\1eabi\3", d, flags=re.I)
            if d.endswith("/"):
                d += stem
            dn, bn = os.path.split(d)
            root, ext = os.path.splitext(bn)
            ext = ext or ".def"
            names = [root + ext] if self.nostrictdef else [root + "u" + ext, root + ext]
            dirs = [os.path.join(mdir, dn)] if dn else \
                   [os.path.join(mdir, "..", "eabi"), os.path.join(mdir, "..", "EABI"), mdir]
            if dn and dn.startswith("/"):
                dirs = [os.path.join(EPOC32, dn.lstrip("/").replace("epoc32/", "", 1))]
            cands = [os.path.join(x, n) for x in dirs for n in names]
        else:
            # no DEFFILE: the build names the .def after the target -- or after LINKAS when a
            # conditional TARGET differs from it
            stems = [stem] + ([os.path.splitext(self.linkas)[0]] if self.linkas else [])
            cands = [os.path.join(mdir, "..", "eabi", s + suf) for s in stems for suf in ("u.def", ".def")]
        for c in cands:
            hit = _isfile_nocase(c)
            if hit:
                return os.path.normpath(hit)
        return None


def _isfile_nocase(path: str) -> str | None:
    """The tree was authored on Windows (EABI/ vs eabi/, eabiU.def vs eabiu.def): on a case-sensitive
    host resolve each path component case-insensitively. Returns the real file or None."""
    hit = nocase(path)
    return hit if hit and os.path.isfile(hit) else None


def nocase(path: str) -> str | None:
    """The existing file or directory `path` names when case is ignored (each component is resolved), or None."""
    if os.path.exists(path):
        return path
    path = os.path.normpath(path)
    parts = path.split("/")
    cur = "/" if path.startswith("/") else ""
    for p in parts:
        if not p:
            continue
        nxt = os.path.join(cur, p) if cur else p
        if not os.path.exists(nxt):
            try:
                hits = [e for e in os.listdir(cur or ".") if e.lower() == p.lower()]
            except OSError:
                return None
            if not hits:
                return None
            nxt = os.path.join(cur, hits[0]) if cur else hits[0]
        cur = nxt
    return cur if os.path.exists(cur) else None


def _num(tok: str) -> int:
    return int(tok, 16) if tok.lower().startswith("0x") else int(tok)


def _map_inc(p: str, mdir: str) -> str:
    r = _map_inc1(p, mdir)
    return (nocase(r) or r) if r else r


def _map_inc1(p: str, mdir: str) -> str:
    q = p.replace("\\", "/")
    if q.startswith("+"):
        q = "/epoc32" + q[1:]
    if re.match(r"^/?epoc32/", q, re.I):
        return os.path.normpath(os.path.join(EPOC32, re.sub(r"^/?epoc32/", "", q, flags=re.I)))
    if q.startswith("/"):
        # source-tree-rooted (/sf/os/...): the sibling-package mapping below; anything else resolves nothing
        if re.match(r"^/sf/", q):
            return map_sf_path(os.path.normpath(os.path.join(BASE, q.lstrip("/"))))
        return os.path.normpath(os.path.join(BASE, q.lstrip("/")))
    return map_sf_path(os.path.normpath(os.path.join(mdir, q)))


_SF_REL = re.compile(r"[\\/](os|mw|app|adapt|tools)[\\/]([^\\/]+)[\\/](.*)$")


def map_sf_path(path: str) -> str:
    """Nokia's tree is sf/<layer>/<package>/..., ours is src/oss.FCL.sf.<layer>.<package>/...: a package
    reaching into a sibling by a relative path lands outside src/. Map such a path onto the sibling checkout."""
    if os.path.exists(path):
        return path
    m = _SF_REL.search(path)
    if not m:
        return path
    layer, pkg, rest = m.groups()
    cand = os.path.join(BASE, f"oss.FCL.sf.{layer}.{pkg}", rest)
    return os.path.normpath(cand) if os.path.exists(cand) else path


_SF_INCS: list = []


def sf_mirror_incs() -> list:
    """-I arguments that make relative includes written for Nokia's sf/<layer>/<pkg> tree resolve at ANY
    nesting depth. cpp retries a missing "relative/path" against each -I dir; a mirror root holds <layer>/<pkg>
    symlinks to our checkouts plus a chain d1/d2/.../d9, and -I <mirror>/d1/../dk for k = 1..9 lands every
    `k x ../` on the mirror root."""
    if _SF_INCS:
        return _SF_INCS
    root = os.path.join(REPO, ".sf-mirror")
    if not os.path.isdir(os.path.join(root, "d1")):
        chain = root
        for i in range(1, 10):
            chain = os.path.join(chain, f"d{i}")
        os.makedirs(chain, exist_ok=True)
        for pkg in (os.listdir(BASE) if os.path.isdir(BASE) else []):
            m = re.match(r"oss\.FCL\.sf\.(os|mw|app|adapt|tools)\.(.+)$", pkg)
            if not m:
                continue
            link = os.path.join(root, m.group(1), m.group(2))
            if os.path.lexists(link):
                continue
            os.makedirs(os.path.dirname(link), exist_ok=True)
            os.symlink(os.path.join(BASE, pkg), link)
    chain = root
    for i in range(1, 10):
        chain = os.path.join(chain, f"d{i}")
        _SF_INCS.extend(["-I", chain])
    return _SF_INCS


_MARKER = re.compile(r'^#\s+\d+\s+"(.*)"')
_PATH_KWS = ("userinclude", "systeminclude", "sourcepath")


def _path_statement_dirs(path: str, mdir: str, extra_defs=()) -> dict:
    """{keyword: [the directory of the file each USERINCLUDE / SYSTEMINCLUDE / SOURCEPATH came from, in
    order]} -- from cpp's line markers (makmake resolves a relative path in an #include'd .mmh against THAT
    file, not the including mmp). START ... END blocks are skipped, as parse() skips them."""
    out = {k: [] for k in _PATH_KWS}
    cur, skipping = mdir, False
    for raw in preprocess(path, extra_defs, markers=True).splitlines():
        mk = _MARKER.match(raw)
        if mk:
            f = mk.group(1).replace("\\\\", "\\")
            same = f.startswith("<") or f == "-" or \
                os.path.normcase(os.path.abspath(os.path.join(mdir, f))) == os.path.normcase(path)
            cur = mdir if same else os.path.dirname(os.path.abspath(os.path.join(mdir, f)))
            continue
        toks = raw.split()
        if not toks:
            continue
        kw = toks[0].lower()
        if kw == "start":
            skipping = True
        elif kw == "end":
            skipping = False
        elif kw in _PATH_KWS and not skipping:
            out[kw].append(cur)
    return out


_REL_INCLUDE = re.compile(r'^(\s*#\s*include\s*")([^"]+)(")', re.M)


def preprocess(path: str, extra_defs=(), markers: bool = False) -> str:
    """cpp over an mmp. `#include "../../../../../../mw/pkg/x.mph"` (the sf/ layout, see map_sf_path): cpp gets a
    copy with such includes made absolute, from stdin in the mmp's own dir -- so every other relative include
    resolves exactly as it did."""
    mdir = os.path.dirname(path)
    text = open(path, errors="replace").read()

    def fix(m):
        inc = m.group(2).replace("\\", "/")
        full = os.path.normpath(os.path.join(mdir, inc))
        mapped = map_sf_path(full) if not os.path.exists(full) else full
        return m.group(1) + mapped.replace("\\", "/") + m.group(3) if mapped != full else m.group(0)
    fixed = _REL_INCLUDE.sub(fix, text)
    src, stdin = (path, None) if fixed == text else ("-", fixed)
    r = subprocess.run([CPP, "-undef", "-nostdinc", "-x", "c", "-w", *([] if markers else ["-P"]), *PLATFORM_DEFS,
                        *extra_defs, "-I", CEDAR, "-I", os.path.join(EPOC32, "include"),
                        "-I", mdir, "-include", variant_for(path),
                        *sf_mirror_incs(), "-idirafter", os.path.join(REPO, ".casefix"),
                        "-include", os.path.join(CEDAR, "platform_paths.hrh"), src],
                       input=stdin, cwd=mdir if stdin is not None else None,
                       capture_output=True, text=True, errors="replace")
    if r.returncode != 0 and not r.stdout:
        raise RuntimeError(f"cpp failed on {path}:\n{r.stderr[:2000]}")
    return r.stdout


def parse(path: str, extra_defs=()) -> Mmp:
    path = os.path.abspath(path)
    mdir = os.path.dirname(path)
    m = Mmp(path=path)
    srcpath = mdir
    skipping = False
    block = None
    # relative paths in an #include'd .mmh/.mmpi are relative to THAT file, as makmake resolves them; cpp's
    # line markers say which file each statement came from. The statements themselves come from the -P output
    # (it joins backslash-continued lines); a second, marker run records the origin of each path statement.
    ctx = _path_statement_dirs(path, mdir, extra_defs)
    seen_ctx = {}
    cur = mdir
    for raw in preprocess(path, extra_defs).splitlines():
        toks = raw.split()
        if not toks:
            continue
        kw, args = toks[0].lower(), toks[1:]
        if kw in _PATH_KWS and not skipping:
            i = seen_ctx.get(kw, 0)
            seen_ctx[kw] = i + 1
            cur = ctx[kw][i] if i < len(ctx.get(kw, [])) else mdir
        if skipping:
            if kw == "end":
                skipping = False
                block = None
            elif block is not None:
                if kw == "target" and args:
                    block["target"] = args[0]
                elif kw == "targetpath" and args:
                    block["targetpath"] = args[0]
                elif kw == "header":
                    block["header"] = True
                elif kw == "uid":
                    block["uids"] = [_num(a) for a in args]
            continue
        if kw == "start":
            skipping = True
            block = None
            if len(args) >= 2 and args[0].lower() == "resource":
                rss = os.path.join(srcpath, args[1].replace("\\", "/"))
                m.resources.append(rss)
                block = dict(rss=rss, target="", targetpath="", header=False, uids=[])
                m.resource_blocks.append(block)
        elif kw in ("resource", "systemresource"):
            for a in args:
                rss = os.path.join(srcpath, a.replace("\\", "/"))
                m.resources.append(rss)
                m.resource_blocks.append(dict(rss=rss, target="", header=True, uids=[],
                                              targetpath=m.targetpath or
                                              ("/system/data" if kw == "systemresource" else "")))
        elif kw == "targetpath" and args:
            m.targetpath = args[0]
        elif kw == "target" and args:
            m.target = args[0].lower()
        elif kw == "targettype" and args:
            m.targettype = args[0].lower()
        elif kw == "uid":
            m.uids = [_num(a) for a in args]
        elif kw == "sourcepath" and args:
            srcpath = _map_inc(args[0], cur)
        elif kw == "source":
            # `SOURCE /freetype2/src/base/ftinit.c`: makmake takes a leading slash as relative to the
            # SOURCEPATH, not the filesystem root -- os.path.join would drop srcpath
            m.sources += [(nocase(p) or p) for p in
                          (os.path.join(srcpath, a.replace("\\", "/").lstrip("/")) for a in args)]
        elif kw == "userinclude":
            m.userinc += [_map_inc(a, cur) for a in args]
        elif kw == "systeminclude":
            m.sysinc += [_map_inc(a, cur) for a in args]
        elif kw == "library":
            # a library named twice is ONE import block, as the build tools make it
            m.libs += [a.lower() for a in args if a.lower() not in m.libs]
        elif kw == "staticlibrary":
            m.staticlibs += [a.lower() for a in args]
        elif kw == "macro":
            # `MACRO "STACK_SCO_DATA"` is legal: the build strips the quotes
            m.macros += [a.strip('"') for a in args]
        elif kw == "deffile" and args:
            m.deffile = args[0]
        elif kw == "linkas" and args:
            m.linkas = args[0]
        elif kw == "nostrictdef":
            m.nostrictdef = True
        elif kw == "epocallowdlldata":
            m.allowdlldata = True
        elif kw == "epocstacksize" and args:
            m.stack = _num(args[0])
        elif kw == "epocheapsize" and len(args) >= 2:
            m.heap = (_num(args[0]), _num(args[1]))
        elif kw == "version" and args:
            try:
                major, _, minor = args[0].partition(".")
                m.version = (int(major) << 16) | int(minor or 0)
            except ValueError:
                pass
    return m


if __name__ == "__main__":
    import sys
    mm = parse(sys.argv[1])
    print(mm)
    print("def:", mm.def_path())
