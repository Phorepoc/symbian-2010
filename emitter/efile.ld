/* efile.ld — link script for the from-source EFILE.EXE (Track B / M7).
 * spike.ld plus: the EXE entry (_E32Startup) and the static-ctor table.
 * .init_array is KEEPed INSIDE the .text output section so elf.extract's
 * ".text = code+rodata" view spans it and its ABS32 entries become ordinary
 * TEXT relocs; dev_exe_startup.cpp walks __init_array_start..__init_array_end
 * before E32Main (the modern-GCC equivalent of uc_exe.cpp's __CTOR_LIST__). */
ENTRY(_E32Startup)
SECTIONS {
  . = 0x8000;
  .text : {
    *(.text) *(.text.*) *(.rodata) *(.rodata.*)
    . = ALIGN(4);
    __init_array_start = .;
    KEEP(*(SORT_BY_INIT_PRIORITY(.init_array.*)))
    KEEP(*(.init_array))
    __init_array_end = .;
    LONG(0)   /* keep __init_array_end (an end-pointer a TU stores as an
                 ABS32) strictly INSIDE the code range, so elf.extract's
                 classifier reads it as a TEXT reloc — EFILE has no static
                 ctors, so the bracket is empty and lands at end-of-.text */
  }
  /* 8-align: the runtime data base (iUserLocalBase 0x400000) is 8-aligned, and
     the XIP prelink shifts every data/bss word by (0x400000 - this VMA); if the
     link VMA were only 4-aligned the shift would 4-skew all 8-byte-aligned
     objects and the euser atomics' alignment check (ldrexd) faults — seen live
     as a DFSR=1 abort in __e32_atomic_cas_ord64 from RReadWriteLock::ReadLock. */
  . = ALIGN(8);
  .data : { *(.data) *(.data.*) }
  .bss  : { *(.bss) *(.bss.*) *(COMMON) }
  /DISCARD/ : { *(.ARM.attributes) *(.comment) *(.note*)
                *(.ARM.exidx*) *(.ARM.extab*) }
}
