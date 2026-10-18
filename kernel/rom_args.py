"""rom_args -- rombuild arguments for every built registry component.

Prints one argument per line (`--xip` / spec / `--data` / spec ...) for kernel/build_rom.sh: every
emitter/components.py entry whose image exists in emitter/, plus the files its build staged for the ROM:
  <image>.romfiles   compiled resources (build_mmp.build_resources)
  <stem>.rsc         ECOM registration next to a plug-in DLL -> \\Resource\\Plugins
Minigui slot renames: wserv.exe -> EWSRV.EXE (estart launches it), econswserv.dll -> ECONS.DLL (ESHELL's console
loads it).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "emitter"))
import components as C  # noqa: E402
import mmp as MMP  # noqa: E402

SLOT = {"wserv.exe": "EWSRV.EXE", "econswserv.dll": "ECONS.DLL"}


def main() -> None:
    out = []
    seen = set()
    for key, comp in C.COMPONENTS.items():
        targettype = MMP.parse(os.path.join(MMP.BASE, comp["mmp"])).targettype
        rom = C.rom_name(key, targettype)
        img = os.path.join(REPO, "emitter", rom)
        zname = SLOT.get(rom, rom).lower()      # one ROM name once (Z: is case-blind)
        if os.path.isfile(img) and zname not in seen:
            seen.add(zname)
            out += ["--xip", "Sys\\Bin\\" + SLOT.get(rom, rom) + "=emitter/" + rom]
            if targettype == "plugin":
                rsc = os.path.splitext(rom)[0] + ".rsc"
                if os.path.isfile(os.path.join(REPO, "emitter", rsc)):
                    out += ["--data", "Resource\\Plugins\\" + rsc + "=emitter/" + rsc]
        man = img + ".romfiles"
        if os.path.isfile(man):
            for line in open(man).read().split():
                out += ["--data", line]
    print("\n".join(out))


if __name__ == "__main__":
    main()
