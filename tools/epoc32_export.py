"""epoc32_export -- build a real epoc32/include tree from bld.inf PRJ_EXPORTS.

The 2010 source has no epoc32/include: every component EXPORTS its public / platform headers there at build time
(bld.inf PRJ_EXPORTS). Everything above kernelhwsrv (estor, bafl, gdi, fbserv, wserv, ...) includes its neighbours via
that tree, so to compile them unmodified we do what `abld export` does: preprocess each bld.inf (platform_paths.hrh
macros), walk PRJ_EXPORTS, and copy each header to its destination under <repo>/.epoc32/include.

    python3 tools/epoc32_export.py                    # the default package set (sources.lock)
    python3 tools/epoc32_export.py oss.FCL.sf.os.graphics [more packages...]

Only destinations under /epoc32/include are honoured (no rom/ iby, data, zips). The export runs twice: with the new
header layout (/epoc32/include/platform/..., kernel/localise.h ...) and with -DSYMBIAN_OLD_EXPORT_LOCATION (headers
under include/kernel/...), the layout most mmps assume; the include tree is the union of the two.
"""
import filecmp, os, re, shutil, subprocess, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(REPO, "src")
OUT = os.path.join(REPO, ".epoc32", "include")
CPP = os.environ.get("ARM_CPP", "arm-none-eabi-cpp")
PATHS_DIR = os.path.join(BASE, "oss.FCL.sf.os.deviceplatformrelease", "symbianosbld", "cedarutils")
VARIANT = os.path.join(PATHS_DIR, "inc", "Symbian_OS_tb102sf.hrh")
DEFAULT_PKGS = [
    "oss.FCL.sf.os.deviceplatformrelease", "oss.FCL.sf.os.kernelhwsrv",
    "oss.FCL.sf.os.ossrv", "oss.FCL.sf.os.persistentdata", "oss.FCL.sf.os.textandloc",
    "oss.FCL.sf.os.graphics", "oss.FCL.sf.os.devicesrv", "oss.FCL.sf.os.security",
    "oss.FCL.sf.adapt.Qemu", "oss.FCL.sf.mw.uiresources",
    # mmf/common/mmfbase.h, icl/imagedata.h (included by bafl and the window server headers)
    "oss.FCL.sf.os.mm", "oss.FCL.sf.os.mmimaging", "oss.FCL.sf.os.mmaudio",
]
SKIP_DIRS = {"test", "tsrc", "tests", "documentation", "examples", "internal"}
SECTION = re.compile(r"^\s*PRJ_(\w+)", re.I)


def preprocess(path, old_location):
    # A bld.inf `#include "../../../../../mw/..."` written for Nokia's sf/<layer>/<pkg> tree is missing here and
    # STOPS cpp -- the whole package would export nothing. Rewrite such includes onto our checkouts
    # (emitter/mmp.py map_sf_path), feed the text on stdin from the file's own directory, and give the markers back
    # the real file name (exports() tracks the current dir from them).
    sys.path.insert(0, os.path.join(REPO, "emitter"))
    import mmp as _MMP
    d = os.path.dirname(os.path.abspath(path))
    text = open(path, errors="replace").read()

    def fix(m):
        full = os.path.normpath(os.path.join(d, m.group(2).replace("\\", "/")))
        if os.path.exists(full):
            return m.group(0)
        mapped = _MMP.map_sf_path(full)
        return m.group(1) + mapped.replace("\\", "/") + m.group(3) if mapped != full else m.group(0)
    fixed = _MMP._REL_INCLUDE.sub(fix, text)
    src, stdin = (path, None) if fixed == text else ("-", fixed)
    r = subprocess.run([CPP, "-undef", "-nostdinc", "-x", "c", "-w",
                        "-DEPOC32", "-DMARM", "-DEABI", "-DMARM_ARMV5", "-DGCCE",
                        "-D__GNUC__=3", "-DSBSV2", *(["-DSYMBIAN_OLD_EXPORT_LOCATION"] if old_location else []),
                        "-I", PATHS_DIR, "-I", OUT, "-I", os.path.dirname(path),
                        # the product-variant feature flags gate some exports
                        # (e.g. SYMBIAN_ENABLE_64_BIT_FILE_SERVER_API -> f32file64.h)
                        "-include", VARIANT,
                        *_MMP.sf_mirror_incs(), "-idirafter", os.path.join(REPO, ".casefix"),
                        "-include", os.path.join(PATHS_DIR, "platform_paths.hrh"), src],
                       input=stdin, cwd=d if stdin is not None else None,
                       capture_output=True, text=True, errors="replace")
    out = r.stdout
    if stdin is not None:
        out = out.replace('"<stdin>"', '"' + path + '"')
    return out


def preprocessed_lines(bldinf, old_location):
    """the preprocessed bld.inf as lines, with a backslash-continued export ("../data/x.xml\\" + an indented
    "    /epoc32/data/z/..." line) joined back into one: the preprocessor drops the backslash but keeps the line break."""
    lines = preprocess(bldinf, old_location).splitlines()
    marker = re.compile(r'^#\s*\d+\s+"')
    joined, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if ln.strip() and not ln.lstrip().startswith("#") and len(ln.split()) == 1:
            j = i + 1									# the preprocessor may also insert line markers after a splice
            while j < len(lines) and (not lines[j].strip() or marker.match(lines[j])):
                j += 1
            if j < len(lines) and lines[j][:1].isspace() and len(lines[j].split()) == 1 and "/" in lines[j]:
                joined.append(ln.strip() + " " + lines[j].strip())
                i = j + 1
                continue
        joined.append(ln)
        i += 1
    return joined


sys.path.insert(0, os.path.join(REPO, "emitter"))
import mmp as _MMP  # noqa: E402


def exports(bldinf, old_location):
    """yield (src_abs, dest_rel_to_epoc32_include) for one bld.inf."""
    cur_dir, section = os.path.dirname(bldinf), ""
    for ln in preprocessed_lines(bldinf, old_location):
        m = re.match(r'^#\s*\d+\s+"([^"]+)"', ln)
        if m:
            f = m.group(1).replace("\\\\", "\\")
            if not f.startswith("<"):
                cur_dir = os.path.dirname(os.path.abspath(f))
            continue
        m = SECTION.match(ln)
        if m:
            section = m.group(1).upper()
            continue
        if section != "EXPORTS":
            continue
        toks = re.findall(r'"[^"]*"|\S+', ln)
        if not toks or toks[0].startswith(":"):
            continue
        toks = [t.strip('"') for t in toks]
        src = os.path.normpath(os.path.join(cur_dir, toks[0].replace("\\", "/")))
        src = _MMP.nocase(src) or src          # the tree was authored on a case-insensitive file system
        dest = toks[1].replace("\\", "/") if len(toks) > 1 else ""
        if not dest:
            rel = os.path.basename(src)
        elif dest.startswith("+"):
            d = dest[1:].lstrip("/").lower()
            if not d.startswith("include/"):
                continue
            rel = dest[1:].lstrip("/")[len("include/"):]
        elif dest.startswith("/") or re.match(r"^[a-zA-Z]:", dest):
            d = re.sub(r"^[a-zA-Z]:", "", dest).lstrip("/")
            if not d.lower().startswith("epoc32/include/"):
                continue
            rel = d[len("epoc32/include/"):]
        elif dest.startswith("|"):
            continue                         # an export INTO the source tree: not an include-tree header
        else:
            rel = dest                       # relative dest => under epoc32/include
        if rel.endswith("/") or not rel:
            rel = rel + os.path.basename(src)
        yield src, rel


def run(pkgs, old_location):
    n_inf = n_new = n_same = n_missing = 0
    for pkg in pkgs:
        for root, dirs, files in os.walk(os.path.join(BASE, pkg)):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and d != ".git"]
            for f in files:
                if f.lower() != "bld.inf":
                    continue
                n_inf += 1
                for src, rel in exports(os.path.join(root, f), old_location):
                    if not os.path.isfile(src):
                        n_missing += 1
                        continue
                    dst = os.path.join(OUT, rel.replace("/", os.sep).lower())
                    if os.path.exists(dst) and filecmp.cmp(src, dst, shallow=False):
                        n_same += 1
                        continue
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    n_new += 1
    # several packages export the same name; the Symbian (cedarutils) platform_paths.hrh must win
    shutil.copy2(os.path.join(PATHS_DIR, "platform_paths.hrh"), os.path.join(OUT, "platform_paths.hrh"))
    # bldvariant.hrh expects the OS variant at variant/Symbian_OS.hrh
    os.makedirs(os.path.join(OUT, "variant"), exist_ok=True)
    shutil.copy2(VARIANT, os.path.join(OUT, "variant", "Symbian_OS.hrh"))
    print("bld.inf: %d   exported: %d new, %d up-to-date, %d missing-src -> %s%s"
          % (n_inf, n_new, n_same, n_missing, OUT, "  (old export location)" if old_location else ""))


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    pkgs = sys.argv[1:] or DEFAULT_PKGS
    run(pkgs, False)
    run(pkgs, True)
