"""E32Image emitter: assembles a Symbian E32Image (DLL, or EXE with imports/exports) from the code, data,
relocations and import/export tables elf.extract took from a linked ELF.

Everything little-endian. The image is what the ROM builder (tools/rombuild.py) takes: it prelinks it into the ROM,
applying the relocations and resolving the ordinal imports.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

# ── Constants ────────────────────────────────────────────────────────────────
E32IMG_SIGNATURE = 0x434F5045  # 'EPOC' LE
UID1_EXE = 0x1000007A
UID1_DLL = 0x10000079
CPU_ARMV5 = 0x2001             # e32_cpu::armv5

# flags bitfield
FLAG_DLL = 0x00000001
FLAG_ABI_EABI = 0x1 << 3       # bits 3-4 ABI: 1 = EABI
FLAG_EPT_EKA2 = 0x1 << 5       # bits 5-7 entry-point type: >=1 = EKA2
FLAG_HDRFMT_J = 0x1 << 24      # bits 24-27 header format: 1 = J
FLAG_HDRFMT_V = 0x2 << 24      # bits 24-27 header format: 2 = V
FLAG_IMPFMT_ELF = 0x1 << 28    # bits 28-31 import format: 0 = PE, 1 = ELF, 2 = PE2 (f32image.h)

# Recommended flags for a minimal EABI/EKA2 EXE, V-format, uncompressed.
FLAGS_DEFAULT_EXE = FLAG_ABI_EABI | FLAG_EPT_EKA2 | FLAG_HDRFMT_V  # 0x02000028


# ── UID checksum ──
def _crc_ccitt_table() -> list[int]:
    """CRC-CCITT/XModem table: poly 0x1021, MSB-first, init 0.

    (crc_tab starts 0x0000, 0x1021, 0x2042, 0x3063, ...).
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
    """crc = (crc<<8) ^ tab[((crc>>8) ^ byte) & 0xff]."""
    for b in data:
        crc = ((crc << 8) ^ _CRC_TAB[((crc >> 8) ^ b) & 0xFF]) & 0xFFFF
    return crc


def _checksum_half(uid12: bytes, start: int) -> int:
    """CRC over 6 bytes sampled every other byte from `start`."""
    sampled = uid12[start:12:2]  # start, start+2, ... start+10 (6 bytes)
    return crc16(0, sampled)


def uid_checksum(uid1: int, uid2: int, uid3: int) -> int:
    """(odd_crc << 16) | even_crc over the 12-byte UID block."""
    uid12 = struct.pack("<III", uid1, uid2, uid3)
    even = _checksum_half(uid12, 0)  # bytes 0,2,4,6,8,10
    odd = _checksum_half(uid12, 1)   # bytes 1,3,5,7,9,11
    return ((odd << 16) | even) & 0xFFFFFFFF


# ── Header ───────────────────────────────────────────────────────────────────
@dataclass
class E32ImageHeader:
    """The 124-byte basic header (sizeof == 124, no padding).

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
    priority: int = 350           # process_priority::foreground
    cpu: int = CPU_ARMV5

    def finalize(self) -> "E32ImageHeader":
        if self.check is None:
            self.check = uid_checksum(self.uid1, self.uid2, self.uid3)
        return self

    def pack(self) -> bytes:
        self.finalize()
        # module_version occupies the 4 bytes at offset 24 (struct: major u16,
        # minor u16). We pack it as one LE u32 so callers can reason about the
        # whole word.
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
# then *(code_base_ptr + virtual_addr) += delta, where text delta = code_run_addr - code_base.
RELOC_RESERVED = 0x0000   # also the block padding value
RELOC_TEXT = 0x1000       # fix by (code_run_addr - code_base)
RELOC_DATA = 0x2000       # fix by (data_run_addr - data_base)
RELOC_INFERRED = 0x3000   # loader picks text/data by inspecting the stored value


def build_reloc_section(offsets, rtype: int = RELOC_TEXT) -> bytes:
    """A relocation section.

    Layout: [size u32][num_relocs u32] then per-4KB-page blocks
            [base u32][block_size u32][rel_info u16 ...]. Each block is padded
    with a trailing 0x0000 entry to a 4-byte boundary. Each named offset is a
    byte offset from the section base (code start for a code-reloc section); it
    names a 32-bit word in the loaded image to be fixed up.

    `offsets` is either a list[int] (all fixed up with the uniform `rtype`
    delta-selector nibble) or a dict[int, int] mapping each offset to its own
    nibble (RELOC_TEXT / RELOC_DATA / RELOC_INFERRED). The nibble selects the
    DELTA: TEXT=code_delta, DATA=data_delta, INFERRED=by value.
    The SECTION the word lives in (text vs data base_ptr) is set separately by
    which section this reloc belongs to (code_reloc vs data_reloc) — so a word in
    code that points into data goes in the code-reloc section with a DATA nibble.
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
    # ValidateRelocations measures from the first block).
    return struct.pack("<II", len(blocks), total) + bytes(blocks)


# ── Imports (EKA2 / "elf" form) ──────────────────────────────────────────────
def build_import_section(imports) -> tuple[bytes, int]:
    """Build the EKA2 import section.

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
              priority: int | None = None, module_version: int = 0x000A0000) -> bytes:
    """Assemble a DLL E32Image with an export table.

      * uid1 = UID1_DLL (0x10000079); the UID checksum is recomputed from it.
      * flags |= FLAG_DLL (bit 0).
      * Export table: a flat array of `export_dir_count` little-endian u32 absolute link-space code addresses,
        ordinal-1 indexed (`exports[i]` is the code-section byte offset of ordinal i+1).
      * `entry_point` is the E32Dll entry offset.

    Layout: [124 header][usize u32][V-section 28][code (4-aligned)]
            [import section: one u32 == 0][code relocs?][export table N*u32].

    With `exe=True` this becomes the EXE-with-imports/exports variant (TARGETTYPE exexp): uid1 = UID1_EXE, no
    FLAG_DLL, and the process fields (`stack_size`/`heap_min`/`heap_max`/`priority`, from the .mmp) apply.
    """
    hdr = E32ImageHeader()
    hdr.cpu = CPU_ARMV5
    hdr.module_version = module_version
    hdr.uid1 = UID1_EXE if exe else UID1_DLL
    hdr.uid2 = uid2
    hdr.uid3 = uid3
    # (the import format stays PE-flagged (0): the images are XIP, which never reads it)
    hdr.flags = FLAG_ABI_EABI | FLAG_EPT_EKA2 | FLAG_HDRFMT_V | (0 if exe else FLAG_DLL)
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

    # Data section (.data initialised bytes; .bss is bss_size zero-fill at run time). .data references are
    # relocated by (data_run - data_base), so data_base must be the link-time .data VMA.
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

    export_dir_offset = cursor
    # Export entries are ABSOLUTE linked code addresses (code_base + offset), as petran/elftran emits them.
    abs_exports = [code_base + off for off in exports]
    export_table = struct.pack(f"<{len(abs_exports)}I", *abs_exports) if exports else b""

    hdr.code_base = code_base
    hdr.code_offset = code_offset
    hdr.code_size = len(code_padded)
    hdr.text_size = len(code_padded)   # no IAT -> text == code
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
                     vendor_id=vendor_id, cap1=cap1, cap2=cap2).pack()
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
# undone. Only the loader's RAM-load path checks it; the ROM never did. ──
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
