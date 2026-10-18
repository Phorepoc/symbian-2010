"""build_euser_dll -- shared helpers for building the user-side OS images (euser, efsrv, hal, efile ...) from the
unmodified 2010 source with the modern toolchain: compile (toolchain/symcc), link (--gc-sections from the .def
exports), unimplemented-export stubs, the "fill-in" collision rule. build_euser_device.py is the euser image itself;
the other build_*.py scripts reuse these helpers.
"""
from __future__ import annotations

import os
import subprocess
import sys

from euser_def import parse_def

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CODE_BASE = 0x8000
SYMCC = os.path.join(REPO, "toolchain", "symcc")
GCC = "arm-none-eabi-gcc"
NM = "arm-none-eabi-nm"
OBJCOPY = "arm-none-eabi-objcopy"

# uc_realx.cia exports the GCC-ABI soft-float helpers (__muldf3, __adddf3, the sf3
# float versions, the int/float converts and df2/sf2 compares) alongside its TRealX
# class methods. Those helpers are BROKEN under our toolchain (they raise a spurious
# __math_exception and return wrong bits — e.g. 1.5*1.5 != 2.25), while the TRealX
# methods (which the %f formatter and the Math library actually use) are correct. The
# .cia never calls its own helpers internally, so we localize them in the object and
# let libgcc supply the real, correct soft-float for all guest `double`/`float` work.
UC_REALX_SOFTFLOAT = [
    "__adddf3", "__subdf3", "__muldf3", "__divdf3",
    "__addsf3", "__subsf3", "__mulsf3", "__divsf3",
    "__floatsidf", "__floatsisf", "__extendsfdf2", "__truncdfsf2",
    "__fixdfsi", "__fixsfsi", "__negdf2", "__negsf2",
    "__eqdf2", "__nedf2", "__ltdf2", "__ledf2", "__gtdf2", "__gedf2", "__cmpdf3",
    "__eqsf2", "__nesf2", "__ltsf2", "__lesf2", "__gtsf2", "__gesf2", "__cmpsf3",
]
# The frozen euser export ordinals (EABI-mangled, matches our toolchain).
DEF_PATH = os.path.join(REPO, "src", "oss.FCL.sf.os.kernelhwsrv", "kernel", "eka",
                        "eabi", "euseru.def")
# euser_selftest is not a real euser symbol: the slot just past the real export table is reserved (it points at the
# unimplemented-export stub) so the extra ordinals after it (TTrap::Trap/UnTrap, build_euser_device.EXTRA_EXPORTS)
# keep the numbers the importing images are built against.
SELFTEST_NAME = "euser_selftest"
STUB_NAME = "__unimpl_export"
B = os.path.join(REPO, "src", "oss.FCL.sf.os.kernelhwsrv", "kernel", "eka")
ED = os.path.join(REPO, "toolchain", "euser_dll")


def workdir(name: str) -> str:
    """Scratch directory of one image build (objects, response files, intermediate ELFs)."""
    d = os.path.join(REPO, ".emitter-build", name)
    os.makedirs(d, exist_ok=True)
    return d

# "Fill-in" TUs: large euser source files pulled in for a handful of exports but
# which redefine many symbols the seams/real TUs already own. After compiling,
# any global one of these objects defines that ANOTHER (earlier) object also
# defines is localized in the fill-in object, so the existing (proven) definition
# wins and the final link stays collision-free (same idea as UC_REALX_SOFTFLOAT,
# generalized to whole-symbol collisions). Order matters: fill-ins are processed
# in list order, so a symbol unique to the fill-ins keeps its first occurrence.
FILLIN_BASENAMES = ["us_exec.cpp", "us_ksvr.cpp",
                    # euser TUs that make the exports EFILE.EXE imports real
                    # (client-server IPC, loader client, publish&subscribe, platsec,
                    # rw-lock, decompression, the epoc process/lib + atomics/UserSvr).
                    # All are big "everything" TUs -> fill-ins: fresh symbols
                    # (CServer2/E32Loader/RProperty/...) stay global; anything an
                    # earlier proven TU already owns is localized (see
                    # _localize_fillin_collisions). Order = first occurrence wins.
                    "us_mes.cpp", "ub_svr.cpp", "us_property.cpp", "us_secure.cpp",
                    "us_rwlock.cpp", "us_decode.cpp", "up_utl.cpp", "up_lib.cpp",
                    # common/secure.cpp owns TSecurityPolicy/TCapabilitySet/PlatSec
                    # (a broad common TU -> fill-in so its overlaps yield to proven TUs).
                    "secure.cpp"]

# The real euser sources (ARM, so every export is reachable by a plain blx). build_euser_device.py adds the device-only
# sources and the executive layer to this list.
SOURCES = [
    os.path.join(B, "common", "array.cpp"),
    os.path.join(B, "common", "arm", "carray.cia"),
    os.path.join(B, "common", "arm", "cmem.cia"),
    os.path.join(B, "common", "heap_hybrid.cpp"),
    os.path.join(B, "common", "debugfunction.cpp"),
    os.path.join(B, "common", "mem.cpp"),
    os.path.join(B, "common", "des8.cpp"),
    os.path.join(B, "common", "arm", "cdes8.cia"),
    os.path.join(B, "common", "des16.cpp"),
    os.path.join(B, "common", "arm", "cdes16.cia"),
    os.path.join(B, "euser", "us_func.cpp"),
    os.path.join(B, "euser", "us_des.cpp"),
    os.path.join(B, "euser", "us_lex16.cpp"),
    os.path.join(B, "euser", "unicode", "Compare.cpp"),
    os.path.join(B, "euser", "unicode", "collate.cpp"),
    os.path.join(B, "euser", "unicode", "unicode.cpp"),
    os.path.join(B, "euser", "unicode", "unitable.cpp"),
    os.path.join(B, "euser", "maths", "um_rtod.cpp"),
    os.path.join(B, "euser", "maths", "um_pow10.cpp"),
    os.path.join(B, "euser", "epoc", "arm", "uc_realx.cia"),
    os.path.join(B, "euser", "epoc", "arm", "uc_realx.cpp"),
    os.path.join(B, "euser", "epoc", "arm", "uc_i64.cia"),
    os.path.join(B, "euser", "maths", "um_utl.cpp"),
    os.path.join(B, "euser", "maths", "um_asin.cpp"),
    os.path.join(B, "euser", "maths", "um_atan.cpp"),
    os.path.join(B, "euser", "maths", "um_dtor.cpp"),
    os.path.join(B, "euser", "maths", "um_exp.cpp"),
    os.path.join(B, "euser", "maths", "um_frac.cpp"),
    os.path.join(B, "euser", "maths", "um_int.cpp"),
    os.path.join(B, "euser", "maths", "um_ln.cpp"),
    os.path.join(B, "euser", "maths", "um_log.cpp"),
    os.path.join(B, "euser", "maths", "um_mod.cpp"),
    os.path.join(B, "euser", "maths", "um_pow.cpp"),
    os.path.join(B, "euser", "maths", "um_rand.cpp"),
    os.path.join(B, "euser", "maths", "um_sin.cpp"),
    os.path.join(B, "euser", "maths", "um_spec.cpp"),
    os.path.join(B, "euser", "maths", "um_sqrt.cpp"),
    os.path.join(B, "euser", "maths", "um_tan.cpp"),
    os.path.join(B, "euser", "us_lex8.cpp"),
    os.path.join(B, "euser", "us_parse.cpp"),
    os.path.join(B, "euser", "us_htab.cpp"),
    os.path.join(B, "euser", "cbase", "ub_buf.cpp"),
    os.path.join(B, "euser", "cbase", "ub_array.cpp"),
    os.path.join(B, "euser", "cbase", "ub_obj.cpp"),
    os.path.join(B, "euser", "cbase", "ub_utl.cpp"),
    os.path.join(B, "euser", "cbase", "ub_cln.cpp"),
    os.path.join(B, "euser", "cbase", "ub_bma.cpp"),
    os.path.join(B, "euser", "cbase", "ub_circ.cpp"),
    os.path.join(B, "euser", "us_regn.cpp"),
    os.path.join(B, "euser", "cbase", "ub_act.cpp"),
    os.path.join(B, "euser", "us_que.cpp"),
    os.path.join(B, "common", "gcchelp.cpp"),
    os.path.join(B, "euser", "us_time.cpp"),
    os.path.join(B, "euser", "cbase", "ub_tim.cpp"),
    os.path.join(B, "euser", "us_exec.cpp"),
    os.path.join(B, "euser", "us_ksvr.cpp"),
    os.path.join(B, "euser", "us_mes.cpp"),
    os.path.join(B, "euser", "cbase", "ub_svr.cpp"),
    os.path.join(B, "euser", "us_property.cpp"),
    os.path.join(B, "euser", "us_secure.cpp"),
    os.path.join(B, "euser", "us_rwlock.cpp"),
    os.path.join(B, "euser", "us_decode.cpp"),
    os.path.join(B, "euser", "epoc", "up_utl.cpp"),
    os.path.join(B, "euser", "epoc", "up_lib.cpp"),
    os.path.join(B, "common", "secure.cpp"),
    os.path.join(B, "common", "arm", "atomics.cia"),
]


def compile_objs(workdir: str, sources: list[str] = None,
                 fillin: list[str] = None, extra_flags=None) -> list[str]:
    """Compile every SOURCE to an ARM object; return the object paths.

    `sources`/`fillin` default to the lists above; build_euser_device.py passes its own.
    `extra_flags(basename) -> list[str]` supplies per-source extra compiler args
    (e.g. `-D__GEN_USER_EXEC_CODE__` for the device exec layer).
    """
    sources = SOURCES if sources is None else sources
    fillin = FILLIN_BASENAMES if fillin is None else fillin
    env = dict(os.environ, SYMCC_ARM="1")
    objs = []
    for src in sources:
        base = os.path.basename(src)
        obj = os.path.join(workdir, base + ".o")
        # uc_realx.cia (TRealX soft-float) branches between its own functions with
        # PC-relative b/bl; -ffunction-sections puts each in a separate section, so
        # GAS can't resolve the cross-function branch ("symbol is in a different
        # section"). Keep this one TU in a single .text (the soft-float cluster is
        # reached as a unit anyway, so nothing is lost to GC).
        extra = (["-fno-function-sections", "-fno-data-sections"]
                 if base in ("uc_realx.cia", "atomics.cia") else [])
        # GCC 14 does not honor the spirit of __attribute__((naked)) for functions
        # returning an aggregate via sret: it brackets the asm body with
        # `mov r3,r0 ... mov r0,r3` return-pointer bookkeeping (kept even at -O2 --
        # DCE is off in naked fns). The PROLOGUE mov clobbers r3, which is a LIVE
        # ARGUMENT in every naked fn with sret + 3 register args:
        # TDesC8/16::Mid(pos,len) and TDes8/16::MidTPtr(pos,len). cdes16.cia's
        # range check then compares aLength==sret-pointer and ALWAYS panics
        # (found live at EFILE bring-up: USER 10 from TParseBase::Drive() on a
        # perfectly valid TParse). Fix: compile the des TUs with the machine-coded
        # guards OFF so the real C++ bodies all exist, and weaken everything in the
        # cdes objects so the C++ definitions win; the .cia keeps only what C++
        # does not define.
        if base in ("des8.cpp", "des16.cpp"):
            extra = extra + ["-D__MINIMUM_MACHINE_CODE__"]
        if extra_flags is not None:
            extra = extra + list(extra_flags(base))
        subprocess.run(["bash", SYMCC, src, "-O2", "-o", obj, *extra], check=True,
                       env=env, cwd=REPO)
        if base in ("cdes8.cia", "cdes16.cia"):
            subprocess.run([OBJCOPY, "--weaken", obj], check=True)
        if base == "uc_realx.cia":
            # Hide the broken GCC-ABI soft-float helpers so libgcc's win.
            subprocess.run([OBJCOPY,
                            *sum(([f"--localize-symbol={s}"] for s in UC_REALX_SOFTFLOAT), []),
                            obj], check=True)
        objs.append(obj)

    _localize_fillin_collisions(objs, fillin)
    return objs


def _localize_fillin_collisions(objs: list[str], fillin: list[str] = None) -> None:
    """Make the FILLIN_BASENAMES objects yield to every other object.

    For each fill-in object (in SOURCES order), localize any global symbol that a
    non-fill-in object — or an earlier fill-in — already defines globally, so the
    existing proven definition is the one the final link resolves against. A
    symbol unique to the fill-ins keeps its first occurrence global.
    """
    fillin = FILLIN_BASENAMES if fillin is None else fillin
    fillin_objs = [o for o in objs
                   if os.path.basename(o).rsplit(".o", 1)[0] in fillin]
    other_objs = [o for o in objs if o not in fillin_objs]

    claimed = _nm("--defined-only", *other_objs) if other_objs else set()
    for obj in fillin_objs:
        defined = _nm("--defined-only", obj)
        collisions = sorted(defined & claimed)
        if collisions:
            subprocess.run([OBJCOPY,
                            *sum(([f"--localize-symbol={s}"] for s in collisions), []),
                            obj], check=True)
        claimed |= (defined - set(collisions))


def _nm(mode: str, *files: str) -> set[str]:
    """Symbol names from nm; `mode` selects --defined-only / --undefined-only."""
    args = list(files)
    out = subprocess.run([NM, mode, *args], check=True,
                         capture_output=True, text=True).stdout
    names: set[str] = set()
    for line in out.splitlines():
        parts = line.split()
        if mode == "--defined-only":
            # "<addr> <T|W|...> <name>" — keep GLOBAL (uppercase) code/data symbols.
            if len(parts) == 3 and parts[1] in ("T", "W", "R", "D", "B", "V"):
                names.add(parts[2])
        else:  # --undefined-only: "U <name>" (or "<addr> U <name>")
            names.add(parts[-1])
    return names


def _libgcc() -> str:
    return subprocess.run([GCC, "-marm", "-march=armv5te", "-print-libgcc-file-name"],
                          check=True, capture_output=True, text=True).stdout.strip()


def _link(objs: list[str], roots: list[str], out_elf: str, workdir: str,
          ignore_unresolved: bool) -> None:
    ld = os.path.join(REPO, "emitter", "euser.ld")   # .text@0x8000 + data/bss
    # roots go through a response file: 770+ `-u` flags are a very long command line. --gc-sections keeps only
    # sections reachable from the roots.
    rsp = os.path.join(workdir, "roots.rsp")
    with open(rsp, "w") as fh:
        fh.write("\n".join(f"-Wl,-u,{n}" for n in roots))
    args = [GCC, "-nostdlib", "-Wl,-q", "-Wl,--gc-sections", "-Wl,--no-demangle",
            f"@{rsp}", f"-Wl,-T,{ld}", "-march=armv5te", "-marm", *objs, _libgcc(),
            "-o", out_elf]
    # extra linker args (--wrap User::Leave, see build_euser_device)
    args[1:1] = os.environ.get("EUSER_LD_EXTRA", "").split()
    if ignore_unresolved:
        args.insert(1, "-Wl,--unresolved-symbols=ignore-all")
    subprocess.run(args, check=True)


def compile_and_link(workdir: str, sources: list[str] = None,
                     fillin: list[str] = None, extra_flags=None
                     ) -> tuple[bytes, list[str], set[str]]:
    """Build the ELF that backs the full euser export table.

    Roots every euser.def symbol our sources actually define (so --gc-sections
    keeps them at addresses we can export), plus euser_selftest and the unimpl
    stub. A first (unresolved-tolerant) link discovers the symbols the rooted code
    calls but that we don't build from source yet; those are aliased to the stub
    so the final link is clean and any such call fails loud instead of running
    into address 0. Returns (elf, exportable_names, stubbed_names).
    """
    objs = compile_objs(workdir, sources, fillin, extra_flags)

    defmap = parse_def(DEF_PATH)
    def_names = set(defmap.by_ordinal.values())
    defined = _nm("--defined-only", *objs)
    exportable = sorted(def_names & defined)
    roots = exportable + [SELFTEST_NAME, STUB_NAME]

    # Pass 1: tolerate unresolved to enumerate what the rooted closure still needs.
    elf1 = os.path.join(workdir, "euser0.pass1.elf")
    _link(objs, roots, elf1, workdir, ignore_unresolved=True)
    missing = sorted(_nm("--undefined-only", elf1) - {""})

    # A tiny stub TU: each missing symbol becomes a 1-instruction thunk to the
    # unimplemented-export handler, so referencing code links and, if ever run,
    # panics cleanly. (Data symbols get the same address; using them faults — the
    # honest "not from source yet" outcome.)
    stub_s = os.path.join(workdir, "unimpl_stubs.s")
    with open(stub_s, "w") as fh:
        fh.write("\t.text\n\t.arm\n")
        for i, name in enumerate(missing):
            fh.write(f"\t.globl {name}\n\t.type {name},%function\n"
                     f"{name}:\n\tb {STUB_NAME}\n")
    stub_o = os.path.join(workdir, "unimpl_stubs.s.o")
    subprocess.run([GCC, "-march=armv5te", "-marm", "-c", stub_s, "-o", stub_o],
                   check=True)

    # Pass 2: clean link (no unresolved tolerance) with the stubs in place.
    elf2 = os.path.join(workdir, "euser0.elf")
    _link(objs + [stub_o], roots, elf2, workdir, ignore_unresolved=False)
    with open(elf2, "rb") as fh:
        return fh.read(), exportable, set(missing)


