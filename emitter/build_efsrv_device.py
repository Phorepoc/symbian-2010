"""build_efsrv_device -- the from-source EFSRV.DLL (F32 client library).

The second OS DLL built from unmodified 2010 source with the modern toolchain,
after euser (emitter/build_euser_device.py). EFILE.EXE (the file server) imports
~41 F32 client VALUE-CLASSES from efsrv -- TParse*/TEntry/CDir/TDriveUnit/
TVolumeInfo/RFileClamp/TEntryArray -- pure client-side utility logic over
descriptors + heap (no RFs server session needed for the class methods
themselves).

Same ELF->E32Image pipeline as the euser build; reuses build_euser_dll's link/
emit helpers. The value-class TUs are compiled against the fileserver sources
(src/...) with the same include shims build_efile.sh
uses. euser symbols they reference resolve at runtime by importing euser.dll; in
this standalone DLL build they fall to the unimpl stub (the real import wiring is
the EFILE-emission step). What matters here: every efsrv export EFILE imports is
built REAL from source.

Run from repo root:  python3 emitter/build_efsrv_device.py [out.dll]
"""
from __future__ import annotations

import os
import sys

import build_euser_dll as BE
import devlink
from e32image import build_dll
from elf import extract
from euser_def import parse_def
from refcheck import check_image, format_result

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CODE_BASE = BE.CODE_BASE
SYMCC = BE.SYMCC

# The fileserver sources (the same tree kernel/build_efile.sh compiles EFILE from).
SRC = os.path.join(REPO, "src", "oss.FCL.sf.os.kernelhwsrv")
FS = os.path.join(SRC, "userlibandfileserver", "fileserver")
DEF_PATH = os.path.join(FS, "eabi", "efsrvu.def")
UID3 = 0x100039E4   # EFSRV.DLL third UID (well-known Symbian F32 client UID)
STUB_NAME = BE.STUB_NAME

# The efsrv client value-class TUs (the subset EFILE imports; not the RFs/RFile
# session client, which EFILE compiles in itself -- cl_file/cl_cli/cl_dir).
#   cl_parse  TParse/TParseBase/TParsePtr[C]        cl_drive  TDriveUnit
#   cl_entry  TEntry                                cl_cdir   CDir/TEntryArray
#   cl_clamp  RFileClamp                            cl_cli    TVolumeInfo + misc
CL = os.path.join(FS, "sfsrv")
SOURCES = [os.path.join(CL, f + ".cpp") for f in
           ("cl_parse", "cl_drive", "cl_entry", "cl_cdir", "cl_clamp", "cl_cli",
            # cl_file/cl_fmt: RFile + RFormat session clients -- ESTART (a real
            # efsrv CLIENT process, unlike EFILE) reads Z:\SYS\DATA\ESTART.TXT
            # and formats corrupt drives through these.
            "cl_file", "cl_fmt",
            # cl_find: TFindFile -- EFILE's StartupInitComplete handler
            # (FindHitListFile in sf_main) constructs one to look for the
            # corrupt-file-names hit list; without it the 0x5d request panics
            # in the unimpl stub.
            "cl_find",
            # cl_dir: RDir (directory session client) -- EWSRV's notifier server
            # (CNotifierManager::RegisterL -> TFindFile::FindWildByDir ->
            # RFs::GetDir -> RDir::Open) scans for notifier plug-ins at startup.
            # Without it RDir::Open was a stub -> the notifier thread panicked
            # EUSRNIMP before signalling its ready-semaphore, so EWSRV's main
            # thread blocked forever on NotifierSemaphore.Wait() and its window-
            # server scheduler never started.
            "cl_dir",
            # The rest of efsrv.mmh, completing the real TU set: ESHELL is a
            # heavy efsrv client (CFileMan in CShell::RunL, CDirScan, TFileText,
            # RRawDisk, ...).
            "cl_fman", "cl_fraw", "cl_ftext", "cl_scan", "cl_insecure",
            "cl_plugin", "cl_blockmap", "cl_debug", "cl_shim", "cl_notification",
            # cl_main: Panic(TClientPanic)/Fault -- the value classes' panic leaf
            "cl_main")]
# C++ runtime the value classes reach at RUNTIME (CDir::NewL -> operator new,
# vtables -> __cxa_pure_virtual): REAL gcchelp.cpp + the efile_exe crt shim,
# statically linked exactly as in EFILE.EXE (see build_efile_exe.py).
RUNTIME_SOURCES = [
    os.path.join(REPO, "src", "oss.FCL.sf.os.kernelhwsrv", "kernel", "eka",
                 "common", "gcchelp.cpp"),
    os.path.join(REPO, "toolchain", "efile_exe", "crt_shim.cpp"),
]

# The same include/define shims kernel/build_efile.sh uses for the
# fileserver component (see that script's header for the rationale of each).
EXTRA = [
    "-DSYMBIAN_ENABLE_64_BIT_FILE_SERVER_API", "-fno-access-control",
    "-fno-function-sections", "-fno-data-sections",
    "-iquote", os.path.join(FS, "inc"), "-iquote", os.path.join(FS, "sfile"),
    "-iquote", os.path.join(FS, "sfsrv"), "-iquote", os.path.join(FS, "ftrace"),
    f"-I{os.path.join(FS, 'sfsrv')}", f"-I{os.path.join(FS, 'sfile')}",
    f"-I{os.path.join(FS, 'inc')}", f"-I{os.path.join(FS, 'ftrace')}",
    f"-I{os.path.join(FS, 'sfat32')}",
    f"-I{os.path.join(SRC, 'kernel', 'eka', 'include')}",
    f"-I{os.path.join(SRC, 'halservices', 'hal', 'inc')}",
]


def compile_objs(workdir: str) -> list[str]:
    import subprocess
    env = dict(os.environ, SYMCC_ARM="1")
    objs = []
    for src in SOURCES:
        obj = os.path.join(workdir, "efsrv_" + os.path.basename(src) + ".o")
        # -fkeep-inline-functions: emit out-of-line copies of the inline session
        # helpers the client TUs call (notably RSubSessionBase::CreateSubSession,
        # a thin inline over the exported DoCreateSubSession). Without it those
        # inlines are neither inlined at every site nor defined, so the efsrv-
        # standalone link leaves them undefined and stubs them -> a client (estart)
        # calling RFile::Open hits the loud unimpl stub. In the EFILE build the same
        # inline is emitted by sf_plugin_shim.cpp; efsrv has no such TU.
        subprocess.run(["bash", SYMCC, src, "-O2", "-fkeep-inline-functions",
                        *EXTRA, "-o", obj],
                       check=True, env=env, cwd=REPO)
        objs.append(obj)
    # runtime TUs compile WITHOUT the fileserver -iquote set (gcchelp.cpp's
    # #include "common.h" must resolve to kernel/eka/common's, not sfile's).
    for src in RUNTIME_SOURCES:
        obj = os.path.join(workdir, "efsrv_" + os.path.basename(src) + ".o")
        subprocess.run(["bash", SYMCC, src, "-O2", "-o", obj],
                       check=True, env=env, cwd=REPO)
        objs.append(obj)
    return objs


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "efsrv_dev.dll")
    workdir = BE.workdir("efsrv")

    defmap = parse_def(DEF_PATH)
    def_names = set(defmap.by_ordinal.values())
    objs = compile_objs(workdir)
    defined = BE._nm("--defined-only", *objs)
    exportable = sorted(def_names & defined)

    # A stub TU: the unimplemented-export placeholder (STUB_NAME) every ordinal we
    # don't build from source points to. Reuse the euser DLL's unimpl_export.cpp.
    import subprocess
    unimpl = os.path.join(BE.ED, "unimpl_export.cpp")
    unimpl_o = os.path.join(workdir, "efsrv_unimpl.o")
    subprocess.run(["bash", SYMCC, unimpl, "-O2", "-o", unimpl_o],
                   check=True, env=dict(os.environ, SYMCC_ARM="1"), cwd=REPO)
    objs.append(unimpl_o)

    roots = exportable + [STUB_NAME]
    # Pass 1: tolerate unresolved to enumerate the external surface. Names with
    # an ordinal provider elsewhere (euser) become REAL IMPORTS via veneers --
    # resolved by the loader / the XIP prelinker at ROM build -- so the
    # value-class methods work at runtime on the device. Anything else (own
    # unbuilt efsrv ordinals' deps) falls to the loud unimpl stub as before.
    elf1 = os.path.join(workdir, "efsrv0.pass1.elf")
    BE._link(objs, roots, elf1, workdir, ignore_unresolved=True)
    missing = sorted(BE._nm("--undefined-only", elf1) - {""})
    imports, stub_names = devlink.split_missing(missing, own_dll=devlink.EFSRV_DLL)
    veneer_o = devlink.make_veneers(workdir, imports, "efsrv")

    # GCC mangles a call to an inherited RSubSessionBase/RSessionBase method under
    # the DERIVED class it is accessed through (RFile::CreateSubSession,
    # RFs::SendReceive, ...), but -fkeep-inline-functions emits the out-of-line body
    # under the BASE class. The two are the same function -- the derived object's
    # base subobject is at offset 0 -- so alias the derived reference to the base
    # symbol (which the objects now define) instead of stubbing it. Without this a
    # client (estart) calling RFile::Open/Read hits the loud unimpl stub.
    # A base symbol is usable as an alias target if the objects define it OR efsrv
    # imports it from euser (the veneer defines it) -- e.g. RSubSessionBase::
    # CloseSubSession is euser ordinal 387, so RFile::Close -> CloseSubSession must
    # trampoline to that imported base, not fall to the loud unimpl stub.
    available = BE._nm("--defined-only", *objs) | set(imports)

    # Map a derived F32 handle class (mangled length+name token) to the base whose
    # inherited method the compiler actually meant. Sessions (RFs) derive from
    # RSessionBase; sub-sessions (RFile/RDir/RFormat/RRawDisk) from RSubSessionBase.
    # Single inheritance, base at offset 0, so the derived and base bodies are the
    # same code -> a plain symbol alias is correct.
    DERIVED_BASE = {
        "3RFs": "12RSessionBase",
        "5RFile": "15RSubSessionBase",
        "4RDir": "15RSubSessionBase",
        "7RFormat": "15RSubSessionBase",
        "8RRawDisk": "15RSubSessionBase",
    }

    def _base_alias(sym):
        # _ZN<n><Class><rest>  (non-const) or _ZNK<n><Class><rest> (const)
        for pfx in ("_ZNK", "_ZN"):
            if not sym.startswith(pfx):
                continue
            rest = sym[len(pfx):]
            for token, base in DERIVED_BASE.items():
                if rest.startswith(token):
                    cand = pfx + base + rest[len(token):]
                    if cand in available and cand != sym:
                        return cand
        return None

    aliases, still_stub = {}, []
    for name in stub_names:
        base = _base_alias(name)
        if base:
            aliases[name] = base
        else:
            still_stub.append(name)
    stub_names = still_stub

    stub_s = os.path.join(workdir, "efsrv_unimpl_stubs.s")
    with open(stub_s, "w") as fh:
        fh.write("\t.text\n\t.arm\n")
        for name in stub_names:
            fh.write(f"\t.globl {name}\n\t.type {name},%function\n"
                     f"{name}:\n\tb {STUB_NAME}\n")
        # A real branch trampoline (not `.set`): a `.set` alias to a weak inline
        # body does not create a section reference, so --gc-sections drops the
        # target and the alias goes undefined. `b base` is a genuine relocation
        # that both defines the derived symbol and keeps the base body alive.
        for derived, base in aliases.items():
            fh.write(f"\t.globl {derived}\n\t.type {derived},%function\n"
                     f"{derived}:\n\tb {base}\n")
    if aliases:
        print(f"efsrv: aliased {len(aliases)} inherited-method refs to their base "
              f"class (e.g. {next(iter(aliases))} -> {aliases[next(iter(aliases))]})")
    stub_o = os.path.join(workdir, "efsrv_unimpl_stubs.s.o")
    subprocess.run([BE.GCC, "-march=armv5te", "-marm", "-c", stub_s, "-o", stub_o],
                   check=True)

    # Root the alias TARGETS: they are weak out-of-line inline bodies referenced
    # only through `.set` aliases, which do not create a section reference, so
    # --gc-sections would otherwise drop them and leave the alias undefined.
    roots = roots + sorted(set(aliases.values()))

    elf2 = os.path.join(workdir, "efsrv0.elf")
    BE._link(objs + [veneer_o, stub_o], roots, elf2, workdir, ignore_unresolved=False)
    with open(elf2, "rb") as fh:
        elf_blob = fh.read()
    ext = extract(elf_blob)

    if ext.code_base != CODE_BASE:
        sys.exit(f"code_base mismatch: 0x{ext.code_base:X} != 0x{CODE_BASE:X}")
    if STUB_NAME not in ext.symbols:
        sys.exit(f"required symbol {STUB_NAME!r} not in ELF")

    exportable_set = set(exportable)
    stub_off = ext.symbols[STUB_NAME] - ext.code_base
    export_offsets = []
    real = 0
    for ordn in range(1, defmap.max_ordinal + 1):
        name = defmap.name(ordn)
        if name in exportable_set and name in ext.symbols:
            export_offsets.append(ext.symbols[name] - ext.code_base)
            real += 1
        else:
            export_offsets.append(stub_off)

    import_list = devlink.import_blocks(ext, imports)
    n_imp = sum(len(e) for _, e in import_list)
    print("DEVICE efsrv (F32 client value-classes, from source)")
    print(f"code: {len(ext.code)} B @0x{ext.code_base:X}   "
          f"data: {len(ext.data)} B   bss: {ext.bss_size} B")
    print(f"exports: {len(export_offsets)} ordinals  "
          f"({real}/{defmap.max_ordinal} real from source, "
          f"{defmap.max_ordinal - real} -> unimpl stub); "
          f"{n_imp} imports ({', '.join(f'{d} {len(e)}' for d, e in import_list)}); "
          f"{len(stub_names)} link stubs")

    img = build_dll(ext.code, export_offsets, uid3=UID3, imports=import_list,
                    code_relocs=ext.code_relocs, code_base=ext.code_base,
                    data=ext.data, data_base=ext.data_base, bss_size=ext.bss_size,
                    data_relocs=ext.data_relocs,
                    code_reloc_types=ext.code_reloc_types,
                    data_reloc_types=ext.data_reloc_types)
    with open(out_path, "wb") as fh:
        fh.write(img)
    print(f"\nWrote {out_path} ({len(img)} bytes)")
    result = check_image(img)
    print(format_result(result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
