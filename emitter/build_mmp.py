"""build_mmp -- build a registered component from its REAL 2010 .mmp, unmodified.

For a component key in emitter/components.py:

  1. parse the .mmp the way abld does (emitter/mmp.py: cpp + platform_paths.hrh + the product-variant .hrh) ->
     sources, include paths, macros, UIDs, LIBRARYs, the frozen EABI .def
  2. compile every SOURCE with toolchain/symcc (parallel, cached by flags+mtime) against the generated
     epoc32/include tree (tools/epoc32_export.py)
  3. pass-1 link -> undefined set; every name with an ordinal in a LIBRARY's .def becomes a REAL import (devlink
     veneer); the rest fall to the loud unimplemented-export stub
  4. final link -> emitter/elf.py -> E32Image with the REAL export ordinal table (unbuilt ordinals -> the unimpl
     stub), into emitter/<rom name>

    python3 emitter/build_mmp.py estor [bafl gdi ...]      python3 emitter/build_mmp.py --all
    python3 emitter/build_mmp.py --rsc <keys>    (resources only: run before the bulk build so every .rsg exists)
    python3 emitter/build_mmp.py --list

ELF kept at .mmp-build/<key>/<key>.elf.
"""
from __future__ import annotations

import concurrent.futures as cf
import glob
import hashlib
import os
import subprocess
import sys
import threading

import build_euser_dll as BE
import build_euser_device as BD
import components as C
import devlink
import mmp as MMP
from e32image import build_dll
from elf import extract
from euser_def import parse_def
from refcheck import check_image, format_result

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT_ROOT = os.path.join(REPO, ".mmp-build")
STUB = BE.STUB_NAME
RT = os.path.join(REPO, "toolchain", "dll_rt")
GCCHELP = os.path.join(BE.B, "common", "gcchelp.cpp")
EE = os.path.join(REPO, "toolchain", "efile_exe")
JOBS = int(os.environ.get("MMP_JOBS", str(os.cpu_count() or 4)))
# EXEs without EPOCSTACKSIZE: the Symbian default is 0x2000, sized for RVCT Thumb frames. Our GCC ARM-mode -O2 frames
# are larger. 0x5000 = what applications that do set it typically use.
DEFAULT_STACK = 0x5000
CXXFILT = "arm-none-eabi-c++filt"


def _toolchain_rev() -> str:
    """Compiler-front-end revision: symcc, the prefix header and the include overlay change codegen for EVERY TU, so
    they are part of each object's cache key."""
    tc = os.path.join(REPO, "toolchain")
    files = [os.path.join(tc, "symcc"), os.path.join(tc, "symbian_gcc_prefix.h")]
    files += sorted(glob.glob(os.path.join(tc, "overlay", "*")))
    h = hashlib.sha1()
    for f in files:
        h.update(open(f, "rb").read())
    return h.hexdigest()


TOOLCHAIN_REV = None


def read_deps(obj):
    """Paths from <obj>.d (make syntax: backslash-newline continuations, backslash-space = a space inside a path)."""
    try:
        text = open(obj + ".d", errors="replace").read()
    except OSError:
        return None
    text = text.replace("\\\r\n", " ").replace("\\\n", " ")
    body = text.split(": ", 1)[1] if ": " in text else ""
    return [d.strip().replace("\t", " ") for d in body.replace("\\ ", "\t").split(" ") if d.strip()]


def _deps_newer(obj):
    """True if a recorded dependency is newer than the object."""
    deps = read_deps(obj)
    if not deps:
        return False
    t = os.path.getmtime(obj)
    for d in deps:
        try:
            if os.path.getmtime(d) > t:
                return True
        except OSError:
            return True          # a header it used is gone
    return False


_SYMCC_CMDS = {}
_SYMCC_LOCK = threading.Lock()


def _symcc_cmd(src, env):
    """symcc's compiler command for this source kind + environment (it depends on the extension and SYMCC_*)."""
    kind = os.path.splitext(src)[1].lower() == ".cia"
    key = (kind, env.get("SYMCC_ARM"))
    with _SYMCC_LOCK:
        if key not in _SYMCC_CMDS:
            out = subprocess.run(["bash", BE.SYMCC, "x.cia" if kind else "x.cpp"], capture_output=True,
                                 env=dict(env, SYMCC_PRINT="1"), cwd=REPO, check=True).stdout
            _SYMCC_CMDS[key] = [a for a in out.decode().split("\0") if a]
        return _SYMCC_CMDS[key]


def _compile_one(src, obj, flags, env):
    global TOOLCHAIN_REV
    if TOOLCHAIN_REV is None:
        TOOLCHAIN_REV = _toolchain_rev()
    key = hashlib.sha1((TOOLCHAIN_REV + " ".join(flags) + src).encode()).hexdigest()
    stamp = obj + ".key"
    if (os.path.exists(obj) and os.path.exists(stamp) and open(stamp).read() == key
            and os.path.getmtime(obj) >= os.path.getmtime(src) and not _deps_newer(obj)):
        return src, 0, ""
    # -MMD: <obj>.d = every header this TU read; feeds _deps_newer (a header change recompiles its dependents).
    # g++ directly with symcc's command line (SYMCC_PRINT, cached per flag set): a shell start per TU is slow.
    r = subprocess.run([*_symcc_cmd(src, env), src, "-O2", "-w", *flags, "-MMD", "-MF", obj + ".d", "-o", obj],
                       capture_output=True, text=True, errors="replace", env=env, cwd=REPO)
    if r.returncode == 0:
        open(stamp, "w").write(key)
    return src, r.returncode, (r.stdout + r.stderr)


def _libsupcxx() -> str:
    return subprocess.run([BE.GCC, "-marm", "-march=armv5te", "-print-file-name=libsupc++.a"],
                          check=True, capture_output=True, text=True).stdout.strip()


DATA_EXPORTS: set[str] = set()      # every DATA-flagged export seen in a provider .def


def gcc_mangled(name):
    """RVCT mangles an extern "C" function type with a Y (Itanium <function-type> ::= F [Y] ...); GCC never writes it.
    A frozen .def name with "FY" is therefore the GCC symbol with "F"."""
    return name.replace("FY", "F") if name.startswith("_Z") else name


def provider_maps(libs):
    """name -> (rom dll, ordinal), honouring the mmp LIBRARY list; euser wins."""
    resolve = {}
    order = []
    for lib in libs:
        stem = lib.rsplit(".", 1)[0]
        if any(o[0] == stem for o in order):
            continue
        if stem in C.BUILTIN:
            rom, defp = C.BUILTIN[stem]
        elif stem in C.COMPONENTS:
            m = MMP.parse(os.path.join(MMP.BASE, C.COMPONENTS[stem]["mmp"]))
            rom = C.rom_name(stem, m.targettype)
            defp = (os.path.join(MMP.BASE, C.COMPONENTS[stem]["def"])
                    if "def" in C.COMPONENTS[stem] else m.def_path())
        else:
            print(f"  ! LIBRARY {lib}: no provider registered (its imports -> stubs)")
            continue
        if not defp:
            print(f"  ! LIBRARY {lib}: no .def found")
            continue
        order.append((stem, rom, defp))
    for stem, rom, defp in sorted(order, key=lambda t: t[0] == "euser"):   # euser last = wins
        d = parse_def(defp)
        for ordn, name in d.by_ordinal.items():
            if name not in d.absent:                # an ABSENT slot provides nothing
                resolve[name] = (rom, ordn)
                if gcc_mangled(name) != name:       # the importer's GCC-mangled spelling of the same function
                    resolve.setdefault(gcc_mangled(name), (rom, ordn))
        DATA_EXPORTS.update(d.data_names)
        if stem == "euser":
            for name in BD.EXTRA_EXPORTS:
                resolve[name] = (rom, BD.extra_export_ordinal(name, d.max_ordinal))
    return resolve, [rom for _, rom, _ in order]


def _is_registration(m, block) -> bool:
    # the registration is `<target stem>.rss`, or (SYMBIAN_SECURE_ECOM idiom) `<dll uid3>.rss` with
    # `TARGET <target stem>.rsc` inside the block
    stem = os.path.splitext(m.target)[0].lower()
    if m.targettype != "plugin":
        return False
    by_target = [b for b in m.resource_blocks if os.path.splitext(b.get("target") or "")[0].lower() == stem]
    if len(by_target) > 1:
        by_target = [b for b in by_target if not b.get("targetpath") and not b.get("header")] or by_target
    if by_target:
        return block in by_target
    return stem == os.path.splitext(os.path.basename(block["rss"]))[0].lower()


def build_resources(key: str, m, rom: str) -> int:
    """START RESOURCE / RESOURCE -> the REAL rcomp (tools/rsc.py): .rsg into .epoc32/include, .rsc into
    emitter/rsc/<targetpath>/, and a ROM manifest emitter/<rom>.romfiles (`<rom path>=<host path>` lines) the ROM
    script adds."""
    # (a plug-in's ECOM registration <target stem>.rss is handled with the image itself)
    blocks = [b for b in m.resource_blocks if not _is_registration(m, b)]
    if not blocks:
        return 0
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import rsc
    lines, bad = [], 0
    for b in blocks:
        stem = os.path.splitext(b["target"] or os.path.basename(b["rss"]))[0].lower()
        tp = (b["targetpath"] or "resource/apps").replace("\\", "/").strip("/")
        out = os.path.join(HERE, "rsc", *tp.split("/"), stem + ".rsc")
        rsg = os.path.join(MMP.EPOC32, "include", stem + ".rsg") if b["header"] else None
        ok, log = rsc.compile_rss(b["rss"], out, rsg, incs=m.userinc + m.sysinc, uids=b["uids"],
                                  defs=C.COMPONENTS[key].get("rdefs", ()))
        if ok:
            lines.append(tp.replace("/", "\\") + "\\" + stem + ".rsc=" +
                         os.path.relpath(out, REPO).replace("\\", "/"))
        else:
            bad += 1
            print(f"  RSC FAIL {os.path.basename(b['rss'])}: " + " | ".join(log.strip().splitlines()[-3:])[:400])
    with open(os.path.join(HERE, rom + ".romfiles"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"  resources: {len(lines)}/{len(blocks)} compiled (rcomp)")
    return bad


def compile_registration(key: str, m, rom: str) -> None:
    """A plug-in's ECOM registration resource -> emitter/<stem>.rsc (z:/resource/plugins/)."""
    if m.targettype == "plugin":
        sys.path.insert(0, os.path.join(REPO, "tools"))
        import rsc as RSC
        for rss in [b["rss"] for b in m.resource_blocks if _is_registration(m, b)]:
            out = os.path.join(HERE, os.path.splitext(rom)[0] + ".rsc")
            ok, log = RSC.compile_rss(rss, out, None, incs=m.userinc + m.sysinc)
            if ok:
                print(f"  registration: {os.path.basename(rss)} -> {os.path.relpath(out, REPO)} "
                      f"(rcomp, {os.path.getsize(out)} B)")
            else:
                print("  ! rcomp failed on registration: " + log.strip()[-300:])


def build(key: str, rsc_only: bool = False) -> int:
    comp = C.COMPONENTS[key]
    m = MMP.parse(os.path.join(MMP.BASE, comp["mmp"]))
    rom = C.rom_name(key, m.targettype)
    if rsc_only:
        r = build_resources(key, m, rom)
        compile_registration(key, m, rom)
        return r
    build_resources(key, m, rom)
    work = os.path.join(OUT_ROOT, key)
    os.makedirs(work, exist_ok=True)
    print(f"=== {key}: {m.target} ({m.targettype}) -> {rom}   [{len(m.sources)} sources]")

    # USERINCLUDE wins quoted lookups (abld order): -iquote precedes every -I, incl. symcc's own kernel dirs.
    incs = [x for p in m.userinc for x in ("-iquote", p)]
    incs += [f"-I{p}" for p in m.userinc + m.sysinc]
    # -fno-threadsafe-statics: RVCT/GCCE-era Symbian has no __cxa_guard_* runtime
    flags = (["-fno-access-control", "-fno-threadsafe-statics", "-include", MMP.variant_for(m.path),
              # e32def.h sets this for the Symbian GCC-ARM config (__GCC32__, which our prefix deliberately leaves
              # off): class-typed header consts become macros instead of per-TU dynamic-init globals (= DLL WSD).
              "-D__NO_CLASS_CONSTS__",
              "-D__DLL__" if not m.is_exe else "-D__EXE__"]
             + [f"-D{x}" for x in m.macros + comp.get("defs", [])]
             + incs + comp.get("flags", [])
             # the trace compiler's generated OstTraceDefinitions.h is not in the release: our traces-off stand-in,
             # searched after every real include dir
             + ["-idirafter", os.path.join(REPO, "toolchain", "shims", "ost_off")]
             # 2010-era C++ (`nullptr` as an identifier)
             + ["-std=gnu++03"]
             # RVCT-only __declspec(notshared/...) under __EABI__: no meaning to GCC, whose import/export is
             # already blanked (IMPORT_C/EXPORT_C in the prefix header)
             + ["-D__declspec(x)="]
             # _FOFF = __builtin_offsetof, a constant expression; the prefix's pointer-arithmetic form is for euser's
             # runtime-index HBufC allocation, kept for the components that use a run-time index (foff_dynamic)
             + ([] if comp.get("foff_dynamic")
                else ["-include", os.path.join(REPO, "toolchain", "shims", "foff_builtin.h")]))
    # RTTI: opt-in (`rtti`).
    rtti = comp.get("rtti", False)
    if rtti:
        flags.append("-frtti")
    env = dict(os.environ, SYMCC_ARM="1")
    drop = {d.lower() for d in comp.get("drop", [])}
    srcs = [] if comp.get("nosrc") else [s for s in m.sources if os.path.basename(s).lower() not in drop]
    srcs += [os.path.join(REPO, e) for e in comp.get("extra", [])]
    rt = [GCCHELP, os.path.join(RT, "dll_crt.cpp"), os.path.join(BE.ED, "unimpl_export.cpp")]
    if not m.is_exe:
        # edllstub.lib (every DLL links it): the REAL Dll::Tls/SetTls/FreeTls + Dll::FileName
        rt += [os.path.join(BE.B, "euser", "epoc", "up_dll_tls.cpp"),
               os.path.join(BE.B, "euser", "epoc", "up_dll_file.cpp")]
    if rtti:
        rt += [os.path.join(RT, "rtti_rt.cpp")]
    if m.is_exe:
        rt += [os.path.join(RT, "exe_dso_handle.cpp")]
        rt += [os.path.join(BE.B, "euser", "epoc", "arm", "uc_exe.cia"),
               os.path.join(EE, "dev_exe_startup.cpp")]

    jobs, objs = [], []
    for i, s in enumerate(srcs):
        obj = os.path.join(work, f"{i:03d}_{os.path.basename(s)}.o")
        if s.endswith(".CIA"):
            # symcc keys its ARM naked-asm mode on a lower-case .cia; an upper-case .CIA reaches g++ as an unknown
            # extension = "linker input", silently compiles NOTHING
            wrap = os.path.join(work, f"{i:03d}_{os.path.basename(s)[:-4].lower()}_ascia.cia")
            text = '#include "%s"\n' % s
            if not os.path.isfile(wrap) or open(wrap).read() != text:
                with open(wrap, "w") as fh:
                    fh.write(text)
            s = wrap
        if s.endswith(".C"):
            # g++ takes an UPPER-case .C for C++ (abld, on a case-blind host, built it as C). Compile a lower-case .c
            # that just #includes it -- quoted includes still resolve next to the real file.
            wrap = os.path.join(work, f"{i:03d}_{os.path.basename(s)[:-2].lower()}_asc.c")
            text = '#include "%s"\n' % s
            if not os.path.isfile(wrap) or open(wrap).read() != text:
                with open(wrap, "w") as fh:
                    fh.write(text)
            s = wrap
        jobs.append((s, obj, flags))
        objs.append(obj)
    for s in rt:
        obj = os.path.join(work, "rt_" + os.path.basename(s) + ".o")
        jobs.append((s, obj, []))
        objs.append(obj)
    failed = []
    with cf.ThreadPoolExecutor(JOBS) as ex:
        for src, rc, out in ex.map(lambda j: _compile_one(*j, env), jobs):
            if rc != 0:
                failed.append((src, out))
    if failed:
        seen = {}
        for src, out in failed:
            errs = [l for l in out.splitlines() if "error" in l or "fatal" in l][:int(os.environ.get("MMP_ERRLINES", "4"))]
            sig = "|".join(e.split(": ", 1)[-1] for e in errs[:1])
            seen.setdefault(sig, []).append((src, errs))
        for sig, group in seen.items():           # one report per distinct first error
            src, errs = group[0]
            more = f"  (+{len(group) - 1} more TUs, same first error)" if len(group) > 1 else ""
            print(f"  COMPILE FAIL {os.path.basename(src)}{more}")
            for l in errs:
                print("      " + l[-200:])
        print(f"  {len(failed)}/{len(jobs)} TUs failed")
        return 1

    if not m.is_exe:
        entry_o = os.path.join(work, "dll_entry.s.o")
        subprocess.run([BE.GCC, "-march=armv5te", "-marm", "-c",
                        f"-DDLL_UID3=0x{(m.uids[1] if len(m.uids) > 1 else 0):08x}",
                        # a DLL allowed writable data (EPOCALLOWDLLDATA / allow_wsd) gets its static constructors
                        # run too
                        *(["-DDLL_STATIC_CTORS"] if comp.get("allow_wsd") or m.allowdlldata else []),
                        os.path.join(RT, "dll_entry.S"), "-o", entry_o], check=True)
        objs.insert(0, entry_o)

    defp = (os.path.join(MMP.BASE, comp["def"]) if "def" in comp else m.def_path())
    if m.targettype == "plugin" and "def" not in comp and not m.deffile:
        # no DEFFILE statement: the default-named .def next door can be a stale pre-ECOM leftover -- the toolchain
        # never reads it for a plug-in, whose export table is fixed.
        defp = None
    defmap = parse_def(defp) if defp else None
    if m.targettype == "plugin" and not defmap:
        # ECOM plug-in: abld freezes exactly one export, the proxy table accessor
        from euser_def import DefMap
        defmap = DefMap({1: "_Z24ImplementationGroupProxyRi"}, 1)
    if m.targettype == "ani" and not defmap:
        # WSERV animation plug-in: one frozen export, the CAnimDll factory
        from euser_def import DefMap
        defmap = DefMap({1: "_Z15CreateCAnimDllLv"}, 1)
    if not m.is_exe and not defmap:
        print("  ! no .def found -- DLL would export nothing")
        return 1
    defined = BE._nm("--defined-only", *objs)
    exportable = sorted(set(defmap.by_ordinal.values()) & defined) if defmap else []
    # .def names in RVCT's spelling of an extern "C" function type: alias them to GCC's symbol (--defsym below)
    y_alias = {n: gcc_mangled(n) for n in (defmap.by_ordinal.values() if defmap else [])
               if n not in defined and gcc_mangled(n) != n and gcc_mangled(n) in defined}
    exportable = sorted(set(exportable) | set(y_alias))
    entry = "_E32Startup" if m.is_exe else "_E32Dll"
    roots = [n for n in exportable if n not in y_alias] + sorted(set(y_alias.values())) + [STUB, entry]
    ld = os.path.join(HERE, ("efile" if m.is_exe else "mmp_dll") + ".ld")

    def link(all_objs, out, ignore):
        rsp = os.path.join(work, "link.rsp")
        with open(rsp, "w") as fh:
            fh.write("\n".join(f"-Wl,-u,{n}" for n in roots) + "\n")
            fh.write("".join(f"-Wl,--defsym,{a}={g}\n" for a, g in y_alias.items()))
            fh.write("\n".join('"' + o + '"' for o in all_objs))
        archives = [*([_libsupcxx()] if rtti else []), BE._libgcc()]
        args = [BE.GCC, "-nostdlib", "-Wl,-q", "-Wl,--gc-sections", "-Wl,--no-demangle",
                f"-Wl,-T,{ld}", "-march=armv5te", "-marm", f"@{rsp}", *archives, "-o", out]
        if ignore:
            args.insert(1, "-Wl,--unresolved-symbols=ignore-all")
        # PC-relative linker veneers for far calls: the default `ldr ip,[pc]` + an ABSOLUTE target word is not
        # reported by --emit-relocs and would stay unrelocated at run time.
        args.insert(1, "-Wl,--pic-veneer")
        r = subprocess.run(args, capture_output=True, text=True, errors="replace")
        if r.returncode:
            print("\n".join(l for l in r.stderr.splitlines() if "warning:" not in l)[-3000:])
            raise SystemExit(f"{key}: link failed")

    elf1 = os.path.join(work, key + ".pass1.elf")
    link(objs, elf1, True)
    missing = sorted(BE._nm("--undefined-only", elf1) - {""})
    # `libs`: extra providers the mmp does not name
    resolve, dll_order = provider_maps(comp.get("libs", []) + m.libs)
    imports = {n: resolve[n] for n in missing if n in resolve and resolve[n][0] != rom}
    # typeinfo (_ZTI/_ZTS) is never imported: RVCT-era .defs rarely freeze it and a provider built without RTTI
    # would export garbage. Each unresolved _ZTI<X> is synthesized locally as a ROOT class (__class_type_info + its
    # name): a dynamic_cast whose target lies below the DLL boundary works (types compare by name), one that would
    # have to walk past it fails cleanly with NULL.
    ti_names = [n for n in missing if n.startswith("_ZTI")]
    for n in missing:
        if n.startswith(("_ZTI", "_ZTS")):
            imports.pop(n, None)
    stub_names = [n for n in missing if n not in imports and not n.startswith(("_ZTI", "_ZTS"))]
    # DATA imports (vtables / patchable consts): no veneer -- each ABS32 reference site becomes the import slot
    # itself (elf.extract data_import_sites).
    data_imps = {n: imports.pop(n) for n in list(imports)
                 if n in DATA_EXPORTS or n.startswith(("_ZTV", "_ZTI", "_ZTS"))}
    veneer_o = devlink.make_veneers(work, imports, key)
    stub_s = os.path.join(work, "unimpl_stubs.s")
    with open(stub_s, "w") as fh:
        fh.write("\t.text\n\t.arm\n")
        for n in stub_names:
            fh.write(f"\t.globl {n}\n\t.type {n},%function\n{n}:\n\tb {STUB}\n")
        for n in data_imps:       # link-time placeholder; never read at runtime
            fh.write(f"\t.globl {n}\n{n}:\n\t.word 0\n")
        if ti_names:
            fh.write("\t.section .rodata.synth_typeinfo,\"a\"\n\t.align 2\n")
            for n in ti_names:
                fh.write(f"\t.globl {n}\n{n}:\n"
                         f"\t.word _ZTVN10__cxxabiv117__class_type_infoE+8\n\t.word 1f\n"
                         f"1:\t.asciz \"{n[4:]}\"\n\t.align 2\n")
    stub_o = stub_s + ".o"
    subprocess.run([BE.GCC, "-march=armv5te", "-marm", "-c", stub_s, "-o", stub_o], check=True)

    elf2 = os.path.join(work, key + ".elf")
    link(objs + [veneer_o, stub_o], elf2, False)
    with open(elf2, "rb") as fh:
        ext = extract(fh.read(), data_imports=frozenset(data_imps))
    if entry not in ext.symbols:
        raise SystemExit(f"{key}: entry {entry} missing")
    ia = ext.symbols.get("__init_array_end", 0) - ext.symbols.get("__init_array_start", 0)
    if not m.is_exe and ia and (comp.get("allow_wsd") or m.allowdlldata):
        print(f"  {ia // 4} static constructors, run by _E32Dll on process attach")
    elif not m.is_exe and ia:
        print(f"  ! DLL has {ia // 4} static constructors (NOT run)")
    if not m.is_exe and (ext.data or ext.bss_size) and not (comp.get("allow_wsd") or m.allowdlldata):
        print(f"  ! DLL has writable static data: data={len(ext.data)} bss={ext.bss_size}")
        syms = subprocess.run([BE.NM, "-S", "--size-sort", "-C", elf2], capture_output=True,
                              text=True).stdout.splitlines()
        for l in [l for l in syms if l.split()[2:3] and l.split()[2] in "bBdD"][-8:]:
            print("      " + l)
        return 1

    export_offsets, real = [], 0
    if defmap:
        stub_off = ext.symbols[STUB] - ext.code_base
        ex_set = set(exportable)
        for ordn in range(1, defmap.max_ordinal + 1):
            n = defmap.name(ordn)
            if n in ex_set and n in ext.symbols:
                export_offsets.append(ext.symbols[n] - ext.code_base)
                real += 1
            else:
                export_offsets.append(stub_off)
    import_list = devlink.import_blocks(ext, imports, dll_order=dll_order)
    if data_imps:
        blocks = {d: list(e) for d, e in import_list}
        n_sites = 0
        for n, (dll, ordn) in data_imps.items():
            for off, addend in ext.data_import_sites.get(n, []):
                if addend > 0xFFFF:
                    raise SystemExit(f"{key}: DATA import {n} addend {addend:#x} > 16 bits")
                blocks.setdefault(dll, []).append((off, ordn, addend))
                n_sites += 1
        import_list = [(d, sorted(blocks[d])) for d in dll_order if d in blocks]
        print(f"  {len(data_imps)} DATA imports -> {n_sites} direct slots")
    n_imp = sum(len(e) for _, e in import_list)
    print(f"  code {len(ext.code)} B  data {len(ext.data)}  bss {ext.bss_size}   "
          f"exports {real}/{defmap.max_ordinal if defmap else 0} real   imports {n_imp} ("
          + ", ".join(f"{d} {len(e)}" for d, e in import_list) + f")   {len(stub_names)} link stubs")
    if stub_names:
        dm = subprocess.run([CXXFILT], input="\n".join(stub_names[:12]), capture_output=True,
                            text=True).stdout.splitlines()
        for l in dm:
            print("      stub: " + l)

    uid2 = m.uids[0] if m.uids else 0
    uid3 = m.uids[1] if len(m.uids) > 1 else 0
    # the mmp's VERSION: SDK-built images import versioned names (libc{00010001}) and the loader requires the SAME major
    kw = dict(module_version=m.version)
    if m.is_exe:
        kw.update(exe=True, stack_size=comp.get("stack") or m.stack or DEFAULT_STACK,
                  heap_min=m.heap[0] or 0x1000, heap_max=comp.get("heap_max") or m.heap[1] or 0x100000)
    img = build_dll(ext.code, export_offsets, uid2=uid2, uid3=uid3,
                    entry_point=ext.symbols[entry] - ext.code_base,
                    imports=import_list, code_relocs=ext.code_relocs, code_base=ext.code_base,
                    data=ext.data, data_base=ext.data_base, bss_size=ext.bss_size,
                    data_relocs=ext.data_relocs, code_reloc_types=ext.code_reloc_types,
                    data_reloc_types=ext.data_reloc_types, **kw)
    out_path = os.path.join(HERE, rom)
    with open(out_path, "wb") as fh:
        fh.write(img)
    compile_registration(key, m, rom)
    result = check_image(img)
    print(f"  wrote emitter/{rom} ({len(img)} B)  " + ("[ACCEPTED]" if result.ok else format_result(result)))
    return 0 if result.ok else 1


def main() -> int:
    args = sys.argv[1:]
    if not args or args == ["--list"]:
        for k, c in C.COMPONENTS.items():
            print(f"{k:20s} {c['mmp']}")
        return 0
    rsc_only = "--rsc" in args
    args = [a for a in args if a != "--rsc"]
    keys = list(C.COMPONENTS) if args == ["--all"] else args
    rc = 0
    for k in keys:
        try:
            rc |= build(k, rsc_only)
        except (Exception, SystemExit) as e:   # noqa: BLE001 -- one bad mmp (or a `link failed` SystemExit) must
            print(f"=== {k}: FAILED: {(str(e).splitlines() or ['?'])[0][:200]}")   # not stop a batch
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
