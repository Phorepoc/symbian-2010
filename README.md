# symbian-2010

The 2010 Symbian open-source release, built from source and booted on QEMU Syborg to Symbian's own **minigui**
configuration: the release's EKA2 kernel, file server and window server, with the text shell (ESHELL) running in a
graphical WSERV window.

The source is not in this repository. [`sources.lock`](sources.lock) pins the commits of the 13 SymbianSource packages
the build needs, and [`fetch.sh`](fetch.sh) takes them from the [Phorepoc forks](https://github.com/Phorepoc). What is
here is what turns that source into a ROM: the build scripts, the modern-GCC glue, and an ELF-to-E32Image emitter.

## Quick start

You need Docker, git and about 4 GB of disk. Linux, macOS and Windows (Docker Desktop, Linux containers) work; the
build itself always runs inside the [Phorepoc toolchain image](https://github.com/Phorepoc/toolchain).

```sh
./fetch.sh      # the sources, into src/ (shallow clones at the pinned commits)
./build.sh      # out/minigui.rom and out/c_drive.img  (from scratch: a few minutes on a many-core machine, up to 20 on a laptop; re-runs only rebuild what changed)
./boot.sh       # boots the ROM, types `dir`, writes out/fb.png and out/serial.log
```

`build.sh` runs in a Docker volume (`phorepoc-symbian-2010`) that also keeps the objects, so a second run only rebuilds
what changed; `FULL=1 ./build.sh` starts from scratch. Every step logs to `out/log/`. `boot.sh` boots the ROM on the
[Phorepoc QEMU Syborg image](https://github.com/Phorepoc/qemu-syborg) (`ghcr.io/phorepoc/qemu-syborg`), which is also
where to look for running it by hand (`run-syborg`, keyboard input through the QEMU monitor).

CI ([`.github/workflows/build.yml`](.github/workflows/build.yml)) does exactly this on every push and keeps the
screenshot and the serial log as artifacts.

## What you get

![ESHELL running in Symbian's minigui configuration on QEMU Syborg](docs/minigui-eshell.png)

This is the 2010 release booted on this image: its own EKA2 kernel, file server and window server, built from the
original source with a modern compiler, running Symbian's **minigui** configuration. That is the text shell (ESHELL)
in a graphical window-server window, with the keyboard working and a FAT C: drive (here after typing `dir`).

Set your expectations accordingly:
- There is **no S60 UI** here: no home screen, no launcher, no apps, no touch UI. Minigui is the configuration
  Symbian itself used to bring up new hardware.
- The display is the Syborg board's plain 768x640 framebuffer, and input is the emulated keyboard.
- Boot to the prompt takes about 30-50 seconds; QEMU 0.9.1 emulates the CPU without acceleration.

The emulator image is [Phorepoc/qemu-syborg](https://github.com/Phorepoc/qemu-syborg).

### A frozen baseline

This image, and the 2010 baseline it runs, will most likely **stay frozen**: the point is to keep the original
release running as close to how it shipped as possible. The deeper and heavier changes happen elsewhere, in
Phorepoc OS's architecture, platform and device adaptations, where the S60 UI is optimised and modernised for today's
hardware.

## How it is built

| step | what | where |
|---|---|---|
| executive headers | `genexec.pl` of the release over `execs.txt` | `toolchain/genexec.sh` |
| nanokernel, kernel | the unmodified 2010 EKA2 kernel (multiple memory model), compiled with `arm-none-eabi-g++` 14 | `kernel/build_nkern.sh`, `kernel/build_ekern.sh` |
| euser, efsrv, hal | the user library, file server client and HAL, from the release's sources and `.def` files | `emitter/build_euser_device.py`, `build_efsrv_device.py`, `build_hal_device.py` |
| epoc32/include | what `abld export` does: the headers every component exports, from the `bld.inf` files | `tools/epoc32_export.py`, `tools/casefix.py` |
| file server | EFILE from the release's sources | `kernel/build_efile.sh`, `emitter/build_efile_exe.py` |
| components | every component of minigui from its real `.mmp`: resources with the release's `rcomp`, code with the compiler, import and export tables from the frozen `.def` files | `emitter/components.py`, `emitter/build_mmp.py` |
| boot pieces | ESTART, the FAT file system plug-in, the keymap, ESHELL | `emitter/build_estart_exe.py`, `build_elocal_fsy.py`, `build_ekdata_dll.py`, `build_eshell_exe.py` |
| bootstrap | the Syborg bootstrap, RVCT armasm translated to GNU as | `tools/armasm2gas/` |
| kernel link, ROM | the kernel linked at its ROM address with the Syborg variant and drivers, then the ROM image | `kernel/link_ekern.sh`, `kernel/build_rom.sh`, `tools/rombuild.py` |
| C: drive | a FAT16 image carrying the bitmap font | `tools/mkfat.py` |

The compiler is not the one the release was written for, so the images are not produced by `elf2e32`: the linker's ELF
output goes through `emitter/elf.py` (read the code, data and relocations) and `emitter/e32image.py` (write the
E32Image), and `tools/rombuild.py` prelinks the images into the ROM.

The 2010 source was written for a case-insensitive file system (`#include <f32plugin.h>` finds `F32plugin.h`).
`tools/casefix.py` links each spelling the source uses into `.casefix/`, which the compiler searches last, so nothing in
the source is renamed or edited.

## What is ours, and what changes the original

**No file of the Symbian source is edited.** Everything below is added beside it, or replaces a component, and is
needed because of the compiler or because there is no real hardware:

| what | why |
|---|---|
| `toolchain/symbian_gcc_prefix.h`, force-included in every translation unit | the release's compiler headers (`__GCCE__`/RVCT) define a few things a current GCC does not |
| `toolchain/overlay/e32cmn.h`, `e32cmn.inl` (include path overlays that `#include_next` the real headers) | with `-fno-exceptions` `TRAP` is the `setjmp`-style `TTrap::Trap`, which returns twice: the overlay declares it `returns_twice`, or GCC 14 keeps locals live across a leave in registers the leave then restores. And `_LIT_SECURE_ID` converts through a reference to a temporary that modern GCC replaces with NULL |
| `toolchain/kinc/` | forwarders for headers the release's build exports to a relocated path (`<kernel/heap_hybrid.h>`) |
| `toolchain/euser_dll/` | three small adapters for euser: the exec layer (`dev_user_execs.cpp` emits the generated executive), forwarders for the descriptor assembler's old-GCC names, sized `operator delete` |
| `toolchain/dll_rt/`, `toolchain/efile_exe/` | DLL and EXE entry and runtime pieces (`_E32Dll`, the `.init_array` walk, `__cxa_pure_virtual`), and a wrapper of the loader's `E32Image::LoadCodeSeg` that answers for drivers linked into the kernel (display) |
| `toolchain/shims/sgresource_null.cpp`, `surfaceupdateclient_null.cpp` | replace the sources of `sgresource.dll` and `surfaceupdateclient.dll`, with the real `.def` ordinal tables: the board has no GPU resource provider and no compositor, and the generic screen driver draws straight into the framebuffer |
| `toolchain/shims/ost_off/` | `OstTraceDefinitions.h` is generated by a trace compiler that is not part of the release; tracing is off |
| `kernel/ekern_seam.cpp` | the glue the kernel link needs: `HwInit0`, the variant entry, `TDesC8::Mid` (a hand-written assembler function modern GCC miscompiles, replaced through `ld --wrap`), skipping `P::StartExtensions` (the drivers are linked in), starting the built-in drivers and the file server, and a serial trace |
| `kernel/ekern_fb_glue.cpp`, `ekern_media_glue.cpp`, `ekern_input_glue.cpp` | bring up the Syborg framebuffer, local media and input drivers, which a ROM would load from files |
| the compiler flags in `toolchain/symcc` and `emitter/build_mmp.py` | `-fno-strict-aliasing`, `-fno-delete-null-pointer-checks`, `-fno-lifetime-dse`, `-fno-short-enums` ...: the 2010 source relies on what RVCT and GCC 3 did, each flag has its reason in the file |

The window server, the screen driver and the other components are built unmodified from their `.mmp` files. The one
component that is not the release's own is the bitmap font rasterizer plug-in, where Symbian's own `stubrasterizer.mmp`
(no vector stack in this configuration) is used.

## Layout

```
fetch.sh, sources.lock   the sources, pinned
build.sh, boot.sh        build in the toolchain image, boot on QEMU Syborg
scripts/                 what runs inside the container
kernel/                  kernel build and link, the glue, the ROM recipe
emitter/                 .mmp parser, component registry, ELF -> E32Image, one build script per image
toolchain/               compiler driver, prefix header, overlays, runtime pieces
tools/                   ROM writer, include export, bootstrap translation, FAT image, PNG writer
```

## Licence

[EPL-1.0](LICENSE), the licence of the Symbian release. The fetched source keeps its own licence headers.
