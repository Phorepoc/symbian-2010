"""build_elocal_fsy -- the from-source ELOCAL.FSY (FAT16 file system plugin).

The syborg estart.txt maps C: (nvmem) and D: (iram) as `ELOCAL  FAT`, so after
StartupInitComplete estart asks the file server to AddFileSystem("ELOCAL"):
EFILE loads ELOCAL.FSY through the loader, Lookup(1) -> CreateFileSystem().
This builds that plugin from the unmodified 2010 sfat sources (the elocal.mmp
TU set -- FAT16; the sfat32 flavour can follow the same recipe as efat32.fsy).

A .FSY is a plain DLL whose ONE export (ordinal 1) is
`EXPORT_C CFileSystem* CreateFileSystem()`; there is no .def file. It links
against efile.lib -- i.e. it IMPORTS the CFileSystem/CMountCB/CFileCB plugin
framework from EFILE.EXE's efileu.def export surface (devlink grew the
efile.exe provider for exactly this build) -- plus efsrv/euser/hal as usual.
All imports resolve at ROM build (tools/rombuild.py --xip prelink; efile.exe
carries its export table in the XIP header).

UIDs: uid1 DLL, uid2 0x100039DF (file system plugin -- the loader REQ-UID
checks it), uid3 0x100000D6 (elocal.mmp `UID 0 0x100000d6`).

Run from repo root:  python3 emitter/build_elocal_fsy.py [out.fsy]
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
CODE_BASE = BE.CODE_BASE
SYMCC = BE.SYMCC
STUB_NAME = BE.STUB_NAME

SRC = os.path.join(REPO, "src", "oss.FCL.sf.os.kernelhwsrv")
FS = os.path.join(SRC, "userlibandfileserver", "fileserver")
SFAT = os.path.join(FS, "sfat")

UID2_FSY = 0x100039DF
UID3 = 0x100000D6

# The elocal.mmp (ARM branch) TU set, verbatim.
SOURCES = [os.path.join(SFAT, f + ".cpp") for f in
           ("fat_config", "sl_cache", "sl_dir",
            "sl_disk", "sl_drv", "sl_file",
            "sl_fmt", "sl_fsy", "sl_main", "sl_mnt",
            "sl_utl", "sl_vfat", "sl_bpb",
            "sl_scan", "sl_check",
            "sl_fat16", "sl_mnt16", "sl_fatcache", "fat_table", "ram_fat_table",
            "sl_leafdir_cache",
            "sl_dir_cache")]
# Same statically-linked C++ runtime as the efsrv/efile builds.
RUNTIME_SOURCES = [
    os.path.join(REPO, "src", "oss.FCL.sf.os.kernelhwsrv", "kernel", "eka",
                 "common", "gcchelp.cpp"),
    os.path.join(REPO, "toolchain", "efile_exe", "crt_shim.cpp"),
]

EXTRA = [
    "-DSYMBIAN_ENABLE_64_BIT_FILE_SERVER_API", "-fno-access-control",
    "-fno-function-sections", "-fno-data-sections",
    "-iquote", os.path.join(SFAT), "-iquote", os.path.join(SFAT, "inc"),
    "-iquote", os.path.join(FS, "sfile"), "-iquote", os.path.join(FS, "inc"),
    f"-I{SFAT}", f"-I{os.path.join(SFAT, 'inc')}",
    f"-I{os.path.join(FS, 'sfile')}", f"-I{os.path.join(FS, 'inc')}",
    f"-I{os.path.join(FS, 'ftrace')}",
    f"-I{os.path.join(SRC, 'kernel', 'eka', 'include')}",
    f"-I{os.path.join(SRC, 'halservices', 'hal', 'inc')}",
]

CREATE_FS = "CreateFileSystem"   # unmangled: extern "C"-linkage in sl_main.cpp


def compile_objs(workdir: str) -> list[str]:
    env = dict(os.environ, SYMCC_ARM="1")
    objs = []
    for src in SOURCES:
        obj = os.path.join(workdir, "elocal_" + os.path.basename(src) + ".o")
        subprocess.run(["bash", SYMCC, src, "-O2", "-fkeep-inline-functions",
                        *EXTRA, "-o", obj],
                       check=True, env=env, cwd=REPO)
        objs.append(obj)
    for src in RUNTIME_SOURCES:
        obj = os.path.join(workdir, "elocal_" + os.path.basename(src) + ".o")
        subprocess.run(["bash", SYMCC, src, "-O2", "-o", obj],
                       check=True, env=env, cwd=REPO)
        objs.append(obj)
    return objs


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "elocal.fsy")
    workdir = BE.workdir("elocal")

    objs = compile_objs(workdir)

    unimpl = os.path.join(BE.ED, "unimpl_export.cpp")
    unimpl_o = os.path.join(workdir, "elocal_unimpl.o")
    subprocess.run(["bash", SYMCC, unimpl, "-O2", "-o", unimpl_o],
                   check=True, env=dict(os.environ, SYMCC_ARM="1"), cwd=REPO)
    objs.append(unimpl_o)

    roots = [CREATE_FS, STUB_NAME]
    elf1 = os.path.join(workdir, "elocal0.pass1.elf")
    BE._link(objs, roots, elf1, workdir, ignore_unresolved=True)
    missing = sorted(BE._nm("--undefined-only", elf1) - {""})
    imports, stub_names = devlink.split_missing(missing, own_dll="elocal.fsy",
                                                include_efile=True)
    veneer_o = devlink.make_veneers(workdir, imports, "elocal")

    stub_s = os.path.join(workdir, "elocal_unimpl_stubs.s")
    with open(stub_s, "w") as fh:
        fh.write("\t.text\n\t.arm\n")
        for name in stub_names:
            fh.write(f"\t.globl {name}\n\t.type {name},%function\n"
                     f"{name}:\n\tb {STUB_NAME}\n")
    stub_o = os.path.join(workdir, "elocal_unimpl_stubs.s.o")
    subprocess.run([BE.GCC, "-march=armv5te", "-marm", "-c", stub_s, "-o", stub_o],
                   check=True)
    if stub_names:
        print(f"elocal: {len(stub_names)} link stubs (unresolved -> loud unimpl):")
        for n in stub_names[:20]:
            print(f"  {n}")
        if len(stub_names) > 20:
            print(f"  ... +{len(stub_names) - 20} more")

    elf2 = os.path.join(workdir, "elocal0.elf")
    BE._link(objs + [veneer_o, stub_o], roots, elf2, workdir,
             ignore_unresolved=False)
    with open(elf2, "rb") as fh:
        elf_blob = fh.read()
    ext = extract(elf_blob)

    if ext.code_base != CODE_BASE:
        sys.exit(f"code_base mismatch: 0x{ext.code_base:X} != 0x{CODE_BASE:X}")
    if CREATE_FS not in ext.symbols:
        sys.exit(f"required export {CREATE_FS!r} not in ELF")
    if ext.data or ext.bss_size:
        # A Symbian DLL may not carry writable static data; the XIP prelinker
        # gives DLLs no data run. Fail loudly rather than ship silent garbage.
        sys.exit(f"elocal.fsy has data ({len(ext.data)} B) / bss ({ext.bss_size} B)"
                 " -- writable static data in a DLL, investigate")

    export_offsets = [ext.symbols[CREATE_FS] - ext.code_base]  # ordinal 1

    import_list = devlink.import_blocks(ext, imports)
    n_imp = sum(len(e) for _, e in import_list)
    print("DEVICE elocal.fsy (FAT16 file system plugin, from source)")
    print(f"code: {len(ext.code)} B @0x{ext.code_base:X}   "
          f"data: {len(ext.data)} B   bss: {ext.bss_size} B")
    print(f"exports: 1 (CreateFileSystem); {n_imp} imports "
          f"({', '.join(f'{d} {len(e)}' for d, e in import_list)}); "
          f"{len(stub_names)} link stubs")

    img = build_dll(ext.code, export_offsets, uid2=UID2_FSY, uid3=UID3,
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
