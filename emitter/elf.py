"""Minimal ELF32 (little-endian ARM, or x86-64 in the x32 ABI) reader for the ELF -> E32Image pipeline.

Reads a *linked* ELF produced with `-Wl,-q` (--emit-relocs): the linker resolves
all internal references and lays sections at their final VMAs, but keeps the
relocation records so we can re-emit the ones that need runtime fixup as E32
relocations. This is the toolchain-free-emitter half of Path A2
(docs/track-u/tls-p1-scope.md): our own emitter, not elf2e32, turns compiled ARM
into an E32Image.

Scope: exactly what the P1b spike + a first real DLL need — SHT_PROGBITS section
bytes, the symbol table, and R_ARM_ABS32 (type 2) relocations, classified into
code vs data by the target symbol's section. Extend as more reloc types appear.
"""

from __future__ import annotations

import struct
import dataclasses
from dataclasses import dataclass, field

R_ARM_ABS32 = 2
# R_ARM_TARGET1: emitted by g++ for .init_array entries; on arm-none-eabi the
# linker resolves it exactly as R_ARM_ABS32 (the stored word is the absolute
# address), so it takes the same E32 runtime reloc.
R_ARM_TARGET1 = 38
# PC-relative / marker relocs that a STATIC link has already fully resolved and
# that need NO runtime fixup: the code segment relocates as a unit, so any
# relative offset between two points inside it is invariant under load base.
# (--emit-relocs / -Wl,-q emits these even though the bytes are already final.)
R_ARM_NONE       = 0
R_ARM_PC24       = 1
R_ARM_REL32      = 3
R_ARM_THM_CALL   = 10
R_ARM_CALL       = 28
R_ARM_JUMP24     = 29
R_ARM_THM_JUMP24 = 30
R_ARM_PREL31     = 42
R_ARM_V4BX       = 40   # marks a `bx` for optional ARMv4 fixup; no-op on armv5+
_PC_REL_OR_MARKER = frozenset({
    R_ARM_NONE, R_ARM_PC24, R_ARM_REL32, R_ARM_THM_CALL, R_ARM_CALL,
    R_ARM_JUMP24, R_ARM_THM_JUMP24, R_ARM_PREL31, R_ARM_V4BX,
    41,   # R_ARM_TARGET2 (the EH tables' typeinfo refs): PC-relative -- device links pin --target2=rel, the encoding
          # arm-none-eabi's personality decodes (libgcc unwind-arm-common: "Pc-relative pointer")
})

# --- x86-64 in the x32 ABI (Path C, Phorepoc amd64): ELFCLASS32 container, EM_X86_64, and RELA
# relocation sections (the addend lives in the record; with --emit-relocs the section bytes are
# already fully relocated, exactly as for ARM's REL). NB the type NUMBERS collide with ARM's
# (2 = R_ARM_ABS32 but R_X86_64_PC32), so the tables are per machine.
EM_ARM = 40
EM_X86_64 = 62
R_X86_64_NONE = 0
R_X86_64_PC32 = 2
R_X86_64_PLT32 = 4
R_X86_64_32 = 10        # zero-extended absolute 32-bit: what x32 non-PIC code uses for addresses
R_X86_64_32S = 11       # sign-extended absolute 32-bit
R_X86_64_PC64 = 24
_ABS32_BY_MACHINE = {
    EM_ARM: frozenset({R_ARM_ABS32, R_ARM_TARGET1}),
    EM_X86_64: frozenset({R_X86_64_32, R_X86_64_32S}),
}
_PC_REL_BY_MACHINE = {
    EM_ARM: _PC_REL_OR_MARKER,
    EM_X86_64: frozenset({R_X86_64_NONE, R_X86_64_PC32, R_X86_64_PLT32, R_X86_64_PC64}),
}

# GOT-relative loads from PIC-built archive members (libgcc, libstdc++ pieces). A static non-PIC link relaxes every one of
# them away, but lld's --emit-relocs keeps the ORIGINAL type (GNU ld rewrote it), so the type says GOT while the bytes say
# what the linker made of the instruction (2026-10-13, the switch to lld). _x86_relaxed reads the bytes:
#   lea  sym(%rip),reg   (8D, modrm mod=00 rm=101) -> PC-relative, as R_X86_64_PC32
#   mov  $sym,reg        (C7, modrm mod=11)         -> absolute: R_X86_64_32, or 32S under REX.W (sign-extended)
#   call sym             (67 E8: addr32 prefix)     -> PC-relative
# A load still going through a GOT slot (8B) or any other form is rejected: there is no GOT in an E32 image.
R_X86_64_GOTPCREL = 9
R_X86_64_GOTPCRELX = 41
R_X86_64_REX_GOTPCRELX = 42
_X86_GOTPCREL_TYPES = frozenset({R_X86_64_GOTPCREL, R_X86_64_GOTPCRELX, R_X86_64_REX_GOTPCRELX})


def _x86_relaxed(r, sec):
    off = r.offset - sec.addr
    op, modrm = sec.data[off - 2], sec.data[off - 1]
    rex_w = r.rtype == R_X86_64_REX_GOTPCRELX and off >= 3 and (sec.data[off - 3] & 0xF8) == 0x48
    if op == 0x8D and (modrm & 0xC7) == 0x05:
        new = R_X86_64_PC32
    elif op == 0xC7 and (modrm & 0xC0) == 0xC0:
        new = R_X86_64_32S if rex_w else R_X86_64_32
    elif op == 0x67 and modrm == 0xE8:
        new = R_X86_64_PC32
    else:
        raise NotImplementedError(
            f"GOT-relative reloc type {r.rtype} at +0x{off:X} not relaxed by the link (bytes {op:02X} {modrm:02X}): no GOT in E32")
    return dataclasses.replace(r, rtype=new)


SHT_PROGBITS = 1
SHT_SYMTAB = 2
SHT_STRTAB = 3
SHT_NOBITS = 8
SHT_REL = 9
SHT_RELA = 4


@dataclass
class Section:
    name: str
    sh_type: int
    addr: int
    offset: int
    size: int
    link: int
    info: int
    entsize: int
    data: bytes
    addralign: int = 1


@dataclass
class Symbol:
    name: str
    value: int
    shndx: int


@dataclass
class Reloc:
    offset: int      # offset within the section being relocated
    sym: int         # symbol table index
    rtype: int       # ELF reloc type (R_ARM_*)
    addend: int = 0  # RELA's r_addend (x32); 0 for REL (ARM: the addend is in the section bytes)


@dataclass
class Extracted:
    """The E32-ready view of a linked ELF."""
    code_base: int
    code: bytes                       # .text (+ merged .rodata)
    data: bytes                       # .data (may be empty)
    data_base: int
    bss_size: int
    code_relocs: list[int] = field(default_factory=list)   # offsets into code
    data_relocs: list[int] = field(default_factory=list)   # offsets into data
    # per-offset delta selector nibble (RELOC_TEXT/RELOC_DATA), keyed by section
    code_reloc_types: dict[int, int] = field(default_factory=dict)
    data_reloc_types: dict[int, int] = field(default_factory=dict)
    symbols: dict[str, int] = field(default_factory=dict)   # name -> VMA
    # DATA imports: name -> [(code offset of the referencing word, addend)]. The
    # word becomes an E32 import slot (ordinal | addend<<16), NOT a text reloc.
    data_import_sites: dict[str, list] = field(default_factory=dict)
    data_import_pcrel: dict[str, list] = field(default_factory=dict)   # name -> [(code off, addend)]
    # x86-64/x32 only: offsets into code of %rip-relative displacements that reach the
    # WRITABLE segment. A PC-relative reference survives relocation only while both ends
    # move by the same delta; E32 moves code and data by different ones, so these carry
    # the link-time code->data distance and have to be re-aimed when the image is placed.
    # See "the %rip problem" in emitter/README or docs/phorepoc/x86-64-port.md.
    code_pcrel_data: list[int] = field(default_factory=list)
    # The alignment the LINKER assumed for each segment. Objects inside a section are laid
    # out relative to its start, so an image keeps its internal alignment only while it is
    # placed at an address congruent to its link base modulo this. x86-64 cares: `movdqa`
    # on a 16-byte-aligned constant #GPs if the constant moved to an odd multiple of 8.
    code_align: int = 4
    data_align: int = 4


class Elf32:
    def __init__(self, blob: bytes):
        self.blob = blob
        if blob[:4] != b"\x7fELF":
            raise ValueError("not an ELF file")
        if blob[4] != 1:
            raise ValueError("not ELF32")
        if blob[5] != 1:
            raise ValueError("not little-endian")
        (self.e_type, self.e_machine, _ev, self.e_entry, self.e_phoff,
         self.e_shoff, self.e_flags, self.e_ehsize, self.e_phentsize,
         self.e_phnum, self.e_shentsize, self.e_shnum,
         self.e_shstrndx) = struct.unpack_from("<HHIIIIIHHHHHH", blob, 16)
        if self.e_machine not in _ABS32_BY_MACHINE:
            raise ValueError(f"not ARM or x86-64/x32 (e_machine={self.e_machine})")
        self._read_sections()
        self._read_symbols()

    def _shname(self, shstr: bytes, off: int) -> str:
        end = shstr.index(b"\x00", off)
        return shstr[off:end].decode()

    def _read_sections(self) -> None:
        raw = []
        for i in range(self.e_shnum):
            base = self.e_shoff + i * self.e_shentsize
            (name_off, sh_type, _flags, addr, offset, size, link, info,
             align, entsize) = struct.unpack_from("<IIIIIIIIII", self.blob, base)
            raw.append((name_off, sh_type, addr, offset, size, link, info, entsize, align))

        shstr_hdr = raw[self.e_shstrndx]
        shstr = self.blob[shstr_hdr[3]:shstr_hdr[3] + shstr_hdr[4]]

        self.sections: list[Section] = []
        for (name_off, sh_type, addr, offset, size, link, info, entsize, align) in raw:
            data = (b"" if sh_type == SHT_NOBITS
                    else self.blob[offset:offset + size])
            self.sections.append(Section(self._shname(shstr, name_off), sh_type,
                                         addr, offset, size, link, info,
                                         entsize, data, align or 1))

    def _read_symbols(self) -> None:
        self.symbols: list[Symbol] = []
        symtab = next((s for s in self.sections if s.sh_type == SHT_SYMTAB), None)
        if not symtab:
            return
        strtab = self.sections[symtab.link].data
        n = len(symtab.data) // 16
        for i in range(n):
            st_name, st_value, _sz, _info, _other, st_shndx = \
                struct.unpack_from("<IIIBBH", symtab.data, i * 16)
            end = strtab.index(b"\x00", st_name)
            self.symbols.append(Symbol(strtab[st_name:end].decode(),
                                       st_value, st_shndx))

    def section(self, name: str) -> Section | None:
        return next((s for s in self.sections if s.name == name), None)

    def relocs_for(self, target_name: str) -> list[Reloc]:
        """REL entries whose target section is `target_name` (e.g. '.text')."""
        target_idx = next((i for i, s in enumerate(self.sections)
                           if s.name == target_name), None)
        out: list[Reloc] = []
        for s in self.sections:
            if s.sh_type not in (SHT_REL, SHT_RELA) or s.info != target_idx:
                continue
            ent = 8 if s.sh_type == SHT_REL else 12      # Elf32_Rela = Rel + r_addend
            for k in range(len(s.data) // ent):
                r_offset, r_info = struct.unpack_from("<II", s.data, k * ent)
                addend = struct.unpack_from("<i", s.data, k * ent + 8)[0] if ent == 12 else 0
                out.append(Reloc(r_offset, r_info >> 8, r_info & 0xFF, addend))
        return out


def extract(blob: bytes, data_imports=frozenset()) -> Extracted:
    """Turn a linked ELF into an E32-ready Extracted view.

    Merges .rodata into the code image (our linker script places it inside the
    .text output section, so it is already contiguous — we just take .text's
    bytes, which span it). Classifies each R_ARM_ABS32 as a code- or data-delta
    fixup by the section its target symbol lives in.
    """
    from e32image import RELOC_TEXT, RELOC_DATA

    elf = Elf32(blob)
    text = elf.section(".text")
    if not text:
        raise ValueError("no .text section")
    data = elf.section(".data")
    bss = elf.section(".bss")

    code_base = text.addr
    code_bytes = text.data
    # A `device` image keeps its unwind index as its own output section right after .text (mmp_dll_device.ld: ld
    # sorts .ARM.exidx only there); it is code all the same -- the phone's unwinder finds it through the header's
    # exception descriptor -- so it is folded in, at its link offset (its PREL31 words need no load-time fixup).
    exidx = elf.section(".ARM.exidx")
    if exidx and exidx.size:
        if exidx.addr < text.addr + text.size:
            raise ValueError(".ARM.exidx must follow .text")
        code_bytes = code_bytes + bytes(exidx.addr - (text.addr + text.size)) + exidx.data
    data_bytes = data.data if data else b""
    bss_size = bss.size if bss else 0
    # The E32Image has only iDataSize + iBssSize: the loader zero-fills bss RIGHT AFTER the data. When
    # .bss is aligned past .data's end (a 32-byte-aligned .bss after a .data ending mid-line), that gap
    # would shift the zero-fill down and leave the LAST bytes of .bss uninitialised (Path C NetSurf:
    # flex's yy_buffer_stack sat in them -> realloc(garbage), KERN-EXEC 3). Carry the gap as data.
    if data and bss and bss.addr > data.addr + len(data_bytes):
        data_bytes = data_bytes + bytes(bss.addr - (data.addr + len(data_bytes)))

    # The writable segment (.data followed by .bss) has its own VMA base, used as
    # the runtime-relocation anchor. With .data present that's its VMA; a DLL with
    # only .bss (no initialised statics) anchors on the .bss VMA instead — else
    # pointers into .bss (e.g. the arena) fall outside the classified data range.
    if data:
        data_base = data.addr
    elif bss:
        data_base = bss.addr
    else:
        data_base = 0

    # Which VMA range is code vs data — to pick the delta selector per reloc.
    code_lo, code_hi = code_base, code_base + len(code_bytes)
    data_lo = data_base
    data_hi = (bss.addr + bss.size) if bss else (data_base + (data.size if data else 0))

    ext = Extracted(code_base=code_base, code=code_bytes, data=data_bytes,
                    data_base=data_base, bss_size=bss_size,
                    code_align=text.addralign,
                    data_align=max(data.addralign if data else 1,
                                   bss.addralign if bss else 1, 4))
    ext.symbols = {s.name: s.value for s in elf.symbols if s.name}

    def classify(value: int) -> int:
        if code_lo <= value < code_hi:
            return RELOC_TEXT
        if data_lo <= value <= data_hi:
            return RELOC_DATA
        raise ValueError(f"reloc value 0x{value:X} in neither code nor data range")

    for target, base, size, off_list, type_map in (
        (".text", code_base, text.size, ext.code_relocs, ext.code_reloc_types),
        (".data", data_base, data.size if data else 0, ext.data_relocs, ext.data_reloc_types),
    ):
        sec = elf.section(target)
        if not sec:
            continue
        for r in elf.relocs_for(target):
            if elf.e_machine == EM_X86_64 and r.rtype in _X86_GOTPCREL_TYPES:
                r = _x86_relaxed(r, sec)
            if r.rtype in _PC_REL_BY_MACHINE[elf.e_machine]:
                # Resolved by the static link, and load-base-invariant ONLY while both ends
                # of the reference move by the same delta. Code->code does; code->data does
                # NOT, because E32 relocation is a two-delta model and the writable segment
                # runs somewhere else entirely. On x86-64/x32 g++ emits a %rip-relative
                # displacement for a plain scalar access to a static, and no flag prevents it
                # (-mcmodel=medium/large are rejected in x32 mode), so record those sites
                # instead of dropping them: whoever places the image has to re-aim them.
                s_val = elf.symbols[r.sym].value if r.sym < len(elf.symbols) else 0
                psym = elf.symbols[r.sym] if r.sym < len(elf.symbols) else None
                if psym is not None and psym.name in data_imports:
                    # S4.2: a %rip-relative reference to an IMPORTED static (Qt's QVariant::handler): the link
                    # aimed it at the local placeholder; only the ROM prelinker, which knows both the site and
                    # the export's run address, can aim it at the real one -- record RELA's addend
                    # (disp = S + A - P)
                    if target != ".text":
                        raise NotImplementedError(f"PC-relative DATA import {psym.name} from {target}")
                    off = r.offset - sec.addr
                    ext.data_import_pcrel.setdefault(psym.name, []).append((off, r.addend))
                    continue
                if data_hi > data_lo and s_val and data_lo <= s_val <= data_hi:
                    if target != ".text":
                        raise NotImplementedError(
                            f"PC-relative {target}->data reloc at +0x{r.offset - sec.addr:X}; "
                            "only code->data sites are handled")
                    ext.code_pcrel_data.append(r.offset - sec.addr)
                continue
            if r.rtype not in _ABS32_BY_MACHINE[elf.e_machine]:
                raise NotImplementedError(
                    f"reloc type {r.rtype} at {target}+0x{r.offset:X} unsupported "
                    f"(machine {elf.e_machine}; handled: absolute-32 + PC-relative/marker set)")
            # In a linked ELF (--emit-relocs), r_offset is an absolute VMA; make
            # it an offset into this section (== offset into the E32 code/data).
            off = r.offset - sec.addr
            stored = struct.unpack_from("<I", sec.data, off)[0]
            sym = elf.symbols[r.sym] if r.sym < len(elf.symbols) else None
            if sym is not None and sym.name in data_imports:
                if target != ".text":
                    raise NotImplementedError(
                        f"DATA import {sym.name} referenced from {target} (needs a code-side slot)")
                ext.data_import_sites.setdefault(sym.name, []).append(
                    (off, (stored - sym.value) & 0xFFFFFFFF))
                continue
            if stored == 0 and (sym is None or sym.value == 0):
                continue   # an undefined WEAK symbol (or R_ARM_NONE-like null): the word stays a null pointer, no fixup
            off_list.append(off)
            # Classify by the TARGET SYMBOL's address, not the stored word: the
            # word is S+A and the compiler may fold a large addend into it (a
            # base-register indexing trick puts the stored value far outside
            # any section), but the runtime delta is determined solely by where
            # S lives. Section symbols carry the section VMA, so they classify
            # the same way. Fall back to the stored word only if S is 0.
            s_val = elf.symbols[r.sym].value if r.sym < len(elf.symbols) else 0
            type_map[off] = classify(s_val or stored)

    # A writable segment whose alignment (a page-aligned .bss: jsc) exceeds its start's offset: the loader places data at a
    # page-aligned run address, so every address in it moves by a multiple of the page. Start the segment at the aligned-down
    # VMA (zero lead-in bytes) so link base == 0 mod align and the .bss alignment survives the move.
    pad = ext.data_base % ext.data_align if (ext.data or ext.bss_size) else 0
    if pad:
        ext.data = bytes(pad) + ext.data
        ext.data_base -= pad
        ext.data_relocs = [o + pad for o in ext.data_relocs]
        ext.data_reloc_types = {o + pad: t for o, t in ext.data_reloc_types.items()}
    return ext
