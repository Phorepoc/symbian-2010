"""build_efile_exe -- emit EFILE.EXE as an E32Image from source.

The link surface is closed (489 imports, all with a from-source provider). This
script turns the compiled EFILE objects into an EXE-with-imports E32Image that resolves against
our from-source DLLs BY ORDINAL at load time:

    euser.dll  (emitter/build_euser_device.py)  -- 435 euseru.def ordinals
               + TTrap::Trap/UnTrap at the spare ordinals past euser_selftest
    efsrv.dll  (emitter/build_efsrv_device.py)  -- 39 F32 value-class ordinals
    hal.dll    (halu.def ordinal 1)             -- HAL::Get

Statically linked (not imports): libgcc (__aeabi_* arithmetic), REAL
common/gcchelp.cpp (operator new/delete over User::Alloc), and the two tiny
seam TUs in toolchain/efile_exe/ (modern-GCC RunThread + crt shim).

Mechanism per import: an ARM import veneer
        <name>:  ldr pc, [pc, #-4]
                 .word <ordinal>            (placed by build_dll's import loop)
The E32 import section names each veneer's WORD offset; at load the loader
reads the ordinal from the word and overwrites it with the resolved export
address, so the veneer becomes a one-instruction jump to the real function.
(EFILE has ZERO data imports -- verified against all three .def files -- so
veneers cover the entire surface.)

Prereq: bash kernel/build_efile.sh   (objects in .efile-build/)
Run from repo root:  python3 emitter/build_efile_exe.py [out.exe]
"""
from __future__ import annotations

import glob
import os
import struct
import subprocess
import sys

import build_euser_dll as BE
import devlink
from e32image import build_dll
from elf import extract
from euser_def import parse_def
from refcheck import check_image, format_result

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OBJ_DIR = os.path.join(REPO, ".efile-build")
LD_SCRIPT = os.path.join(HERE, "efile.ld")

EFILE_DEF = os.path.join(devlink.FS, "eabi", "efileu.def")

# efile.mmh: TARGETTYPE exexp, UID2/3, EPOCSTACKSIZE, EPOCHEAPSIZE.
UID2, UID3 = 0x1000008C, 0x100039E3
STACK_SIZE, HEAP_MIN, HEAP_MAX = 0x6000, 0x1000, 0x800000

ENTRY_NAME = "_E32Startup"
STUB_NAME = BE.STUB_NAME
EE = os.path.join(REPO, "toolchain", "efile_exe")

# TUs compiled here on top of the .efile-build objects:
#   uc_exe.cia         REAL exexp entry point (_E32Startup; loader writes the
#                      codeseg unique ID at entry+12)
#   dev_exe_startup    RunThread for modern GCC (see that file)
#   gcchelp.cpp        REAL operator new/delete over User::Alloc
#   crt_shim.cpp       __cxa_pure_virtual / __aeabi_atexit / sized delete
#   unimpl_export.cpp  the loud stub behind efileu.def ordinals not linked in
EXTRA_SOURCES = [
    os.path.join(BE.B, "euser", "epoc", "arm", "uc_exe.cia"),
    os.path.join(EE, "dev_exe_startup.cpp"),
    os.path.join(BE.B, "common", "gcchelp.cpp"),
    os.path.join(EE, "crt_shim.cpp"),
    os.path.join(BE.ED, "unimpl_export.cpp"),
]
# Our TUs that need the fileserver's private headers (the build_efile.sh include set), and the --wrap
# each one implements:
#   ldr_builtin_devices.cpp  LoadLogicalDevice of a driver built into ekern = KErrAlreadyExists, not KErrNotFound
FS_DIR = os.path.join(REPO, "src", "oss.FCL.sf.os.kernelhwsrv", "userlibandfileserver", "fileserver")
FS_SOURCES = [os.path.join(EE, "ldr_builtin_devices.cpp")]
FS_FLAGS = ["-DSYMBIAN_ENABLE_64_BIT_FILE_SERVER_API", "-DSYMBIAN_DISTINCT_LOCALE_MODEL", "-fno-access-control",
            *[a for d in ("inc", "sfile", "sfsrv", "ftrace") for a in ("-iquote", os.path.join(FS_DIR, d))],
            *["-I" + os.path.join(FS_DIR, d) for d in ("sfsrv", "sfile", "inc", "ftrace", "sfat32")]]
WRAPS = ["_ZN8E32Image11LoadCodeSegERK7RLdrReq"]


def _compile_extras(workdir: str) -> list[str]:
    env = dict(os.environ, SYMCC_ARM="1")
    objs = []
    for src in EXTRA_SOURCES:
        obj = os.path.join(workdir, "efileexe_" + os.path.basename(src) + ".o")
        subprocess.run(["bash", BE.SYMCC, src, "-O2", "-DSYMBIAN_DISTINCT_LOCALE_MODEL", "-o", obj],
                       check=True, env=env, cwd=REPO)
        objs.append(obj)
    for src in FS_SOURCES:
        obj = os.path.join(workdir, "efileexe_" + os.path.basename(src) + ".o")
        subprocess.run(["bash", BE.SYMCC, src, "-O2", *FS_FLAGS, "-o", obj], check=True, env=env, cwd=REPO)
        objs.append(obj)
    return objs


def _link(objs: list[str], roots: list[str], out_elf: str, workdir: str,
          ignore_unresolved: bool) -> None:
    """BE._link with the EXE linker script (entry + .init_array bracket)."""
    rsp = os.path.join(workdir, "efile_roots.rsp")
    with open(rsp, "w") as fh:
        fh.write("\n".join(f"-Wl,-u,{n}" for n in roots))
    args = [BE.GCC, "-nostdlib", "-Wl,-q", "-Wl,--gc-sections",
            "-Wl,--no-demangle", f"@{rsp}", f"-Wl,-T,{LD_SCRIPT}", *[f"-Wl,--wrap={w}" for w in WRAPS],
            "-march=armv5te", "-marm", *objs, BE._libgcc(), "-o", out_elf]
    if ignore_unresolved:
        args.insert(1, "-Wl,--unresolved-symbols=ignore-all")
    subprocess.run(args, check=True)


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "efile.exe")
    workdir = BE.workdir("efile")

    objs = sorted(glob.glob(os.path.join(OBJ_DIR, "*.cpp.o"))
                  + glob.glob(os.path.join(OBJ_DIR, "*.cia.o")))
    if len(objs) < 48:
        sys.exit(f"only {len(objs)} objects in {OBJ_DIR} -- "
                 "run: bash kernel/build_efile.sh")
    objs += _compile_extras(workdir)

    efile_def = parse_def(EFILE_DEF)
    def_names = set(efile_def.by_ordinal.values())
    defined = BE._nm("--defined-only", *objs)
    exportable = sorted(def_names & defined)
    roots = exportable + [ENTRY_NAME, STUB_NAME]

    # Pass 1: enumerate what the rooted closure imports.
    elf1 = os.path.join(workdir, "efile.pass1.elf")
    _link(objs, roots, elf1, workdir, ignore_unresolved=True)
    undefined = sorted(BE._nm("--undefined-only", elf1) - {""})

    imports, unmapped = devlink.split_missing(undefined, own_dll="efile.exe")
    if unmapped:
        sys.exit("imports with NO ordinal provider (link surface regression?):\n  "
                 + "\n  ".join(unmapped))

    # Pass 2: clean link with the import veneers in place.
    veneer_obj = devlink.make_veneers(workdir, imports, "efile")
    elf2 = os.path.join(workdir, "efile.elf")
    _link(objs + [veneer_obj], roots, elf2, workdir, ignore_unresolved=False)
    with open(elf2, "rb") as fh:
        ext = extract(fh.read())

    for name in (ENTRY_NAME, STUB_NAME):
        if name not in ext.symbols:
            sys.exit(f"required symbol {name!r} not in ELF")

    # Import blocks: the veneer's WORD (entry+4) offset, grouped per DLL.
    import_list = devlink.import_blocks(ext, imports)

    # Export table (efileu.def): real where linked, else the loud unimpl stub.
    stub_off = ext.symbols[STUB_NAME] - ext.code_base
    export_offsets, real = [], 0
    exportable_set = set(exportable)
    for ordn in range(1, efile_def.max_ordinal + 1):
        name = efile_def.name(ordn)
        if name in exportable_set and name in ext.symbols:
            export_offsets.append(ext.symbols[name] - ext.code_base)
            real += 1
        else:
            export_offsets.append(stub_off)

    n_imp = sum(len(e) for _, e in import_list)
    print("EFILE.EXE (from-source F32 file server, EXE-with-imports E32Image)")
    print(f"code: {len(ext.code)} B @0x{ext.code_base:X}   "
          f"data: {len(ext.data)} B   bss: {ext.bss_size} B")
    print(f"entry: {ENTRY_NAME} @ code+0x{ext.symbols[ENTRY_NAME] - ext.code_base:X}")
    print(f"imports: {n_imp} across {len(import_list)} DLLs  ("
          + ", ".join(f"{dll} {len(e)}" for dll, e in import_list) + ")")
    print(f"exports: {len(export_offsets)} ordinals ({real} real, "
          f"{efile_def.max_ordinal - real} -> unimpl stub)")
    print(f"code relocs: {len(ext.code_relocs)}   data relocs: {len(ext.data_relocs)}")

    img = build_dll(ext.code, export_offsets, exe=True, uid2=UID2, uid3=UID3,
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
