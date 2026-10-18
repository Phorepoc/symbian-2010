"""build_ekdata_dll -- the from-source EKDATA.DLL (keyboard map data).

EWSRV's CKeyTranslator loads EKDATA.DLL at runtime (ky_tran.cpp: default key
data; UID3 is REQUIRED to be KKeyboardDataUid, checked via Type()[2]) and
resolves its THREE data-table exports (eabi/ekdata.def):
    1  KeyDataConvTable(SConvTable&, TUint&, TUint&, SScanCodeBlockList&, SKeyCodeList&)
    2  KeyDataFuncTable(SFuncTables&)
    3  KeyDataSettings(TRadix&, TCtrlDigitsTermination&, TInt&, TInt&)

Built from the REAL syborg keymap (baseport/syborg/keymap/keymap.cpp,
keymap.mmp: MACRO __SECURE_API__), unmodified. UIDs: 0x1000008d / 0x100039e0.

Run from repo root:  python3 emitter/build_ekdata_dll.py [out.dll]
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
KEYMAP = os.path.join(REPO, "src", "oss.FCL.sf.adapt.Qemu",
                      "baseport", "syborg", "keymap", "keymap.cpp")

UID2_DLL = 0x1000008D
UID3 = 0x100039E0

EXTRA = [
    "-D__SECURE_API__",   # keymap.mmp MACRO
    "-fno-access-control", "-fno-function-sections", "-fno-data-sections",
    f"-I{os.path.join(SRC, 'kernel', 'eka', 'include')}",
]

# eabi/ekdata.def, in ordinal order.
EXPORTS = [
    "_Z16KeyDataConvTableR10SConvTableRjS1_R18SScanCodeBlockListR12SKeyCodeList",
    "_Z16KeyDataFuncTableR11SFuncTables",
    "_Z15KeyDataSettingsR6TRadixR22TCtrlDigitsTerminationRiS3_",
]


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "ekdata.dll")
    workdir = BE.workdir("ekdata")

    env = dict(os.environ, SYMCC_ARM="1")
    objs = []
    # unimpl_export FIRST: the loader's DEpocCodeSeg::Lookup treats an export
    # whose address equals the image entry point (0 here) as an absent-ordinal
    # hole and returns NULL. With keymap.o first, KeyDataSettings sat at code
    # offset 0 -> Lookup(3)==NULL -> EWSRV blx NULL (boot43). Keep offset 0
    # occupied by non-exported code.
    for src in [os.path.join(BE.ED, "unimpl_export.cpp"),
                KEYMAP,
                os.path.join(REPO, "src", "oss.FCL.sf.os.kernelhwsrv", "kernel", "eka",
                             "common", "gcchelp.cpp"),
                os.path.join(REPO, "toolchain", "efile_exe", "crt_shim.cpp")]:
        obj = os.path.join(workdir, "ekdata_" + os.path.basename(src) + ".o")
        subprocess.run(["bash", SYMCC, src, "-O2", *EXTRA, "-o", obj],
                       check=True, env=env, cwd=REPO)
        objs.append(obj)

    roots = EXPORTS + [STUB_NAME]
    elf1 = os.path.join(workdir, "ekdata0.pass1.elf")
    BE._link(objs, roots, elf1, workdir, ignore_unresolved=True)
    missing = sorted(BE._nm("--undefined-only", elf1) - {""})
    imports, stub_names = devlink.split_missing(missing, own_dll="ekdata.dll")
    veneer_o = devlink.make_veneers(workdir, imports, "ekdata")

    stub_s = os.path.join(workdir, "ekdata_unimpl_stubs.s")
    with open(stub_s, "w") as fh:
        fh.write("\t.text\n\t.arm\n")
        for name in stub_names:
            fh.write(f"\t.globl {name}\n\t.type {name},%function\n"
                     f"{name}:\n\tb {STUB_NAME}\n")
    stub_o = os.path.join(workdir, "ekdata_unimpl_stubs.s.o")
    subprocess.run([BE.GCC, "-march=armv5te", "-marm", "-c", stub_s, "-o", stub_o],
                   check=True)

    elf2 = os.path.join(workdir, "ekdata0.elf")
    BE._link(objs + [veneer_o, stub_o], roots, elf2, workdir,
             ignore_unresolved=False)
    with open(elf2, "rb") as fh:
        ext = extract(fh.read())

    for name in EXPORTS:
        if name not in ext.symbols:
            sys.exit(f"required export {name!r} not in ELF")
    if ext.data or ext.bss_size:
        sys.exit(f"ekdata.dll has data ({len(ext.data)} B) / bss ({ext.bss_size} B)"
                 " -- writable static data in a DLL, investigate")

    export_offsets = [ext.symbols[n] - ext.code_base for n in EXPORTS]

    import_list = devlink.import_blocks(ext, imports)
    n_imp = sum(len(e) for _, e in import_list)
    print("DEVICE ekdata.dll (syborg keymap data, from source)")
    print(f"code: {len(ext.code)} B @0x{ext.code_base:X}")
    print(f"exports: {len(EXPORTS)}; {n_imp} imports "
          f"({', '.join(f'{d} {len(e)}' for d, e in import_list)}); "
          f"{len(stub_names)} link stubs")

    img = build_dll(ext.code, export_offsets, uid2=UID2_DLL, uid3=UID3,
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
