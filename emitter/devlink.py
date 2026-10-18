"""devlink -- shared import-link machinery for the images that run on the from-source kernel.

Used by build_efile_exe.py / build_efsrv_device.py / build_hal_device.py: an
image that runs on OUR kernel resolves its external references BY ORDINAL
against the from-source DLL set at load/ROM-build time. The pieces:

  * ordinal_maps() -- symbol name -> (dll_name, ordinal) over euseru.def (+ the
    TTrap spare ordinals from build_euser_device.EXTRA_EXPORTS), efsrvu.def and
    halu.def. euser wins overlaps (matches the gate math).
  * make_veneers() -- one ARM veneer `ldr pc,[pc,#-4]; .word 0` per imported
    symbol, each in its own section (--gc-sections keeps the referenced ones).
    The E32 import block names the veneer's WORD offset; the loader (or the XIP
    prelinker in tools/rombuild.py) overwrites the word with the resolved
    export address. Verified: EFILE + efsrv + hal have ZERO data imports, so
    veneers cover the whole device surface.
  * import_blocks() -- group resolved veneers into build_dll's `imports` form.
"""
from __future__ import annotations

import os
import subprocess

import build_euser_dll as BE
import build_euser_device as BD
from euser_def import parse_def

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

SRC = os.path.join(REPO, "src", "oss.FCL.sf.os.kernelhwsrv")
FS = os.path.join(SRC, "userlibandfileserver", "fileserver")
EFSRV_DEF = os.path.join(FS, "eabi", "efsrvu.def")
HAL_DEF = os.path.join(SRC, "halservices", "hal", "eabi", "halu.def")

EUSER_DLL, EFSRV_DLL, HAL_DLL = "euser.dll", "efsrv.dll", "hal.dll"
# The file server EXE also EXPORTS (efileu.def): the CFileSystem/CMountCB/...
# plugin framework a .FSY links against ("LIBRARY efile.lib" in the mmp).
# Only the FSY builds opt in (include_efile=True) -- efsrv/euser/hal must not
# accidentally grow efile imports.
EFILE_EXE = "efile.exe"
EFILE_DEF = os.path.join(FS, "eabi", "efileu.def")


def ordinal_maps(include_efile: bool = False) -> dict[str, tuple[str, int]]:
    """name -> (dll, ordinal), euser first (matches the gate math)."""
    euser = parse_def(BE.DEF_PATH)
    efsrv = parse_def(EFSRV_DEF)
    hal = parse_def(HAL_DEF)
    resolve: dict[str, tuple[str, int]] = {}
    if include_efile:
        efile = parse_def(EFILE_DEF)
        for ordn, name in efile.by_ordinal.items():
            resolve[name] = (EFILE_EXE, ordn)
    for ordn, name in hal.by_ordinal.items():
        resolve[name] = (HAL_DLL, ordn)
    for ordn, name in efsrv.by_ordinal.items():
        resolve[name] = (EFSRV_DLL, ordn)
    for ordn, name in euser.by_ordinal.items():
        resolve[name] = (EUSER_DLL, ordn)           # euser wins overlaps
    for name in BD.EXTRA_EXPORTS:                   # TTrap spare ordinals
        resolve[name] = (EUSER_DLL,
                         BD.extra_export_ordinal(name, euser.max_ordinal))
    return resolve


# Offset of the import WORD inside one veneer (see make_veneers).
VENEER_WORD_OFFSET = 4


def make_veneers(workdir: str, names, tag: str) -> str:
    """One `ldr pc,[pc,#-4]; .word 0` veneer per name, own section each."""
    src = os.path.join(workdir, f"{tag}_import_veneers.s")
    with open(src, "w") as fh:
        fh.write("\t.arm\n")
        for name in sorted(names):
            fh.write(f"\t.section .text.imp.{name},\"ax\",%progbits\n"
                     f"\t.globl {name}\n\t.type {name},%function\n"
                     f"{name}:\n\tldr pc, [pc, #-4]\n\t.word 0\n"
                     f"\t.size {name}, 8\n")
    obj = src + ".o"
    subprocess.run([BE.GCC, "-march=armv5te", "-marm", "-c", src, "-o", obj],
                   check=True)
    return obj


def import_blocks(ext, imports: dict[str, tuple[str, int]],
                  dll_order=(EUSER_DLL, EFSRV_DLL, HAL_DLL, EFILE_EXE)):
    """build_dll `imports` list from resolved veneers: the veneer's WORD offset
    (entry+4) per import, grouped per DLL. Veneers gc'd out are skipped."""
    blocks: dict[str, list[tuple[int, int, int]]] = {}
    for name, (dll, ordn) in imports.items():
        if name not in ext.symbols:
            continue
        word_off = ext.symbols[name] + VENEER_WORD_OFFSET - ext.code_base
        blocks.setdefault(dll, []).append((word_off, ordn, 0))
    return [(dll, sorted(blocks[dll])) for dll in dll_order if dll in blocks]


def split_missing(missing, own_dll: str, include_efile: bool = False):
    """Partition a pass-1 undefined set into (imports_map, unresolved):
    names with an ordinal provider in ANOTHER dll become imports; a name whose
    provider is the image itself (own_dll) or that has no provider stays out
    (caller stubs or fails on it)."""
    resolve = ordinal_maps(include_efile)
    imports: dict[str, tuple[str, int]] = {}
    rest = []
    for name in missing:
        tgt = resolve.get(name)
        if tgt and tgt[0] != own_dll:
            imports[name] = tgt
        else:
            rest.append(name)
    return imports, rest
