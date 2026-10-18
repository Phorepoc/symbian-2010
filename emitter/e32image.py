"""E32Image emitter — Milestone 1 (Stage 1: "loads and validates").

Emits a Symbian E32Image whose bytes are accepted by EKA2L1's loader
(`parse_e32img`, EKA2L1 @47b4ad76a). This is the proof-of-concept emitter; the
byte-layout knowledge here is what the real KlainMainLang backend will reproduce.

Spec: ../docs/formats/e32image-spec.md
Everything little-endian (ARM Symbian + x86 host are both LE; EKA2L1 only
byte-swaps on a big-endian host).
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

# ── Constants from EKA2L1 source ─────────────────────────────────────────────
E32IMG_SIGNATURE = 0x434F5045  # 'EPOC' LE  (e32img.cpp:219)
UID1_EXE = 0x1000007A          # e32_img_type::exe  (e32img.h:56)
UID1_DLL = 0x10000079          # e32_img_type::dll
CPU_ARMV5 = 0x2001             # e32_cpu::armv5     (e32img.h:45)
CPU_X86 = 0x1000               # e32_cpu::x86 -- also what Path C (amd64 CPU, x32 ABI) images carry

# flags bitfield (e32img.cpp dump_flag_info / header_format)
FLAG_DLL = 0x00000001
FLAG_ABI_EABI = 0x1 << 3       # bits 3-4 ABI: 1 = EABI
FLAG_EPT_EKA2 = 0x1 << 5       # bits 5-7 entry-point type: >=1 = EKA2
FLAG_HDRFMT_J = 0x1 << 24      # bits 24-27 header format: 1 = J
FLAG_HDRFMT_V = 0x2 << 24      # bits 24-27 header format: 2 = V
FLAG_IMPFMT_ELF = 0x1 << 28    # bits 28-31 import format: 0 = PE, 1 = ELF, 2 = PE2 (f32image.h)

# Recommended flags for a minimal EABI/EKA2 EXE, V-format, uncompressed.
FLAGS_DEFAULT_EXE = FLAG_ABI_EABI | FLAG_EPT_EKA2 | FLAG_HDRFMT_V  # 0x02000028


# ── UID checksum (crypt.cpp: crc16 / calculate_checksum / ..._uid_checksum) ──
def _crc_ccitt_table() -> list[int]:
    """CRC-CCITT/XModem table: poly 0x1021, MSB-first, init 0.

    Matches crypt.cpp:24 (crc_tab starts 0x0000, 0x1021, 0x2042, 0x3063, ...).
    """
    table = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
        table.append(crc)
    return table


_CRC_TAB = _crc_ccitt_table()


def crc16(crc: int, data: bytes) -> int:
    """crypt.cpp:51 — crc = (crc<<8) ^ tab[((crc>>8) ^ byte) & 0xff]."""
    for b in data:
        crc = ((crc << 8) ^ _CRC_TAB[((crc >> 8) ^ b) & 0xFF]) & 0xFFFF
    return crc


def _checksum_half(uid12: bytes, start: int) -> int:
    """crypt.cpp:196 — CRC over 6 bytes sampled every other byte from `start`."""
    sampled = uid12[start:12:2]  # start, start+2, ... start+10 (6 bytes)
    return crc16(0, sampled)


def uid_checksum(uid1: int, uid2: int, uid3: int) -> int:
    """crypt.cpp:214 — (odd_crc << 16) | even_crc over the 12-byte UID block."""
    uid12 = struct.pack("<III", uid1, uid2, uid3)
    even = _checksum_half(uid12, 0)  # bytes 0,2,4,6,8,10
    odd = _checksum_half(uid12, 1)   # bytes 1,3,5,7,9,11
    return ((odd << 16) | even) & 0xFFFFFFFF


# ── Header ───────────────────────────────────────────────────────────────────
@dataclass
class E32ImageHeader:
    """The 124-byte basic header (e32img.h:118, sizeof == 124, no padding).

    `check` is computed from the UIDs; leave it None and call finalize().
    """

    uid1: int = UID1_EXE
    uid2: int = 0
    uid3: int = 0
    check: int | None = None
    sig: int = E32IMG_SIGNATURE
    header_crc: int = 0            # not validated by the parser
    module_version: int = 0x000A0000  # 4 bytes @24; MUST avoid 0x1000/0x2000
    compression_type: int = 0     # 0 = uncompressed
    petran_major: int = 0
    petran_minor: int = 0
    petran_build: int = 0
    time_lo: int = 0
    time_hi: int = 0
    flags: int = FLAGS_DEFAULT_EXE
    code_size: int = 0
    data_size: int = 0
    heap_size_min: int = 0x1000
    heap_size_max: int = 0x100000
    stack_size: int = 0x2000
    bss_size: int = 0
    entry_point: int = 0
    code_base: int = 0x8000
    data_base: int = 0
    dll_ref_table_count: int = 0
    export_dir_offset: int = 0
    export_dir_count: int = 0
    text_size: int = 0
    code_offset: int = 0
    data_offset: int = 0
    import_offset: int = 0
    code_reloc_offset: int = 0
    data_reloc_offset: int = 0
    priority: int = 350           # process_priority::foreground (process.h:81)
    cpu: int = CPU_ARMV5

    def finalize(self) -> "E32ImageHeader":
        if self.check is None:
            self.check = uid_checksum(self.uid1, self.uid2, self.uid3)
        return self

    def pack(self) -> bytes:
        self.finalize()
        # module_version occupies the 4 bytes at offset 24 (struct: major u16,
        # minor u16). We pack it as one LE u32 so callers can reason about the
        # whole word (the EKA1-detection landmine, spec §1).
        blob = struct.pack(
            "<"      # little-endian
            "I"      # uid1
            "I"      # uid2
            "I"      # uid3
            "I"      # check
            "I"      # sig
            "I"      # header_crc
            "I"      # module_version (major|minor)  @24
            "I"      # compression_type              @28
            "BBH"    # petran_major, petran_minor, petran_build  @32
            "I"      # time_lo                        @36
            "I"      # time_hi                        @40
            "I"      # flags                          @44
            "I"      # code_size                      @48
            "I"      # data_size
            "I"      # heap_size_min
            "I"      # heap_size_max
            "I"      # stack_size
            "I"      # bss_size
            "I"      # entry_point                    @72
            "I"      # code_base
            "I"      # data_base
            "I"      # dll_ref_table_count            @84
            "I"      # export_dir_offset
            "I"      # export_dir_count
            "I"      # text_size                      @96
            "I"      # code_offset                    @100
            "I"      # data_offset
            "I"      # import_offset                  @108
            "I"      # code_reloc_offset
            "I"      # data_reloc_offset
            "H"      # priority                       @120
            "H",     # cpu                            @122
            self.uid1, self.uid2, self.uid3, self.check, self.sig,
            self.header_crc, self.module_version, self.compression_type,
            self.petran_major, self.petran_minor, self.petran_build,
            self.time_lo, self.time_hi, self.flags, self.code_size,
            self.data_size, self.heap_size_min, self.heap_size_max,
            self.stack_size, self.bss_size, self.entry_point, self.code_base,
            self.data_base, self.dll_ref_table_count, self.export_dir_offset,
            self.export_dir_count, self.text_size, self.code_offset,
            self.data_offset, self.import_offset, self.code_reloc_offset,
            self.data_reloc_offset, self.priority, self.cpu,
        )
        assert len(blob) == 124, f"header must be 124 bytes, got {len(blob)}"
        return blob


@dataclass
class VSection:
    """The 28-byte V-format extension (present iff header_format == 2)."""

    secure_id: int = 0
    vendor_id: int = 0
    cap1: int = 0
    cap2: int = 0
    exception_des: int = 0
    spare2: int = 0
    export_desc_size: int = 0
    export_desc_type: int = 0
    export_desc: int = 0

    def pack(self) -> bytes:
        blob = struct.pack(
            "<IIIIIIHBB",
            self.secure_id, self.vendor_id, self.cap1, self.cap2,
            self.exception_des, self.spare2, self.export_desc_size,
            self.export_desc_type, self.export_desc,
        )
        assert len(blob) == 28, f"V-section must be 28 bytes, got {len(blob)}"
        return blob


# ── Relocations ──────────────────────────────────────────────────────────────
# rel_info u16 = (type << 12) | (offset & 0x0FFF); the loader does
# virtual_addr = block.base + (info & 0xFFF), rel_type = info & 0xF000
# (libmanager.cpp:59), then *(code_base_ptr + virtual_addr) += delta
# (codeseg.cpp:166), where text delta = code_run_addr - code_base.
RELOC_RESERVED = 0x0000   # also the block padding value (loader::relocation_type)
RELOC_TEXT = 0x1000       # fix by (code_run_addr - code_base)
RELOC_DATA = 0x2000       # fix by (data_run_addr - data_base)
RELOC_INFERRED = 0x3000   # loader picks text/data by inspecting the stored value

# ── the %rip problem, and why it needs a file of its own ─────────────────────────────────────
# E32 relocation is a TWO-DELTA model: every fixup is a whole 32-bit word that gets either the
# code delta or the data delta. It can express "this word holds an address" and nothing else.
# A %rip-relative displacement is not an address -- it is a DISTANCE between two points that
# E32 moves apart, and the fixup it needs is (data_delta - code_delta), which no reloc type in
# the format can ask for. Symbian never had to: ARM reaches a static through a literal pool
# (an absolute R_ARM_ABS32 word) and the real x86 port was IA-32, which has no %rip at all.
#
# On amd64/x32 g++ emits a %rip-relative displacement for a plain scalar access to a static
# and there is no way to turn it off (-fno-pic does not, and -mcmodel=medium/large are rejected
# outright in x32 mode). So these sites are recorded BESIDE the image, in a text sidecar, and
# the PRELINKER re-aims them -- which is sound because a prelinked XIP image has exactly one
# code address and one data address, forever. It would NOT be sound for a RAM-loaded image, so
# the sidecar is deliberately not part of the E32Image: nothing can mistake it for something a
# loader could apply.
PCREL_SIDECAR_SUFFIX = ".pcrel"


def write_pcrel_sidecar(image_path: str, ext) -> str:
    """Write <image>.pcrel beside an x32 image: what the prelinker needs and the E32Image
    cannot carry -- the code->data %rip sites, and the SEGMENT ALIGNMENTS the linker assumed.
    ALWAYS written (an empty site list is a fact, not a missing file), so a stale or absent
    sidecar is distinguishable from an image that genuinely has no such sites."""
    path = image_path + PCREL_SIDECAR_SUFFIX
    with open(path, "w", newline="\n") as fh:
        fh.write("# phorepoc x32 placement facts. %rip-relative code->data displacements, to\n"
                 "# be re-aimed by the prelinker by (data_delta - code_delta), and the\n"
                 "# alignment each segment must keep when it moves. See e32image.py.\n")
        fh.write("code_base 0x%X\ndata_base 0x%X\ncode_align %d\ndata_align %d\ncount %d\n"
                 % (ext.code_base, ext.data_base, ext.code_align, ext.data_align,
                    len(ext.code_pcrel_data)))
        for off in sorted(ext.code_pcrel_data):
            fh.write("%08x\n" % off)
        # S4.2: %rip-relative references to an imported static: pcimp <dll> <ordinal> <code off> <addend>
        for dll, ordn, off, addend in getattr(ext, "pcimp", []):
            fh.write("pcimp %s %d %08x %d\n" % (dll, ordn, off, addend))
    return path


def build_reloc_section(offsets, rtype: int = RELOC_TEXT) -> bytes:
    """A relocation section per `read_relocations` (e32img.cpp:101).

    Layout: [size u32][num_relocs u32] then per-4KB-page blocks
            [base u32][block_size u32][rel_info u16 ...]. Each block is padded
    with a trailing 0x0000 entry to a 4-byte boundary. Each named offset is a
    byte offset from the section base (code start for a code-reloc section); it
    names a 32-bit word in the loaded image to be fixed up.

    `offsets` is either a list[int] (all fixed up with the uniform `rtype`
    delta-selector nibble) or a dict[int, int] mapping each offset to its own
    nibble (RELOC_TEXT / RELOC_DATA / RELOC_INFERRED). The nibble selects the
    DELTA (codeseg.cpp:135): TEXT=code_delta, DATA=data_delta, INFERRED=by value.
    The SECTION the word lives in (text vs data base_ptr) is set separately by
    which section this reloc belongs to (code_reloc vs data_reloc, libmanager
    .cpp:64) — so a word in code that points into data goes in the code-reloc
    section with a DATA nibble.
    """
    from collections import OrderedDict

    types = offsets if isinstance(offsets, dict) else {o: rtype for o in offsets}

    pages: "OrderedDict[int, list[int]]" = OrderedDict()
    for off in sorted(types):
        pages.setdefault(off & 0xFFFFF000, []).append((off & 0xFFF) | types[off])

    blocks = bytearray()
    total = 0
    for base, entries in pages.items():
        total += len(entries)
        ents = list(entries)
        if len(ents) % 2:
            ents.append(0)  # pad to 4-byte alignment (loader treats 0 as padding)
        block_size = 8 + 2 * len(ents)
        blocks += struct.pack("<II", base, block_size)
        blocks += struct.pack(f"<{len(ents)}H", *ents)

    # iSize = the blocks only, NOT this 8-byte header (elf2e32 e32imagefile.cpp `iSize = rsize`; F32's
    # ValidateRelocations measures from the first block). It said 8 + blocks until X4.37 -- invisible
    # while only the ROM prelinker read it, KErrCorrupt the first time F32 loaded an image from C:.
    return struct.pack("<II", len(blocks), total) + bytes(blocks)


# ── Imports (EKA2 / "elf" form) ──────────────────────────────────────────────
def build_import_section(imports) -> tuple[bytes, int]:
    """Build the EKA2 import section (e32img.cpp:462, resolved by
    libmanager.cpp:135 `elf_fix_up_import_dir`).

    `imports` is a list of `(dll_name, entries)` blocks, where `entries` is a
    list of `(code_offset, ordinal, adj)`. For modern (>= eka2) images the block
    stores CODE OFFSETS, and the 32-bit word the emitter has placed at each code
    offset holds `ordinal | (adj << 16)`; at load time the loader reads that
    word for the ordinal, resolves the export in `dll_name`, and overwrites the
    word with `export_addr + adj`.

    On-disk layout at import_offset:
        [size u32]
        per block: [dll_name_offset u32 (rel to import_offset)]
                   [number_of_imports u32]
                   [code_offset u32 × n]
        then the null-terminated dll-name strings the offsets point at.

    Returns (bytes, dll_ref_table_count).
    """
    header_size = 4
    for _dll, entries in imports:
        header_size += 8 + 4 * len(entries)

    name_offsets: dict[str, int] = {}
    names = bytearray()
    cur = header_size
    for dll, _entries in imports:
        if dll not in name_offsets:
            name_offsets[dll] = cur
            nb = dll.encode("ascii") + b"\x00"
            names += nb
            cur += len(nb)

    blocks = bytearray()
    for dll, entries in imports:
        blocks += struct.pack("<II", name_offsets[dll], len(entries))
        for (off, _ordn, _adj) in entries:
            blocks += struct.pack("<I", off)

    total = 4 + len(blocks) + len(names)
    return struct.pack("<I", total) + bytes(blocks) + bytes(names), len(imports)


# ── Whole-image builder ──────────────────────────────────────────────────────
def _align4(n: int) -> int:
    return (n + 3) & ~3


def build_stage1(code: bytes = b"", *, header_format_v: bool = True,
                 secure_id: int | None = None, vendor_id: int = 0,
                 cap1: int = 0, cap2: int = 0, uid3: int = 0,
                 code_relocs: list[int] | None = None,
                 code_base: int = 0x8000, stack_size: int | None = None) -> bytes:
    """Assemble a Stage-1 image: no imports, no relocations, no exports.

    Layout: [124 header][uncompressed_size u32][V-section 28 if V]
            [code (4-aligned)][import section: one u32 == 0].

    Security fields (V-format only): `secure_id` (defaults to UID3), `vendor_id`,
    and the capability bitmask `cap1`/`cap2`. EKA2L1 wires these straight from the
    header into the running process's security_info (libmanager.cpp:228), so they
    are observable via `process_security_info` (SVC 0xAF). On a real device these
    are what the loader/servers enforce and what a signature must authorise; on the
    emulator they are carried and reported but not enforced (gap G4).
    """
    hdr = E32ImageHeader()
    if stack_size is not None:
        hdr.stack_size = stack_size   # bigger stack for deep call chains (e.g. a
                                      # DLL doing mbedTLS handshake work on our thread)
    hdr.uid3 = uid3       # app UID3 / default SID. SWInstall registers the installed
                          # exe by UID3 and rejects a null one ("installation not
                          # possible"); align it with the SID and SIS package UID.
    header_format = 2 if header_format_v else 1
    hdr.flags = FLAG_ABI_EABI | FLAG_EPT_EKA2 | (header_format << 24)

    # A tiny valid ARMv5 stub: MOV r0,#0 ; BX lr. Not executed at Stage 1, but a
    # real (if trivial) entry so the file grows into Stage 2 naturally.
    if not code:
        code = struct.pack("<II", 0xE3A00000, 0xE12FFF1E)  # mov r0,#0 ; bx lr

    prologue = 124 + 4 + (28 if header_format_v else 0)  # header + usize + Vsec
    code_offset = _align4(prologue)
    code_padded = code + b"\x00" * (_align4(len(code)) - len(code))

    import_offset = code_offset + len(code_padded)
    import_section = struct.pack("<I", 0)  # size word only; no DLL blocks

    # Optional code relocation section, placed after the import section. Offsets
    # are relative to code start; the loader fixes each named word by
    # (code_run_addr - code_base) at attach time (codeseg.cpp:366).
    reloc_section = b""
    code_reloc_offset = 0
    if code_relocs:
        code_reloc_offset = import_offset + len(import_section)
        reloc_section = build_reloc_section(code_relocs, RELOC_TEXT)

    hdr.code_base = code_base
    hdr.code_offset = code_offset
    hdr.code_size = len(code_padded)
    hdr.text_size = len(code_padded)      # all text, no IAT (no imports)
    # entry_point is an OFFSET into the code section, NOT code_base. Resolved
    # from EKA2L1 source (glass box): for a RAM-loaded exe, the primary thread's
    # initial PC = code_chunk_base + header.entry_point
    #   process.cpp:114  create_prim_thread(..., codeseg->get_entry_point(this), ...)
    #   codeseg.cpp:640  get_entry_point -> code_chunk->base(pr) + ep
    #   libmanager.cpp:216  info.entry_point = img->header.entry_point   (verbatim)
    #   thread.cpp:171   ctx.set_pc(entry_point); sp=stack_top; r1=stack_top; r4=0
    # Our stub is the first instruction in the code section, so the offset is 0.
    hdr.entry_point = 0                   # offset of entry within code (spec §7)
    hdr.dll_ref_table_count = 0
    hdr.import_offset = import_offset
    hdr.code_reloc_offset = code_reloc_offset
    hdr.data_reloc_offset = 0
    hdr.export_dir_offset = 0
    hdr.export_dir_count = 0

    body = bytearray()
    body += hdr.pack()
    body += struct.pack("<I", 0)          # uncompressed_size (ignored when raw)
    if header_format_v:
        body += VSection(secure_id=hdr.uid3 if secure_id is None else secure_id,
                         vendor_id=vendor_id, cap1=cap1, cap2=cap2).pack()
    # pad to code_offset (in case alignment inserted a gap)
    body += b"\x00" * (code_offset - len(body))
    body += code_padded
    body += import_section
    body += reloc_section

    # Patch uncompressed_size to total size (cosmetic; parser ignores when raw).
    struct.pack_into("<I", body, 124, len(body))
    return bytes(body)


def build_dll(code: bytes, exports: list[int], *,
              uid2: int = 0, uid3: int = 0, entry_point: int = 0,
              secure_id: int | None = None, vendor_id: int = 0,
              cap1: int = 0, cap2: int = 0,
              code_relocs=None, data: bytes = b"", data_base: int = 0,
              bss_size: int = 0, data_relocs=None, imports=None,
              code_base: int = 0x8000,
              code_reloc_types=None, data_reloc_types=None,
              exe: bool = False, stack_size: int | None = None,
              heap_min: int | None = None, heap_max: int | None = None,
              priority: int | None = None, cpu: int = CPU_ARMV5,
              imp_elf: bool | None = None, module_version: int = 0x000A0000,
              exports_in_code: bool = False, exception_descriptor: int = 0) -> bytes:
    """Assemble a DLL E32Image with an export table (P1a).

    Delta vs. build_stage1 (an EXE), per docs/track-u/tls-p1-scope.md §DLL delta
    and the EKA2L1 loader (e32img.cpp / libmanager.cpp):
      * uid1 = UID1_DLL (0x10000079) — the sole DLL discriminator EKA2L1 uses;
        the UID checksum is recomputed from it.
      * flags |= FLAG_DLL (bit 0).
      * Export table: a flat array of `export_dir_count` little-endian u32 CODE
        OFFSETS (base 0 — i.e. byte offsets into the code section), ordinal-1
        indexed. The loader adds code_base to each (libmanager.cpp:223) and
        lookup(ord) returns code_chunk_base + offset (codeseg.cpp:648/675).

    `exports[i]` is the code-section byte offset of the function exported as
    ordinal i+1. `entry_point` is the E32Dll entry offset (a trivial return-0
    stub is fine; there is no static init here).

    Layout: [124 header][usize u32][V-section 28][code (4-aligned)]
            [import section: one u32 == 0][code relocs?][export table N*u32].

    With `exe=True` this becomes the EXE-with-imports/exports variant (M7:
    EFILE.EXE, TARGETTYPE exexp): uid1 = UID1_EXE, no FLAG_DLL, and the process
    fields (`stack_size`/`heap_min`/`heap_max`/`priority`, from the .mmp) apply.
    Everything else — import blocks, export table, relocs — is byte-identical
    mechanism; the loader paths differ only in process creation.
    """
    hdr = E32ImageHeader()
    hdr.cpu = cpu                   # CPU_X86 for Path C (amd64/x32) images
    hdr.module_version = module_version
    hdr_exception_descriptor = exception_descriptor
    hdr.uid1 = UID1_EXE if exe else UID1_DLL
    hdr.uid2 = uid2
    hdr.uid3 = uid3
    # The ABI and entry-point-type fields are ARM-only: F32's loader (f32image.h ValidateHeader)
    # rejects a non-ARM image that sets either with KErrNotSupported. XIP ROM images never meet that
    # check (rombuild writes its own ROM flags), so x86 images carried the ARM bits unnoticed until the
    # first RAM-loaded one (X4.37).
    # And the import FORMAT: our import blocks list the offsets of import words in the code (the
    # veneers' slots) -- ELF-derived imports. Flagged PE (0), the loader instead looks for a PE import
    # address table at iTextSize and rejects the image (f32image.h ValidateImports). x86 only for now:
    # ARM images are equally ELF-style, but the parked ARM line and the EKA2L1-era stages were proven
    # with the old flags (XIP never reads them) -- flip them when ARM next RAM-loads an image.
    arm_bits = 0 if cpu == CPU_X86 else FLAG_ABI_EABI | FLAG_EPT_EKA2
    # imp_elf=True: an ARM image a REAL loader reads (the 701: emitter/build_mmp.py `device` components)
    imp_fmt = FLAG_IMPFMT_ELF if (cpu == CPU_X86 if imp_elf is None else imp_elf) else 0
    hdr.flags = arm_bits | imp_fmt | FLAG_HDRFMT_V | (0 if exe else FLAG_DLL)
    if stack_size is not None:
        hdr.stack_size = stack_size
    if heap_min is not None:
        hdr.heap_size_min = heap_min
    if heap_max is not None:
        hdr.heap_size_max = heap_max
    if priority is not None:
        hdr.priority = priority

    prologue = 124 + 4 + 28               # header + usize + V-section
    code_offset = _align4(prologue)

    # Imports: place `ordinal | (adj<<16)` into each named code word so the code
    # and the import table agree; the loader overwrites it with export_addr+adj.
    code = bytearray(code)
    dll_ref_table_count = 0
    text_len = None
    if exports_in_code and exports:
        # A REAL loader (f32image.h ValidateHeader) wants the export directory INSIDE the code section, as elf2e32
        # lays it out: [text][export dir]. Its entries are link-time addresses, and for an ELF-import-format image
        # the loader leaves them to the code relocations (sf_lepoc.cpp RelocateExports) -- so each gets one.
        # (EKA2L1-era images keep the old trailing table: that loader rebases exports itself, relocs would double it.)
        text_len = _align4(len(code))
        code += b"\x00" * (text_len - len(code))
        exp_at = len(code)
        code += struct.pack(f"<{len(exports)}I", *(code_base + off for off in exports))
        extra = {exp_at + 4 * i: RELOC_TEXT for i in range(len(exports))}
        if isinstance(code_reloc_types, dict) and code_reloc_types:
            code_reloc_types = {**code_reloc_types, **extra}
        else:
            code_relocs = sorted(set(code_relocs or []) | set(extra))
            code_reloc_types = None
    if imports:
        for _dll, entries in imports:
            for (off, ordn, adj) in entries:
                struct.pack_into("<I", code, off, (ordn & 0xFFFF) | ((adj & 0xFFFF) << 16))
    code_padded = bytes(code) + b"\x00" * (_align4(len(code)) - len(code))

    import_offset = code_offset + len(code_padded)
    if imports:
        import_section, dll_ref_table_count = build_import_section(imports)
        # Pad to 4 so following sections (relocs/exports) stay word-aligned; the
        # size word inside counts only real content, so trailing pad is ignored.
        import_section += b"\x00" * (_align4(len(import_section)) - len(import_section))
    else:
        import_section = struct.pack("<I", 0)  # size word only; no DLL blocks
    cursor = import_offset + len(import_section)

    # Data section (.data initialised bytes; .bss is bss_size zero-fill at run
    # time). EKA2L1 creates a fresh RW chunk for a loaded DLL (codeseg.cpp:285)
    # and relocates .data references by (data_run - data_base), so data_base must
    # be the link-time .data VMA.
    data_offset = 0
    data_padded = data + b"\x00" * (_align4(len(data)) - len(data))
    if data:
        data_offset = cursor
        cursor += len(data_padded)

    code_reloc_offset = 0
    code_reloc_section = b""
    if code_relocs:
        code_reloc_offset = cursor
        # A code word may point into DATA/.bss (e.g. a global's absolute address);
        # such a reloc needs the DATA delta, not TEXT. Honor per-offset types when
        # provided (from elf.extract's classify-by-target-section).
        code_reloc_section = build_reloc_section(
            code_reloc_types if code_reloc_types else code_relocs, RELOC_TEXT)
        cursor += len(code_reloc_section)

    data_reloc_offset = 0
    data_reloc_section = b""
    if data_relocs:
        data_reloc_offset = cursor
        data_reloc_section = build_reloc_section(
            data_reloc_types if data_reloc_types else data_relocs, RELOC_TEXT)
        cursor += len(data_reloc_section)

    export_dir_offset = (code_offset + exp_at) if text_len is not None else cursor
    # Export entries are ABSOLUTE linked code addresses (code_base + offset), as
    # petran/elftran emits them. EKA2L1 only auto-adds code_base for old images
    # (epoc_ver <= eka2, libmanager.cpp:219); our V-format header classifies as
    # epoc94 (> eka2, e32img.cpp:313), so the add is skipped and lookup computes
    # code_chunk_base + entry - code_base. Storing code_base+offset makes that
    # resolve to code_chunk_base + offset. (Confirmed: a bare offset came back as
    # code_chunk_base + offset - code_base, below the segment, and faulted.)
    abs_exports = [code_base + off for off in exports]
    export_table = struct.pack(f"<{len(abs_exports)}I", *abs_exports) if exports and text_len is None else b""

    hdr.code_base = code_base
    hdr.code_offset = code_offset
    hdr.code_size = len(code_padded)
    hdr.text_size = len(code_padded) if text_len is None else text_len   # no IAT -> text == code (less exports)
    hdr.entry_point = entry_point
    hdr.dll_ref_table_count = dll_ref_table_count
    hdr.import_offset = import_offset
    hdr.data_base = data_base
    hdr.data_offset = data_offset
    hdr.data_size = len(data)             # initialised .data size (unpadded)
    hdr.bss_size = bss_size
    hdr.code_reloc_offset = code_reloc_offset
    hdr.data_reloc_offset = data_reloc_offset
    hdr.export_dir_offset = export_dir_offset if exports else 0
    hdr.export_dir_count = len(exports)

    body = bytearray()
    body += hdr.pack()
    body += struct.pack("<I", 0)          # uncompressed_size (patched below)
    body += VSection(secure_id=hdr.uid3 if secure_id is None else secure_id,
                     vendor_id=vendor_id, cap1=cap1, cap2=cap2, exception_des=hdr_exception_descriptor).pack()
    body += b"\x00" * (code_offset - len(body))
    body += code_padded
    body += import_section
    body += data_padded
    body += code_reloc_section
    body += data_reloc_section
    body += export_table

    struct.pack_into("<I", body, 124, len(body))
    set_header_crc(body)
    return bytes(body)


# ── iHeaderCrc (f32image.h ValidateHeader: Mem::Crc32 over the header, iCodeOffset bytes, with the
# field itself = KImageCrcInitialiser). Mem::Crc32 (us_func.cpp) is the reflected 0xEDB88320 table
# with the caller's initial value (0) and no final inversion -- zlib's crc32 with both inversions
# undone. Only the loader's RAM-load path checks it; the ROM never did (X4.37). ──
KIMAGE_CRC_INITIALISER = 0xC90FDAA2


def mem_crc32(data: bytes, crc: int = 0) -> int:
    return zlib.crc32(data, crc ^ 0xFFFFFFFF) ^ 0xFFFFFFFF


def set_header_crc(image: bytearray) -> None:
    """Fill iHeaderCrc (@0x14) in place; call it LAST, after any header field is patched."""
    code_offset = struct.unpack_from("<I", image, 0x64)[0]
    struct.pack_into("<I", image, 0x14, KIMAGE_CRC_INITIALISER)
    struct.pack_into("<I", image, 0x14, mem_crc32(bytes(image[:code_offset])))


def build_exe(code: bytes, exports: list[int] | None = None, **kw) -> bytes:
    """EXE-with-imports/exports (TARGETTYPE exexp) — build_dll with exe=True."""
    return build_dll(code, list(exports or []), exe=True, **kw)


COMPRESSION_BYTE_PAIR = 0x102822AA  # compress_type::byte_pair_c (e32img.cpp:45)
COMPRESSION_DEFLATE = 0x101F7AFC    # compress_type::deflate_c


def compress_e32(image: bytes) -> bytes:
    """Convert an uncompressed E32Image into a byte-pair-compressed one.

    The emulator (parse_e32img, e32img.cpp:398) reads the first `code_offset`
    bytes verbatim (header + usize + V-section = the prologue), then treats the
    remainder as two consecutive byte-pair sections: the code section
    (`code_size` bytes) followed by "the rest" (imports/relocs/…). It sizes the
    decompressed image as `uncompressed_size + code_offset`, so the usize field
    (@124) must be everything after the prologue.

    We split the already-built uncompressed image on exactly those boundaries,
    compress each section, and patch compression_type (@28) + usize (@124).
    """
    from bytepair import write_section

    code_size = struct.unpack_from("<I", image, 48)[0]
    code_offset = struct.unpack_from("<I", image, 100)[0]

    prologue = image[:code_offset]
    payload = image[code_offset:]
    code = payload[:code_size]
    rest = payload[code_size:]

    out = bytearray(prologue)
    out += write_section(code)
    out += write_section(rest)
    struct.pack_into("<I", out, 28, COMPRESSION_BYTE_PAIR)   # compression_type
    struct.pack_into("<I", out, 124, len(payload))           # uncompressed_size
    set_header_crc(out)                                      # the header changed: CRC last
    return bytes(out)


def decompress_e32(image: bytes) -> bytes:
    """Inverse of compress_e32: expand a byte-pair E32Image to its raw form.

    Mirrors parse_e32img's decompress path (e32img.cpp:398). Use it as an RE
    lens on real firmware: a real on-disk Symbian binary is byte-pair (or
    deflate) compressed — feed it here to recover the true code + import +
    relocation bytes for inspection. Returns the raw image with compression_type
    cleared. Byte-pair only (deflate raises).
    """
    from bytepair import read_section

    ctype = struct.unpack_from("<I", image, 28)[0]
    if ctype == 0:
        return image
    if ctype == 0x101F7AFC:   # KUidCompressionDeflate
        from e32deflate import inflate
        code_offset = struct.unpack_from("<I", image, 100)[0]
        usize = struct.unpack_from("<I", image, 124)[0]
        body = inflate(image, code_offset, usize)
        if len(body) != usize:
            raise ValueError(f"deflate: got {len(body)} bytes, header says {usize}")
        prologue = bytearray(image[:code_offset])
        struct.pack_into("<I", prologue, 28, 0)
        struct.pack_into("<I", prologue, 124, 0)
        return bytes(prologue) + body
    if ctype != COMPRESSION_BYTE_PAIR:
        raise NotImplementedError(f"compression_type 0x{ctype:08X} unsupported")

    code_size = struct.unpack_from("<I", image, 48)[0]
    code_offset = struct.unpack_from("<I", image, 100)[0]
    usize = struct.unpack_from("<I", image, 124)[0]

    prologue = bytearray(image[:code_offset])
    code, off = read_section(image, code_size, code_offset)
    rest, _ = read_section(image, usize - code_size, off)
    struct.pack_into("<I", prologue, 28, 0)   # clear compression_type
    struct.pack_into("<I", prologue, 124, 0)  # usize meaningless when raw
    return bytes(prologue) + code + rest


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        # RE helper: `py e32image.py decompress <in.e32> [out.e32]` expands a
        # real (compressed) firmware binary so its bytes can be inspected.
        if sys.argv[1] == "decompress":
            raw = decompress_e32(open(sys.argv[2], "rb").read())
            out = sys.argv[3] if len(sys.argv) > 3 else sys.argv[2] + ".raw"
            open(out, "wb").write(raw)
            print(f"Decompressed -> {out} ({len(raw)} bytes)")
            raise SystemExit(0)
        # `py e32image.py compress <in.e32> <out.e32>`: byte-pair compress (Proteus P2 test images)
        if sys.argv[1] == "compress":
            comp = compress_e32(open(sys.argv[2], "rb").read())
            open(sys.argv[3], "wb").write(comp)
            print(f"Compressed -> {sys.argv[3]} ({len(comp)} bytes)")
            raise SystemExit(0)
    img = build_stage1()
    print(f"Stage-1 image: {len(img)} bytes")
    print("header check =", hex(uid_checksum(UID1_EXE, 0, 0)))
