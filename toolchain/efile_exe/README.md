# toolchain/efile_exe -- EXE-emission seam TUs

Support TUs for the EXEs `emitter/build_efile_exe.py` and its siblings emit. The file server itself is the unmodified
2010 fileserver TUs (`kernel/efile_compile_check.sh`) plus the REAL `uc_exe.cia` (exexp entry) and `common/gcchelp.cpp`
(operator new/delete); these files are the only non-Symbian code in the image:

- `dev_exe_startup.cpp` -- RunThread for modern GCC (uc_exe.cpp only has __GCC32__/__ARMCC__ bodies): heap setup,
  `User::InitProcess`, the `.init_array` static-ctor walk (bracketed by `emitter/efile.ld`), `E32Main`.
- `crt_shim.cpp` -- `__cxa_pure_virtual` / `__aeabi_atexit` / `__dso_handle` / sized operator delete; everything else
  C++-runtime comes from libgcc.
- `ldr_builtin_devices.cpp` -- the file server loader's answer for a driver that is linked into the kernel (see the file).
