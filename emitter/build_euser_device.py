"""build_euser_device -- euser.dll, the user library, from the 2010 source for the from-source kernel.

The real euser source (build_euser_dll.SOURCES) plus toolchain/euser_dll/dev_user_execs.cpp, the generated user-side
executive (compiled -D__GEN_USER_EXEC_CODE__, so Exec::* / E32Loader::* / UserSvr::* emit the svc numbers the kernel
implements), plus a few small adapters:
  - hash_env.cpp    DefaultHash::Integer + Mem::Swap (live only in uc_utl.cia)
  - desc_compat.cpp .cia mangled-name forwarders for the descriptor asm
  - cxx_rt_dev.cpp  the two modern-g++ runtime leaves (sized delete, __cxa_pure_virtual)
Exports the real euseru.def table; ordinals whose code is not built fall to the loud unimplemented-export stub
(EUSRNIMP panic carrying the caller's address).

Run from repo root:  python3 emitter/build_euser_device.py [out.dll]
"""
from __future__ import annotations

import os
import sys

import build_euser_dll as BE
from e32image import build_dll
from elf import extract
from euser_def import parse_def
from refcheck import check_image, format_result

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
KINC = os.path.join(REPO, "toolchain", "kinc")

# The exec layer: the generated user-side executive (all 281 Exec::/E32Loader::/UserSvr:: stub bodies).
DEV_EXECS = os.path.join(BE.ED, "dev_user_execs.cpp")

# Further real euser TUs the images that import euser need (the exports EFILE, WSERV ... reach).
#   uc_trp.cia + us_trp.cpp  the trap harness (User::Leave family, TTrap). __LEAVE_EQUALS_THROW__ is undefined in
#                  our build (-fno-exceptions), so uc_trp.cia's setjmp-style context-save Leave/Trap is selected.
#   uc_huffman.cia TBitInput::ReadL/HuffmanL (machine-coded; the C++ bodies in us_decode.cpp are #ifdef'd out under
#                  __HUFFMAN_MACHINE_CODED__).
#   us_encode.cpp  Huffman::IsValid.
#   us_hand.cpp    TFindHandleBase (+ handle-name find wrappers).
#   us_loc.cpp     TLocaleMessageText (locale message accessor).
#   common/alloc.cpp  RAllocator::MaxLength (+ RAllocator base).
#   uc_exec.cia    RThread::RequestSignal/RequestComplete (+ machine-coded exec wrappers; a hand-ARM grab-bag).
#   uc_utl.cia     RFastLock::Wait()/Signal() (+ util grab-bag).
_EU = os.path.join(BE.B, "euser")
DEVICE_EXTRA_SOURCES = [
    os.path.join(_EU, "epoc", "arm", "uc_trp.cia"),
    os.path.join(_EU, "us_trp.cpp"),
    os.path.join(_EU, "epoc", "arm", "uc_huffman.cia"),
    os.path.join(_EU, "us_encode.cpp"),
    os.path.join(_EU, "us_hand.cpp"),
    os.path.join(_EU, "us_loc.cpp"),
    os.path.join(BE.B, "common", "alloc.cpp"),
    # us_shbuf.cpp: RShPool / RShBuf / TShPoolCreateInfo
    os.path.join(_EU, "us_shbuf.cpp"),
    # us_utl.cpp: the real Panic(TCdtPanic) ("USER" category; the descriptor/container check panics route here) +
    # PanicTFixedArray. uc_trp.cpp: PanicNoTrapFrame + the trap-harness C++ leaves.
    os.path.join(_EU, "us_utl.cpp"),
    os.path.join(_EU, "epoc", "arm", "uc_trp.cpp"),
    os.path.join(_EU, "epoc", "arm", "uc_exec.cia"),
    os.path.join(_EU, "epoc", "arm", "uc_utl.cia"),
    os.path.join(_EU, "us_graph.cpp"),        # TRect/TPoint/TSize geometry
    os.path.join(_EU, "cbase", "ub_cons.cpp"),  # CConsoleBase + Console::NewL (RLibrary-loads econs.dll, NewConsole @1)
    os.path.join(_EU, "us_evnt.cpp"),         # TRawEvent accessors (WSERV's keyboard path)
    os.path.join(_EU, "cbase", "ub_dtim.cpp"),  # CDeltaTimer
    os.path.join(_EU, "cbase", "ub_tque.cpp"),  # TTickCountQue, CDeltaTimer's queue
    os.path.join(_EU, "us_ref.cpp"),          # RRefBase (Alloc/Copy/Free)
    os.path.join(_EU, "locmapping.cpp"),      # LocaleMapping: the distinct-locale-model language -> 3-DLL table
    os.path.join(BE.ED, "cxx_rt_dev.cpp"),    # sized operator delete + __cxa_pure_virtual
    # the graphical stack's euser needs: CPolicyServer (fbserv + wserv are policy servers), RMsgQueueBase (ws32/wserv
    # event queues), Power (wserv's power-event handling)
    os.path.join(_EU, "cbase", "ub_polsvr.cpp"),
    os.path.join(_EU, "us_mqueue.cpp"),
    os.path.join(_EU, "us_power.cpp"),
]
# All of these are FILL-INS: any global they define that an earlier object already owns is localized, so only their
# fresh leaves go global and the link stays collision-free (see build_euser_dll._localize_fillin_collisions).
DEVICE_FILLIN = BE.FILLIN_BASENAMES + [os.path.basename(s) for s in DEVICE_EXTRA_SOURCES]
# Exports appended PAST the real euseru.def table: our -fno-exceptions build makes the TRAP macro reference the
# setjmp-style TTrap::Trap/UnTrap entry points, which euseru.def does not list (an RVCT __LEAVE_EQUALS_THROW__
# build has no such imports). EFILE.EXE imports them by ordinal, so they get spare ordinals here: the reserved slot
# = max_ordinal+1, then these in list order (max+2, max+3). build_efile_exe.py resolves against this list.
EXTRA_EXPORTS = ["_ZN5TTrap4TrapERi", "_ZN5TTrap6UnTrapEv"]


def extra_export_ordinal(name: str, max_ordinal: int) -> int:
    """Ordinal of an EXTRA_EXPORTS name (the reserved slot occupies max_ordinal+1)."""
    return max_ordinal + 2 + EXTRA_EXPORTS.index(name)


# The two big hand-ARM grab-bags branch between their own functions with PC-relative b/bl, which -ffunction-sections
# would split across sections (GAS: "symbol is in a different section"); keep each in one .text.
_SINGLE_SECTION = {"uc_exec.cia", "uc_utl.cia"}


def _device_sources() -> list[str]:
    """The real euser sources + the device-only real TUs + the adapters + dev_user_execs.cpp (last: it defines
    symbols no real-source TU defines)."""
    kept_ed = [os.path.join(BE.ED, n) for n in ("hash_env.cpp", "desc_compat.cpp", "unimpl_export.cpp")]
    return BE.SOURCES + DEVICE_EXTRA_SOURCES + kept_ed + [DEV_EXECS]


def _extra_flags(basename: str) -> list[str]:
    # SYMBIAN_DISTINCT_LOCALE_MODEL: variant-wide on Symbian^3 -- without it LoadLocale is the old single-DLL model
    # (a KErrNotSupported stub against the 3-DLL elocl_* a Symbian^3 ROM carries)
    return _extra_flags_tu(basename) + ["-DSYMBIAN_DISTINCT_LOCALE_MODEL"]


def _extra_flags_tu(basename: str) -> list[str]:
    """Per-TU extra compiler args. dev_user_execs.cpp needs the generated-exec macro + the kinc header shim
    (u32exec.h -> exec_user.h bodies); the big grab-bag .cia files need a single .text section."""
    if basename == "dev_user_execs.cpp":
        return ["-D__GEN_USER_EXEC_CODE__", f"-I{KINC}"]
    if basename == "heap_hybrid.cpp":	# keeps finding its "..." headers
        return ["-iquote", os.path.join(BE.B, "common")]
    flags = ["-fno-function-sections", "-fno-data-sections"] if basename in _SINGLE_SECTION else []
    if basename.lower().endswith(".cia"):
        # The atomics must match the kernel's CPU configuration (Cortex-A8, kernel/link_ekern.sh EXTRA): with the
        # symcc default (__MARM_ARMV5__), cpudefs.h selects the kernel-ASSISTED atomics (atomic_*_exec.h ->
        # FAST_EXEC1(EFastExecFastAtomic*)) and our v7 kernel's fast exec table has no such entries, so the first
        # __e32_atomic_* call dies KERN-EXEC 27. ARMV7 selects the real LDREX/STREX bodies. It also keeps the fast
        # locks (RFastLock Wait/Signal) off SWP.
        return flags + ["-march=armv7-a", "-mno-unaligned-access",
                "-U__MARM_ARMV5__", "-D__MARM_ARMV7__", "-D__CPU_CORTEX_A8N__"]
    return flags


DEVICE_SOURCES = _device_sources()

# User::Leave and User::Panic/Invariant are wrapped nowhere: the images are plain setjmp-TRAP builds.


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "euser_dev.dll")
    workdir = BE.workdir("euser")

    defmap = parse_def(BE.DEF_PATH)
    elf_blob, exportable, stubbed = BE.compile_and_link(
        workdir, sources=DEVICE_SOURCES, fillin=DEVICE_FILLIN,
        extra_flags=_extra_flags)
    if os.environ.get("EUSER_LIST_UNIMPL") == "1":
        for n in sorted(stubbed):
            print(f"LINKSTUB {n}")
    ext = extract(elf_blob)

    if ext.code_base != BE.CODE_BASE:
        sys.exit(f"code_base mismatch: 0x{ext.code_base:X} != 0x{BE.CODE_BASE:X}")
    for name in (BE.SELFTEST_NAME, BE.STUB_NAME):
        if name not in ext.symbols:
            sys.exit(f"required symbol {name!r} not in ELF")

    exportable_set = set(exportable)
    stub_off = ext.symbols[BE.STUB_NAME] - ext.code_base
    export_offsets = []
    real = 0
    for ordn in range(1, defmap.max_ordinal + 1):
        name = defmap.name(ordn)
        if name in exportable_set and name in ext.symbols:
            export_offsets.append(ext.symbols[name] - ext.code_base)
            real += 1
        else:
            export_offsets.append(stub_off)
            if os.environ.get("EUSER_LIST_UNIMPL") == "1":   # which exports are still unimplemented stubs
                print(f"UNIMPL {ordn} {name}")
    selftest_ord = defmap.max_ordinal + 1
    export_offsets.append(ext.symbols[BE.SELFTEST_NAME] - ext.code_base)
    # TTrap::Trap/UnTrap at spare ordinals past the reserved slot (see EXTRA_EXPORTS).
    for name in EXTRA_EXPORTS:
        if name not in ext.symbols:
            sys.exit(f"EXTRA_EXPORTS symbol {name!r} not in ELF")
        export_offsets.append(ext.symbols[name] - ext.code_base)

    print(f"code: {len(ext.code)} B @0x{ext.code_base:X}   "
          f"data: {len(ext.data)} B   bss: {ext.bss_size} B")
    print(f"exports: {len(export_offsets)} ordinals  "
          f"({real}/{defmap.max_ordinal} real from source, "
          f"{defmap.max_ordinal - real} -> unimpl stub); "
          f"{len(stubbed)} link stubs; reserved slot @ {selftest_ord}; "
          + ", ".join(f"{n} @ {extra_export_ordinal(n, defmap.max_ordinal)}"
                      for n in EXTRA_EXPORTS))
    print(f"code relocs: {len(ext.code_relocs)}   data relocs: {len(ext.data_relocs)}")

    # euser.mmh: `uid 0x1000008d 0x100039e5`; images built against the SDK import euser{000a0000}[100039e5].dll and
    # the loader matches that UID3
    img = build_dll(ext.code, export_offsets, uid2=0x1000008D, uid3=0x100039E5,
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
