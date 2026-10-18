"""build_hal_device -- the from-source HAL.DLL (user-side HAL client).

The third device DLL after euser/efsrv. EFILE imports `HAL::Get` (ord 1);
sf_cache_man/sf_ldr/sf_utl ask for EMemoryPageSize/ECPUSpeed/EPowerGood.

Built exactly as the real syborg hal.dll (`baseport/syborg/hal/hal.mmp`):
  * REAL `halservices/hal/src/hal_main.cpp` + `userhal.cpp` (unmodified)
  * the two GENERATED tables, produced by the REAL generator on the REAL syborg
    config:  perl halcfg.pl     hal_data.h values.hda -> values.cpp
             perl halcfg.pl -x  hal_data.h config.hcf -> config.cpp
Exports = halu.def (ord 1 = HAL::Get(TAttribute,TInt&)); euser references are
REAL IMPORTS via devlink veneers (resolved at load / XIP-prelink time).

Run from repo root:  python3 emitter/build_hal_device.py [out.dll]
"""
from __future__ import annotations

import os
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

HALSRC = os.path.join(devlink.SRC, "halservices", "hal")
SYBHAL = os.path.join(REPO, "src", "oss.FCL.sf.adapt.Qemu",
                      "baseport", "syborg", "hal")
UID3 = 0x100039E8   # hal.mmp UID
STUB_NAME = BE.STUB_NAME


def _generate(workdir: str) -> list[str]:
    """halcfg.pl on the syborg config -> values.cpp + config.cpp (real tables)."""
    halcfg = os.path.join(HALSRC, "halcfg.pl")
    hal_data = os.path.join(HALSRC, "inc", "hal_data.h")
    values_cpp = os.path.join(workdir, "hal_values.cpp")
    config_cpp = os.path.join(workdir, "hal_config.cpp")
    def _perl(*args):
        subprocess.run(["perl", *args], check=True)
    _perl(halcfg, hal_data, os.path.join(SYBHAL, "values.hda"), values_cpp)
    _perl(halcfg, "-x", hal_data, os.path.join(SYBHAL, "config.hcf"), config_cpp)
    return [values_cpp, config_cpp]


def compile_objs(workdir: str) -> list[str]:
    sources = [os.path.join(HALSRC, "src", "hal_main.cpp"),
               os.path.join(HALSRC, "src", "userhal.cpp"),
               *_generate(workdir),
               os.path.join(BE.ED, "unimpl_export.cpp")]
    extra = [f"-I{os.path.join(HALSRC, 'inc')}",
             f"-I{os.path.join(REPO, 'toolchain', 'kinc')}"]
    env = dict(os.environ, SYMCC_ARM="1")
    objs = []
    for src in sources:
        obj = os.path.join(workdir, "hal_" + os.path.basename(src) + ".o")
        subprocess.run(["bash", BE.SYMCC, src, "-O2", *extra, "-o", obj],
                       check=True, env=env, cwd=REPO)
        objs.append(obj)
    return objs


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "hal_dev.dll")
    workdir = BE.workdir("hal")

    defmap = parse_def(devlink.HAL_DEF)
    def_names = set(defmap.by_ordinal.values())
    objs = compile_objs(workdir)
    defined = BE._nm("--defined-only", *objs)
    exportable = sorted(def_names & defined)
    roots = exportable + [STUB_NAME]

    elf1 = os.path.join(workdir, "hal0.pass1.elf")
    BE._link(objs, roots, elf1, workdir, ignore_unresolved=True)
    missing = sorted(BE._nm("--undefined-only", elf1) - {""})
    imports, stub_names = devlink.split_missing(missing, own_dll=devlink.HAL_DLL)
    veneer_o = devlink.make_veneers(workdir, imports, "hal")

    stub_s = os.path.join(workdir, "hal_unimpl_stubs.s")
    with open(stub_s, "w") as fh:
        fh.write("\t.text\n\t.arm\n")
        for name in stub_names:
            fh.write(f"\t.globl {name}\n\t.type {name},%function\n"
                     f"{name}:\n\tb {STUB_NAME}\n")
    stub_o = os.path.join(workdir, "hal_unimpl_stubs.s.o")
    subprocess.run([BE.GCC, "-march=armv5te", "-marm", "-c", stub_s, "-o", stub_o],
                   check=True)

    elf2 = os.path.join(workdir, "hal0.elf")
    BE._link(objs + [veneer_o, stub_o], roots, elf2, workdir, ignore_unresolved=False)
    with open(elf2, "rb") as fh:
        ext = extract(fh.read())
    if STUB_NAME not in ext.symbols:
        sys.exit(f"required symbol {STUB_NAME!r} not in ELF")

    exportable_set = set(exportable)
    stub_off = ext.symbols[STUB_NAME] - ext.code_base
    export_offsets, real = [], 0
    for ordn in range(1, defmap.max_ordinal + 1):
        name = defmap.name(ordn)
        if name in exportable_set and name in ext.symbols:
            export_offsets.append(ext.symbols[name] - ext.code_base)
            real += 1
        else:
            export_offsets.append(stub_off)

    import_list = devlink.import_blocks(ext, imports)
    n_imp = sum(len(e) for _, e in import_list)
    print("DEVICE hal (real hal_main/userhal + halcfg.pl syborg tables)")
    print(f"code: {len(ext.code)} B @0x{ext.code_base:X}   "
          f"data: {len(ext.data)} B   bss: {ext.bss_size} B")
    print(f"exports: {len(export_offsets)} ordinals  "
          f"({real}/{defmap.max_ordinal} real from source, "
          f"{defmap.max_ordinal - real} -> unimpl stub); "
          f"{n_imp} imports; {len(stub_names)} link stubs")

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
