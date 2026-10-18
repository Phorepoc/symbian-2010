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

The ROM itself is built in [symbian-2010](https://github.com/Phorepoc/symbian-2010).

### A frozen baseline

This image, and the 2010 baseline it runs, will most likely **stay frozen**: the point is to keep the original
release running as close to how it shipped as possible. The deeper and heavier changes happen elsewhere, in
Phorepoc OS's architecture, platform and device adaptations, where the S60 UI is optimised and modernised for today's
hardware.

