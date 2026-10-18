"""build_estart_exe -- emit the REAL ESTART.EXE as an E32Image.

Compiles the genuine 2010 estart, unmodified:
    fileserver/estart/estart.cpp        (TFSStartup: drive mapping via
                                         Z:\\SYS\\DATA\\ESTART.TXT, FSY mounting,
                                         format, StartSystem)
    baseport/syborg/estart/estartmain.cpp (TSyborgFSStartup: adds InitHostFS ->
                                         spawns SVPHOSTFS.EXE, absent -> skipped)
with -DSYMBIAN_EXCLUDE_ESTART_DOMAIN_MANAGER (a supported configuration: drops the
domaincli dependency).

Emission mirrors build_efile_exe.py: EXE-with-imports resolving by ordinal
against euser.dll / efsrv.dll / hal.dll (estart is a genuine efsrv CLIENT --
RFs/RFile session API -- unlike EFILE which compiles the client TUs into
itself). Statically linked: uc_exe.cia entry, dev_exe_startup, gcchelp,
crt_shim. TARGETTYPE exe: no exports.

Run from repo root:  python3 emitter/build_estart_exe.py [out.exe]
"""
from __future__ import annotations

import os
import subprocess
import sys

import build_euser_dll as BE
import devlink
from e32image import build_dll
from elf import extract
from refcheck import check_image, format_result

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LD_SCRIPT = os.path.join(HERE, "efile.ld")

SRC = os.path.join(REPO, "src", "oss.FCL.sf.os.kernelhwsrv")
FS = os.path.join(SRC, "userlibandfileserver", "fileserver")
QB = os.path.join(REPO, "src", "oss.FCL.sf.adapt.Qemu",
                  "baseport", "syborg")

# estart.mmp: TARGETTYPE exe, UID 0 0x10272C04, no explicit stack/heap (defaults).
UID2, UID3 = 0, 0x10272C04
STACK_SIZE, HEAP_MIN, HEAP_MAX = 0x2000, 0x1000, 0x100000

ENTRY_NAME = "_E32Startup"
EE = os.path.join(REPO, "toolchain", "efile_exe")

ESTART_SOURCES = [
    os.path.join(FS, "estart", "estart.cpp"),
    os.path.join(QB, "estart", "estartmain.cpp"),
]
RUNTIME_SOURCES = [
    os.path.join(BE.B, "euser", "epoc", "arm", "uc_exe.cia"),
    os.path.join(EE, "dev_exe_startup.cpp"),
    os.path.join(BE.B, "common", "gcchelp.cpp"),
    os.path.join(EE, "crt_shim.cpp"),
]

ESTART_FLAGS = [
    "-DSYMBIAN_ENABLE_64_BIT_FILE_SERVER_API",
    "-DSYMBIAN_EXCLUDE_ESTART_DOMAIN_MANAGER",
    "-fno-access-control", "-fno-function-sections", "-fno-data-sections",
    "-iquote", os.path.join(FS, "inc"), "-iquote", os.path.join(FS, "estart"),
    f"-I{os.path.join(FS, 'estart')}", f"-I{os.path.join(FS, 'sfsrv')}",
    f"-I{os.path.join(FS, 'sfile')}", f"-I{os.path.join(FS, 'inc')}",
    f"-I{os.path.join(FS, 'ftrace')}", f"-I{os.path.join(FS, 'sfat32')}",
    f"-I{os.path.join(SRC, 'kernel', 'eka', 'include')}",
    f"-I{os.path.join(SRC, 'halservices', 'hal', 'inc')}",
]


def _compile(workdir: str) -> list[str]:
    env = dict(os.environ, SYMCC_ARM="1")
    objs = []
    for src in ESTART_SOURCES:
        obj = os.path.join(workdir, "estart_" + os.path.basename(src) + ".o")
        subprocess.run(["bash", BE.SYMCC, src, "-O2", *ESTART_FLAGS,
                        "-o", obj], check=True, env=env, cwd=REPO)
        objs.append(obj)
    for src in RUNTIME_SOURCES:
        obj = os.path.join(workdir, "estartrt_" + os.path.basename(src) + ".o")
        subprocess.run(["bash", BE.SYMCC, src, "-O2", "-o", obj],
                       check=True, env=env, cwd=REPO)
        objs.append(obj)
    return objs


def _link(objs: list[str], out_elf: str, workdir: str, ignore_unresolved: bool) -> None:
    args = [BE.GCC, "-nostdlib", "-Wl,-q", "-Wl,--gc-sections",
            "-Wl,--no-demangle", f"-Wl,-u,{ENTRY_NAME}", f"-Wl,-T,{LD_SCRIPT}",
            "-march=armv5te", "-marm", *objs, BE._libgcc(), "-o", out_elf]
    if ignore_unresolved:
        args.insert(1, "-Wl,--unresolved-symbols=ignore-all")
    subprocess.run(args, check=True)


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "estart.exe")
    workdir = BE.workdir("estart")

    objs = _compile(workdir)

    elf1 = os.path.join(workdir, "estart.pass1.elf")
    _link(objs, elf1, workdir, ignore_unresolved=True)
    undefined = sorted(BE._nm("--undefined-only", elf1) - {""})

    imports, unmapped = devlink.split_missing(undefined, own_dll="estart.exe")
    if unmapped:
        sys.exit("imports with NO ordinal provider:\n  " + "\n  ".join(unmapped))

    veneer_obj = devlink.make_veneers(workdir, imports, "estart")
    elf2 = os.path.join(workdir, "estart.elf")
    _link(objs + [veneer_obj], elf2, workdir, ignore_unresolved=False)
    with open(elf2, "rb") as fh:
        ext = extract(fh.read())

    if ENTRY_NAME not in ext.symbols:
        sys.exit(f"required symbol {ENTRY_NAME!r} not in ELF")

    import_list = devlink.import_blocks(ext, imports)
    n_imp = sum(len(e) for _, e in import_list)
    print("ESTART.EXE (from-source TFSStartup/TSyborgFSStartup, EXE-with-imports)")
    print(f"code: {len(ext.code)} B @0x{ext.code_base:X}   "
          f"data: {len(ext.data)} B   bss: {ext.bss_size} B")
    print(f"imports: {n_imp} across {len(import_list)} DLLs  ("
          + ", ".join(f"{dll} {len(e)}" for dll, e in import_list) + ")")

    img = build_dll(ext.code, [], exe=True, uid2=UID2, uid3=UID3,
                    entry_point=ext.symbols[ENTRY_NAME] - ext.code_base,
                    stack_size=STACK_SIZE, heap_min=HEAP_MIN, heap_max=HEAP_MAX,
                    imports=import_list,
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
