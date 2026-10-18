#!/usr/bin/env python3
# rombuild.py -- the Symbian XIP ROM writer.
#
# Layout produced (all addresses linear = rom_base + file_offset):
#   [0 .. 512)         TRomHeader: the bootstrap supplies the reset/restart vectors and
#                      reserves the 512-byte header (TheRomHeader in bootmain.s); we fill
#                      iRomBase/iRomSize/iRomRootDirectoryList/iPrimaryFile/iTime/iCheckSum.
#   [512 .. )          rest of the bootstrap boot code (XIP at rom_base).
#   then, 4-aligned:   each XIP file = 120-byte TRomImageHeader + payload (the kernel, the variant
#                      extension, our E32Images prelinked with their imports resolved).
#   then:              the directory tree (rom_dir blocks) and root_dir_list.
#   iCheckSum is computed so the ROM's 32-bit word sum == 0 (e32rom.h iCheckSum semantics).
#
# Usage:  python3 tools/rombuild.py <bootstrap.bin> <out.rom> --primary ekern.bin --kinfo ... --variant ...
#             --xip 'Sys\Bin\x.dll=emitter/x.dll' --data 'Z-path=host-path' ... (or @argsfile)

import argparse
import os
import struct
import sys
import time

UID1_EXE = 0x1000007A
FILE_ATTRIB_DIR = 0x10
XIP_HDR_SIZE = 120          # TRomImageHeader, epoc10
ROM_HEADER_SIZE = 512       # TRomHeader_sz (h2inc)

# TRomHeader field offsets (h2inc of e32rom.h)
H_iTime = 128
H_iRomBase = 140
H_iRomSize = 144
H_iRomRootDirectoryList = 148
H_iPrimaryFile = 160
H_iSecondaryFile = 164      # -> TRomEntry of the first USER process (normally EFILE.EXE);
                            # exstart.cpp SecondaryInfo reads it via Epoc::RomHeader().iSecondaryFile
H_iCheckSum = 168
H_iUncompressedSize = 244   # bootstrap's "Running from RAM" path reads ROM size from here
H_iPageableRomStart = 328   # 0 = not a pageable ROM (fall back to iUncompressedSize)
H_iKernDataAddress = 152    # RAM VMA the kernel .data/.bss are linked at + copied to
H_iKernelLimit = 156        # top of kernel data region
H_iTotalSvDataSize = 200    # total kernel .data + .bss (4K-rounded)
H_iVariantFile = 204        # -> TRomEntry of the variant extension DLL
H_iTraceMask = 256          # copied to super-page iDebugMask; enables __KTRACE_OPT(cat)
H_iUserDataAddress = 220    # multiple-MM Mmu::DoInit2 asserts this == iDllDataBase+iMaxDllDataSize
H_iTotalUserDataSize = 224  # must be page-aligned (0 is fine); ROM DLL static-data size

# Multiple memory model (ARM) user-data base: iDllDataBase + iMaxDllDataSize resolves to
# KUserSharedDataEnd1GB (== KUserSharedDataBase2GB) = 0x40000000 for both the 1GB and 2GB user
# splits (mmboot.h). Mmu::DoInit2 (multiple/mmu.cpp:182) panics ERomUserDataAddressInvalid
# (Kern::Fault "MMU" reason 9) unless the ROM header carries this exact value.
KUserDataAddress_MultipleMM = 0x40000000


def _exe_limits(img):
    """The stack/heap sizes an EXE's TRomImageHeader carries: the image's own (the mmp's EPOCSTACKSIZE/EPOCHEAPSIZE)."""
    return dict(stack_size=img.stack or 0x2000, heap_min=img.heap_min or 0x1000, heap_max=img.heap_max or 0x100000)


def uid_checksum(uid1, uid2, uid3):
    """The Symbian UID checksum (odd/even byte interleave rotate)."""
    b = struct.pack("<III", uid1 & 0xFFFFFFFF, uid2 & 0xFFFFFFFF, uid3 & 0xFFFFFFFF)
    even = bytes(b[i] for i in range(0, 12, 2))
    odd = bytes(b[i] for i in range(1, 12, 2))

    def crc(data):
        c = 0
        for byte in data:
            c ^= byte << 8
            for _ in range(8):
                c = ((c << 1) ^ 0x1021) & 0xFFFF if (c & 0x8000) else (c << 1) & 0xFFFF
        return c
    return (crc(even) | (crc(odd) << 16)) & 0xFFFFFFFF


def align4(ba):
    while len(ba) % 4:
        ba.append(0)


class RomBuilder:
    def __init__(self, bootstrap: bytes, rom_base: int):
        self.rom_base = rom_base
        self.rom = bytearray(bootstrap)
        align4(self.rom)
        self.primary_addr = 0        # primary's TRomImageHeader linear address
        self.primary_entry_addr = 0  # primary's TRomEntry linear address (-> iPrimaryFile)
        self.secondary_addr = 0      # secondary (first user EXE) TRomImageHeader linear address
        self.secondary_entry_addr = 0  # secondary's TRomEntry linear address (-> iSecondaryFile)

    def lin(self, off):
        return self.rom_base + off

    def add_xip_file(self, payload: bytes, *, uid1=UID1_EXE, uid2=0, uid3=0, entry_off=0,
                     is_primary=False, place_at=None, entry_abs=None, code_size=None,
                     data_addr=0, data_size=0, bss_size=0, stack_size=0x2000,
                     heap_min=0x1000, heap_max=0x100000, data_bss_lin_base=0,
                     priority=0x200, cap1=0, cap2=0,
                     export_count=0, export_addr=0, secure_id=0, vendor_id=0,
                     dll_ref_table=0, extra_flags=0, exc_desc=0, module_version=0):
        """Append a TRomImageHeader + payload; return its linear address and total size.

        For a real kernel: place_at pins the header linear address (the ROM is padded to it,
        since the kernel is not position-independent); entry_abs is the absolute entry point
        (_E32Startup); code_size is the execute-in-place ROM part (text+rodata, < len(payload)
        when .data trails); data_addr = ROM LMA of .data; data_size/bss_size drive the
        bootstrap's .data copy + .bss zero.
        """
        if place_at is not None:
            target = place_at - self.rom_base
            while len(self.rom) < target:
                self.rom.append(0)
        align4(self.rom)
        addr = self.lin(len(self.rom))
        code_addr = addr + XIP_HDR_SIZE
        total = XIP_HDR_SIZE + len(payload)
        csz = code_size if code_size is not None else len(payload)
        hdr = bytearray(XIP_HDR_SIZE)
        struct.pack_into("<21I", hdr, 0,
                         uid1, uid2, uid3, uid_checksum(uid1, uid2, uid3),
                         entry_abs if entry_abs is not None else code_addr + entry_off,
                         code_addr,               # code_address
                         data_addr,               # data_address (ROM LMA of .data)
                         csz,                      # code_size (execute-in-place part)
                         csz,                      # text_size
                         data_size, bss_size,
                         heap_min, heap_max,
                         stack_size,
                         dll_ref_table,           # dll_ref_table_address (TDllRefTable, or 0)
                         export_count,            # export_dir_count
                         export_addr,             # export_dir_address
                         secure_id, vendor_id, cap1, cap2)   # SSecurityInfo
        struct.pack_into("<BBH", hdr, 84, 1, 0, 0)          # major, minor, build
        struct.pack_into("<8I", hdr, 88,
                         0x20 | extra_flags,  # @88 iFlags: KRomImageEpt_Eka2 (CHECK_ROM_ENTRY_POINT)
                                    #      + KRomImageFlagData/DataInit/DataPresent (ROM DLL WSD)
                         priority,  # @92  iPriority: must be a valid TProcessPriority for a USER
                                    #      EXE (DProcess::ConvertPriority; e.g. EPriorityForeground
                                    #      =350) or DProcess::Create returns KErrArgument. Ignored
                                    #      for the kernel.
                         data_bss_lin_base,  # @96  iDataBssLinearBase
                         0,        # @100 iNextExtension
                         0,        # @104 iHardwareVariant
                         data_size + bss_size,  # @108 iTotalDataSize (data+bss + DLL reserve).
                                    # MUST be 0 when the EXE has no data/bss, else pcodeseg.cpp:236
                                    # (user proc) faults K::EProcessDataAddressInvalid=KERN 77 when
                                    # iDataBssRunAddress != iDataBssLinearBase. (Was wrongly the
                                    # image size; harmless for the kernel, fatal for a user EXE.)
                         module_version,  # @112 iModuleVersion: the E32 header's (major<<16|minor). The loader matches
                                    #      versioned imports (name{000a0000}.dll = 10.0 or later, every SDK app) against it;
                                    #      0 made each such import "not found" and the process was killed at load (X-plore)
                         # @116 iExceptionDescriptor: the E32 header's (code offset | 1) as an address -- the C++ unwinder
                         # (drtaeabi, UserSvr::ExceptionDescriptor) finds a ROM image's .ARM.exidx through it
                         (code_addr + exc_desc) if exc_desc else 0)
        self.rom += hdr
        self.rom += payload
        if is_primary:
            self.primary_addr = addr
        return addr, total

    # kernel-data ROM header fields (set for a real ekern primary)
    total_user_data = 0      # ROM DLL .data+.bss, page-rounded per DLL (prelink_e32)
    kern_data_base = 0
    kern_data_limit = 0
    total_sv_data = 0
    variant_entry_addr = 0   # the variant file's TRomEntry (-> TRomHeader.iVariantFile)
    variant_img_addr = 0     # the variant file's TRomImageHeader
    trace_mask = 0           # TRomHeader.iTraceMask (KBOOT trace etc.)

    def add_variant_file(self, export_addrs, entry_abs, hw_variant=0x01000000,
                         uid3=0x100039E8):
        """Append a minimal variant TRomImageHeader whose export table points at ekern's
        statically-linked variant functions (ordinal N = export_addrs[N-1]). The kernel's
        A::CreateVariant reads iExportDir[0] (VariantInitialise) to create the Asic."""
        align4(self.rom)
        addr = self.lin(len(self.rom))              # TRomImageHeader linear address
        expdir = addr + XIP_HDR_SIZE                # export table sits right after the header
        hdr = bytearray(XIP_HDR_SIZE)
        struct.pack_into("<21I", hdr, 0,
                         0x10000079, 0, uid3, uid_checksum(0x10000079, 0, uid3),  # uid1=DLL
                         entry_abs,                 # entry_point (_variant_e32dll)
                         expdir,                    # code_address (nominal)
                         0, 0, 0, 0, 0,             # data_addr, code/text/data/bss sizes
                         0x1000, 0x100000, 0x2000,  # heap_min/max, stack
                         0,                         # dll_ref_table_address
                         len(export_addrs),         # export_dir_count  (@60)
                         expdir,                    # export_dir_address (@64)
                         0, 0, 0, 0)
        struct.pack_into("<BBH", hdr, 84, 1, 0, 0)
        struct.pack_into("<8I", hdr, 88,
                         0x20,        # iFlags: KRomImageEpt_Eka2 (CHECK_ROM_ENTRY_POINT)
                         0x200,       # priority
                         0,           # data_bss_linear_base_address
                         0,           # next_extension_linear_address
                         hw_variant,  # iHardwareVariant (@104; top byte<=3 -> compatible)
                         XIP_HDR_SIZE + len(export_addrs) * 4,  # total_size
                         0, 0)
        self.rom += hdr
        for a in export_addrs:
            self.rom += struct.pack("<I", a)
        self.variant_img_addr = addr
        return addr

    def _dir_block(self, entries):
        """entries: list of (name, addr_lin, size, is_dir). Returns the packed rom_dir.

        REAL TRomDir format (e32rom.h + srom/sr_rom.cpp -- the fileserver's rom fs
        binary-searches these, found live at M7 Fault(38) EMainScanMediaDriverDirOpen):
          int32 iSize            bytes of the TRomEntry region ONLY (not this field)
          TRomEntry iEntry...    each {i32 size, u32 addr_lin, u8 att, u8 nameLen,
                                 utf16 name}, 4-aligned
          TRomDirSortInfo        at &iEntry + align4(iSize):
              u16 iSubDirCount, u16 iFileCount,
              u16 subdir_offsets[iSubDirCount]   (entry_off_from_first_entry / 4)
              u16 file_offsets[iFileCount]
          each table sorted ascending by CRomMountCB::Compare (ASCII fold to
          lowercase over the UTF-16 units, ties broken shorter-first).
        """
        body = bytearray()
        offsets = []                       # byte offset of each entry within the block
        for name, addr_lin, size, is_dir, attrib in entries:
            offsets.append(4 + len(body))  # +4 for the leading int32 dir size
            nm = name.encode("utf-16-le")
            if is_dir:
                attrib = FILE_ATTRIB_DIR
            body += struct.pack("<IIBB", size, addr_lin, attrib, len(name))
            body += nm
            while len(body) % 4:
                body.append(0)

        def sort_key(i):
            name = entries[i][0]
            folded = tuple(ord(c.lower() if 'A' <= c <= 'Z' else c) for c in name)
            return (folded, len(name))
        subdirs = sorted((i for i, e in enumerate(entries) if e[3]), key=sort_key)
        files   = sorted((i for i, e in enumerate(entries) if not e[3]), key=sort_key)
        sort_info = struct.pack("<HH", len(subdirs), len(files))
        for i in subdirs + files:
            sort_info += struct.pack("<H", (offsets[i] - 4) // 4)  # scaled from first entry
        while len(sort_info) % 4:
            sort_info += b"\0"
        return struct.pack("<i", len(body)) + body + sort_info, offsets

    def add_tree(self, files):
        """files: list of (z_path, addr_lin, size[, attrib]). Builds the tree bottom-up.

        z_path like 'Sys\\Bin\\ekern.exe' (no drive).  Returns root_dir_list lin addr.
        attrib defaults to ReadOnly|XIP (0x81, an XIP E32Image -- without KEntryAttXIP
        the loader ignores the ROM address, RFile::Att gate in sf_cache.cpp); plain
        data files (e.g. estart.txt) pass 0x01 (ReadOnly).
        """
        # build a nested dict:  name -> either ('file', addr, size, attrib) or subdict
        root = {}
        for entry in files:
            path, addr, size = entry[0], entry[1], entry[2]
            attrib = entry[3] if len(entry) > 3 else 0x81
            parts = path.replace("/", "\\").strip("\\").split("\\")
            node = root
            for d in parts[:-1]:
                # ROM names are case-insensitive: `resource\apps` must merge into an
                # existing `Resource` dir (two entries folding equal = one is unreachable)
                d = next((k for k in node if k.lower() == d.lower()
                          and isinstance(node[k], dict)), d)
                node = node.setdefault(d, {})
            node[parts[-1]] = ("file", addr, size, attrib)

        def emit(node):
            """Emit sub-dir blocks first (need their addrs), then this dir block. Returns addr."""
            entries = []
            for name, val in sorted(node.items()):
                if isinstance(val, tuple):
                    entries.append((name, val[1], val[2], False, val[3]))
                else:
                    sub_addr = emit(val)
                    # size of a dir entry's target is unused by the parser; use its block size
                    entries.append((name, sub_addr, 0, True, 0))
            block, offsets = self._dir_block(entries)
            align4(self.rom)
            addr = self.lin(len(self.rom))
            self.rom += block
            # iPrimaryFile must point at the primary file's TRomEntry (its iAddressLin@4
            # then points at the TRomImageHeader), not at the header itself.
            for (name, tgt, size, is_dir, _att), off in zip(entries, offsets):
                if not is_dir and self.primary_addr and tgt == self.primary_addr:
                    self.primary_entry_addr = addr + off
                if not is_dir and tgt == getattr(self, "variant_img_addr", 0) and self.variant_img_addr:
                    self.variant_entry_addr = addr + off
                if not is_dir and self.secondary_addr and tgt == self.secondary_addr:
                    self.secondary_entry_addr = addr + off
            return addr

        root_addr = emit(root)
        align4(self.rom)
        rdl_addr = self.lin(len(self.rom))
        self.rom += struct.pack("<iII", 1, 0, root_addr)   # num_root=1, hw_variant, addr
        return rdl_addr

    def finalize(self, root_dir_list_addr):
        align4(self.rom)
        # iTime is a TTimeK: microseconds since midnight 1 Jan year 0 AD (Symbian epoch),
        # NOT the Unix epoch. P::InitSystemTime falls back to this ROM timestamp when the RTC
        # read fails and passes it to K::SecondsFrom2000, which range-checks (result/1e6 -
        # Year2000InSeconds) against +/-TInt; a Unix-epoch value underflows -> KErrOverflow ->
        # PP::Panic(EInitialSystemTimeInvalid) = Kern::Fault("PLAT", 0). Convert to the year-0
        # epoch. Year2000InSeconds = KDaysFrom0ADTo2000AD(730497) * 86400; Unix time of
        # 2000-01-01T00:00:00Z = 946684800.
        K_YEAR2000_IN_SECONDS = 730497 * 86400
        K_UNIX_2000 = 946684800
        itime_symbian = (K_YEAR2000_IN_SECONDS + (int(time.time()) - K_UNIX_2000)) * 1_000_000
        struct.pack_into("<q", self.rom, H_iTime, itime_symbian)
        struct.pack_into("<I", self.rom, H_iRomBase, self.rom_base)
        # A whole number of pages, as a real rombuild image: the bootstrap's ROM bank size (-> the super page's
        # iTotalRomSize) follows iRomSize, and the FLEXIBLE memory model's DRomMemoryManager::Init3 claims that
        # many bytes of ROM pages -- a non-page-multiple is KErrArgument -> FAULT mrom.cpp:475 (PinePhone SMP rung).
        self.rom.extend(b"\0" * (-len(self.rom) % 0x1000))
        struct.pack_into("<I", self.rom, H_iRomSize, len(self.rom))
        struct.pack_into("<I", self.rom, H_iUncompressedSize, len(self.rom))
        struct.pack_into("<I", self.rom, H_iPageableRomStart, 0)
        struct.pack_into("<I", self.rom, H_iRomRootDirectoryList, root_dir_list_addr)
        struct.pack_into("<I", self.rom, H_iPrimaryFile, self.primary_entry_addr)
        if self.kern_data_base:
            struct.pack_into("<I", self.rom, H_iKernDataAddress, self.kern_data_base)
            struct.pack_into("<I", self.rom, H_iKernelLimit, self.kern_data_limit)
            struct.pack_into("<I", self.rom, H_iTotalSvDataSize, self.total_sv_data)
            # user-data area (multiple MM Mmu::DoInit2 consistency asserts). ROM DLL static data
            # is laid out DOWNWARD from iUserDataAddress (prelink_e32); the kernel reserves
            # iTotalUserDataSize (page multiple) at the top of every process's DLL-data chunk
            struct.pack_into("<I", self.rom, H_iUserDataAddress,
                             getattr(self, "user_data_top", KUserDataAddress_MultipleMM))
            struct.pack_into("<I", self.rom, H_iTotalUserDataSize, self.total_user_data)
        if self.variant_entry_addr:
            struct.pack_into("<I", self.rom, H_iVariantFile, self.variant_entry_addr)
        if self.secondary_entry_addr:
            struct.pack_into("<I", self.rom, H_iSecondaryFile, self.secondary_entry_addr)
        if self.trace_mask:
            struct.pack_into("<I", self.rom, H_iTraceMask, self.trace_mask)
        struct.pack_into("<I", self.rom, H_iCheckSum, 0)
        s = sum(struct.unpack_from("<%dI" % (len(self.rom) // 4), self.rom, 0)) & 0xFFFFFFFF
        struct.pack_into("<I", self.rom, H_iCheckSum, (-s) & 0xFFFFFFFF)
        return bytes(self.rom)


# ── XIP prelink: place our E32Images in ROM with imports resolved ────────────
# What real rombuild does for XIP files: each emitted E32Image (emitter/) is
# assigned its final ROM address, its relocations are applied (TEXT += the
# code delta, DATA += the data delta), and every import slot -- the veneer's
# `.word ordinal` (emitter/devlink.py) -- is overwritten with the resolved
# export's ROM address. At runtime nothing is left to fix up: the images are
# plain execute-in-place ROM code calling each other at absolute addresses.
# (euser/efsrv/hal have ZERO static data, so no ROM-DLL data machinery needed;
# the EXE's .data/.bss are handled by the kernel's XIP process-create path via
# the TRomImageHeader fields, like the kernel's own data.)

E32_UID1_DLL = 0x10000079
RELOC_TEXT_NIBBLE = 0x1000
RELOC_DATA_NIBBLE = 0x2000


class E32Xip:
    """A parsed, uncompressed E32Image ready for XIP placement."""

    def __init__(self, path, zname):
        self.path, self.zname = path, zname
        d = open(path, "rb").read()
        if struct.unpack_from("<I", d, 28)[0] != 0:
            raise ValueError(f"{path}: compressed E32Image not supported for XIP")
        g = lambda off: struct.unpack_from("<I", d, off)[0]
        self.uid1, self.uid2, self.uid3 = g(0), g(4), g(8)
        self.is_exe = self.uid1 != E32_UID1_DLL
        self.code_size, self.data_size = g(48), g(52)
        self.heap_min, self.heap_max, self.stack = g(56), g(60), g(64)
        self.bss_size, self.entry_off = g(68), g(72)
        self.link_code_base, self.link_data_base = g(76), g(80)
        ref_count, exp_off, self.export_count = g(84), g(88), g(92)
        code_off, data_off, imp_off = g(100), g(104), g(108)
        creloc_off, dreloc_off = g(112), g(116)
        self.priority = struct.unpack_from("<H", d, 120)[0]
        # E32ImageHeaderV SSecurityInfo @128: the SID must survive into the
        # TRomImageHeader -- ECOM rejects a plug-in whose SID != its UID3
        # (CDllData::SaveSecurityInfoL), and servers police clients by SID.
        self.secure_id, self.vendor_id = g(128), g(132)
        self.module_version = g(24)  # E32ImageHeader iModuleVersion
        # the ABI bits (KImageABIMask == KRomImageABIMask 0x18; EABI = 0x08): the loader refuses an explicit RLibrary::Load of a
        # DLL whose ABI differs from the process (sf_lepoc.cpp ABIFAIL) -- with 0 here every ROM DLL was 'GCC98r2' and an SDK
        # (EABI) app's RLibrary::Load("z:euser") got KErrNotSupported (X-plore, 2026-10-12)
        self.abi_flags = g(44) & 0x18
        self.exc_desc = g(144)       # E32ImageHeaderV iExceptionDescriptor (code offset | 1; 0 = no unwind tables)
        self.code = bytearray(d[code_off:code_off + self.code_size])
        self.data = bytearray(d[data_off:data_off + self.data_size] if data_off else b"")
        self.exports = list(struct.unpack_from(f"<{self.export_count}I", d, exp_off)
                            ) if self.export_count else []
        self.code_relocs = self._parse_relocs(d, creloc_off)
        self.data_relocs = self._parse_relocs(d, dreloc_off)
        self.imports = self._parse_imports(d, imp_off, ref_count)
        # assigned at layout time:
        self.hdr_addr = self.code_addr = self.data_lma = self.export_addr = 0
        self.payload = b""
        # ROM DLL writable static data (prelink_e32): run address, "has data" closure, and the
        # TDllRefTable = the direct dependencies the loader must build code segments for
        self.own_wsd = self.data_present = False
        self.data_run = self.ref_table_addr = 0
        self.ref_deps = []
        # What the linker assumed about placement: the ELF link base is 0x8000 (16-aligned), so each image's code sits
        # on a 16-byte boundary (hand-written NEON/aligned-load code needs it). Costs <= 12 bytes of padding per image.
        self.code_align, self.data_align = 16, 4

    @staticmethod
    def _parse_relocs(d, off):
        if not off:
            return []
        # E32RelocSection.iSize is the size of the BLOCKS, not counting its own 8-byte header (elf2e32
        # e32imagefile.cpp, F32's ValidateRelocations). The emitter counted the header until X4.37, and
        # images of both kinds are about, so walk by the relocation COUNT, which both agree on.
        _size, total = struct.unpack_from("<II", d, off)
        out, p = [], off + 8
        while len(out) < total:
            base, bsize = struct.unpack_from("<II", d, p)
            assert bsize >= 8, f"bad relocation block at {p:#x}"
            for k in range((bsize - 8) // 2):
                (info,) = struct.unpack_from("<H", d, p + 8 + 2 * k)
                if info:
                    out.append((base + (info & 0xFFF), info & 0xF000))
            p += bsize
        return out

    @staticmethod
    def _parse_imports(d, off, ref_count):
        blocks, p = [], off + 4
        for _ in range(ref_count):
            name_off, n = struct.unpack_from("<II", d, p)
            p += 8
            offs = list(struct.unpack_from(f"<{n}I", d, p))
            p += 4 * n
            nm = d[off + name_off:d.index(b"\x00", off + name_off)].decode()
            blocks.append((nm, offs))
        return blocks

    def export_run(self, e):
        """The run-time address of an export given at its link address. An exported STATIC (S4.2: Qt 4 exports
        QListData::shared_null & co, which its inline code refcounts) lives in the DLL's data, so it moves with the
        DATA delta -- to the DLL's one data address in RAM -- not with the code: given the code delta, the entry
        pointed at the initial copy of the data inside the ROM image and the first write faulted."""
        dsz = self.data_size + self.bss_size
        if dsz and self.data_run and self.link_data_base <= e < self.link_data_base + dsz:
            return e - self.link_data_base + self.data_run
        return e - self.link_code_base + self.code_addr

    def resolve(self, by_name, data_run_base):
        """Apply relocs + imports for the assigned addresses; build payload."""
        dcode = self.code_addr - self.link_code_base
        ddata = (data_run_base - self.link_data_base) if self.data_size or self.bss_size else 0
        # Relocation is a SHIFT, so it preserves alignment only when the shift is a multiple of it; checked here
        # rather than assumed.
        if dcode % self.code_align:
            raise ValueError("%s: code placed at 0x%08X, %d off the %d-byte alignment its "
                             "link base 0x%08X assumed"
                             % (self.zname, self.code_addr, dcode % self.code_align,
                                self.code_align, self.link_code_base))
        if ddata % self.data_align:
            raise ValueError("%s: static data placed at 0x%08X, %d off the %d-byte alignment "
                             "its link base 0x%08X assumed"
                             % (self.zname, data_run_base, ddata % self.data_align,
                                self.data_align, self.link_data_base))

        def fix(buf, off, typ):
            (w,) = struct.unpack_from("<I", buf, off)
            w = (w + (dcode if typ == RELOC_TEXT_NIBBLE else ddata)) & 0xFFFFFFFF
            struct.pack_into("<I", buf, off, w)

        for off, typ in self.code_relocs:
            fix(self.code, off, typ)
        for off, typ in self.data_relocs:
            fix(self.data, off, typ)
        for dll, offs in self.imports:
            key = dll.split("{")[0].lower()
            tgt = by_name.get(key)
            if tgt is None:
                raise ValueError(f"{self.zname}: imports {dll} which is not in the ROM bundle")
            for off in offs:
                (w,) = struct.unpack_from("<I", self.code, off)
                ordn, adj = w & 0xFFFF, w >> 16
                if adj >= 0x8000:
                    adj -= 0x10000      # S4.3: a signed 16-bit addend (Qt: &QVectorData::shared_null - 8)
                if not 1 <= ordn <= tgt.export_count:
                    raise ValueError(f"{self.zname}: ordinal {ordn} out of range for {dll}")
                # export entries are ABSOLUTE link-space addresses (code_base+off)
                exp = tgt.export_run(tgt.exports[ordn - 1])
                struct.pack_into("<I", self.code, off, (exp + adj) & 0xFFFFFFFF)

        parts = bytes(self.code)
        if self.data:
            parts += b"\x00" * (-len(parts) % 4) + bytes(self.data)
        if self.exports:
            reb = [self.export_run(e) & 0xFFFFFFFF for e in self.exports]
            parts += b"\x00" * (-len(parts) % 4)
            self.export_addr = self.code_addr + len(parts)
            parts += struct.pack(f"<{len(reb)}I", *reb)
        if self.ref_deps:
            # TDllRefTable { TUint16 iFlags; TUint16 iNumberOfEntries; TRomImageHeader* iEntry[]; }
            parts += b"\x00" * (-len(parts) % 4)
            assert self.ref_table_addr == self.code_addr + len(parts), self.zname
            parts += struct.pack(f"<HH{len(self.ref_deps)}I", 0, len(self.ref_deps),
                                 *[d.hdr_addr for d in self.ref_deps])
        self.payload = parts


LAST_XIP_MAP = []


def prelink_e32(rom_builder, imgs, exe_data_run_base, data_top=KUserDataAddress_MultipleMM):
    """Assign ROM addresses for the E32 bundle (sequential append), resolve, and
    return per-image (payload, fields) ready for add_xip_file. Layout must match
    exactly what add_xip_file will do: align4 -> 120-byte header -> payload."""
    by_name = {}
    for img in imgs:
        by_name[img.zname.rsplit("\\", 1)[-1].lower()] = img
    # --- ROM DLL writable static data (what real rombuild does; e32rom.h / sf_lepoc.cpp) ---
    # A DLL with .data/.bss gets a fixed run address carved DOWNWARD from 0x40000000 and
    # KRomImageFlagData. "DataPresent" = self or any dependency has data: the loader skips
    # code-segment creation for a ROM DLL WITHOUT it (E32Image::AlwaysLoaded) and walks
    # iDllRefTable for the rest, so every image lists its DataPresent direct dependencies;
    # the kernel then commits + initialises the data per process (DEpocCodeSeg::InitData).
    top = data_top
    for img in imgs:
        img.deps = [d for d in (by_name.get(n.split("{")[0].lower()) for n, _ in img.imports)
                    if d is not None and d is not img]
        img.own_wsd = (not img.is_exe) and (len(img.data) + img.bss_size) > 0
        img.data_present = img.own_wsd
    wsd = [img for img in imgs if img.own_wsd]
    rom_builder.user_data_top = data_top
    for img in wsd:
        top -= (len(img.data) + img.bss_size + 0xFFF) & ~0xFFF
        img.data_run = top
    rom_builder.total_user_data = data_top - top
    changed = True
    while changed:                                   # closure over the (cyclic) import graph
        changed = False
        for img in imgs:
            if not img.data_present and any(d.data_present for d in img.deps):
                img.data_present = changed = True
    for img in imgs:
        img.ref_deps = [d for d in img.deps if d.data_present]
    cur = len(rom_builder.rom)
    for img in imgs:
        cur = (cur + 3) & ~3
        # An image keeps its INTERNAL alignment only if it lands congruent to its link base. The XIP header is 120
        # bytes, so pad it back so the CODE, not the header, is aligned.
        a = img.code_align
        while (rom_builder.lin(cur) + XIP_HDR_SIZE - img.link_code_base) % a:
            cur += 4
        img.hdr_addr = rom_builder.lin(cur)
        img.code_addr = img.hdr_addr + XIP_HDR_SIZE
        # payload = code [+ pad + data] [+ pad + export table]
        plen = len(img.code)
        if img.data:
            plen += (-plen % 4)
            img.data_lma = img.code_addr + plen
            plen += len(img.data)
        if img.exports:
            plen += (-plen % 4) + 4 * len(img.exports)
        if img.ref_deps:
            plen += (-plen % 4)
            img.ref_table_addr = img.code_addr + plen
            plen += 4 + 4 * len(img.ref_deps)
        cur += XIP_HDR_SIZE + plen
    for img in imgs:
        img.resolve(by_name, exe_data_run_base if img.is_exe else img.data_run)
    # placement map for tools/romsym.py: name, ROM code addr, code len, link base
    global LAST_XIP_MAP
    LAST_XIP_MAP = [(img.zname.rsplit("\\", 1)[-1].lower(), img.code_addr,
                            len(img.code), img.link_code_base) for img in imgs]


def build_kernel_rom(bootstrap, rom_base, kernel_bin, k, variant=None, xip_e32=None, data_files=None):
    """Build a ROM with a real ekern primary. k = dict of ints (see --kinfo).
    variant = (entry_abs, [export_addrs]) to also add the variant extension DLL.
    xip_e32 = ([E32Xip...], xk) to XIP-prelink our emitted E32Images into the ROM (imports
    resolved at build time; the EXE among them becomes iSecondaryFile); xk keys: databss,
    cap1, cap2."""
    b = RomBuilder(bootstrap, rom_base)
    b.trace_mask = k.get("trace_mask", 0)
    total_sv = (k["data_size"] + k["bss_size"] + 0xFFF) & ~0xFFF   # 4K-round data+bss
    b.kern_data_base = k["kern_data_base"]
    b.total_sv_data = total_sv
    # kernel region = data/bss + stack + heap headroom; a generous limit for first boot.
    # MUST be rounded UP to a 1MB (chunk) boundary: MmuBase::CreateKernelSection uses
    # iKernelLimit VERBATIM as the kernel linear-section base (TLinearSection::New does NOT
    # align it), and the section allocator hands out chunk-aligned addresses as base+r*1MB.
    # If iKernelLimit is only 4K-aligned, every kernel chunk is skewed by that sub-1MB
    # remainder, and ArmMmu::MapRamPages -- which indexes PTEs from the 1MB boundary
    # (offset>>12 & 0xFF) -- installs each chunk's PTEs at the wrong page-table slot, so the
    # chunk's own base VA lands on an unmapped PTE and the first access data-aborts.
    # The kernel heap max = the primary's iHeapSizeMax (sinit.cpp Mutate). The region must hold data/bss + stack +
    # the whole heap.
    kheap_max = k.get("heap_max", 0x02000000)
    b.kern_data_limit = (k["kern_data_base"] + total_sv + k.get("stack", 0x2000) + kheap_max + 0x400000 + 0xFFFFF) & ~0xFFFFF
    addr, size = b.add_xip_file(
        # ekern is an executable image: UID1 must be KExecutableImageUidValue (0x1000007A) or
        # DCodeSeg::Create rejects the kernel codeseg with KErrNotSupported (scodeseg.cpp:677) ->
        # InitialiseMicrokernel returns -5 -> KernelMain faults EInitMicrokernelFailed(50).
        kernel_bin, uid1=k.get("uid1", UID1_EXE), uid3=k.get("uid3", 0x00000000),
        is_primary=True, place_at=k["place_at"], entry_abs=k["entry"],
        code_size=k["code_size"], data_addr=k["data_lma"], data_size=k["data_size"],
        bss_size=k["bss_size"], stack_size=k.get("stack", 0x2000),
        heap_min=0x1000, heap_max=kheap_max,
        data_bss_lin_base=k["kern_data_base"])
    files = [("Sys\\Bin\\ekern.exe", addr, size)]
    if variant is not None:                       # variant = (entry_abs, [export_addrs])
        v_entry, v_exports = variant
        v_addr = b.add_variant_file(v_exports, v_entry)
        files.append(("Sys\\Bin\\kasyborg.dll", v_addr, XIP_HDR_SIZE + len(v_exports) * 4))
    if xip_e32 is not None:                       # xip_e32 = ([E32Xip...], xk)
        imgs, xk = xip_e32
        databss = xk.get("databss", 0x00400000)   # multiple-MM iUserLocalBase
        prelink_e32(b, imgs, databss)
        for img in imgs:
            is_exe = img.is_exe
            addr, size = b.add_xip_file(
                img.payload, place_at=img.hdr_addr,
                uid1=img.uid1, uid2=img.uid2, uid3=img.uid3,
                entry_abs=img.code_addr + img.entry_off,
                code_size=len(img.code),
                data_addr=img.data_lma, data_size=len(img.data),
                bss_size=img.bss_size if (is_exe or img.own_wsd) else 0,
                **_exe_limits(img),
                data_bss_lin_base=databss if is_exe else img.data_run,
                dll_ref_table=img.ref_table_addr,
                # KImageDll: the loader's ONLY "is a DLL" test for a ROM image (E32Image::Construct)
                # -- without it a dependency is code-seg-created as an EXE and the load fails
                extra_flags=(0 if is_exe else 0x00000001)
                            | img.abi_flags                              # KRomImageABIMask (E32 iFlags bits 3-4)
                            | (0x04000000 if img.own_wsd else 0)        # KRomImageFlagData
                            | (0x03000000 if img.data_present else 0),  # ...DataInit|DataPresent
                priority=(img.priority or 350) if is_exe else 0x200,
                cap1=xk.get("cap1", 0xFFFFFFFF), cap2=xk.get("cap2", 0xFFFFFFFF),
                export_count=img.export_count, export_addr=img.export_addr,
                secure_id=img.secure_id, vendor_id=img.vendor_id, exc_desc=img.exc_desc,
            module_version=img.module_version)
            assert addr == img.hdr_addr, \
                f"{img.zname}: layout drift 0x{addr:X} != 0x{img.hdr_addr:X}"
            # Only the FILE SERVER is the ROM secondary (iSecondaryFile -> the first
            # USER process the kernel launches). The kernel's SecondaryInfo hard-codes
            # iFileName="z:\sys\bin\efile.exe", so iSecondaryFile must point at efile.
            # Other EXEs in the bundle (e.g. ESTART.EXE) are ordinary ROM files that
            # the fileserver's loader launches later -- they must NOT become the
            # secondary, or the kernel boots them instead of the fileserver.
            if is_exe and img.zname.lower().replace("/", "\\").endswith("\\efile.exe"):
                b.secondary_addr = addr
            files.append((img.zname, addr, size))
    for zname, payload in (data_files or []):
        # plain data file (e.g. estart.txt): raw bytes, no image header, attrib
        # ReadOnly only (NOT XIP -- it is not an E32Image).
        align4(b.rom)
        d_addr = b.lin(len(b.rom))
        b.rom += payload
        files.append((zname, d_addr, len(payload), 0x01))
    rdl = b.add_tree(files)
    return b.finalize(rdl)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Symbian XIP ROM writer",
                                 fromfile_prefix_chars="@")   # @file: one argument per line
    ap.add_argument("bootstrap", help="raw bootstrap binary (objcopy -O binary)")
    ap.add_argument("out", help="output .rom path")
    ap.add_argument("--rom-base", default="0x80000000")
    ap.add_argument("--primary", required=True, help="raw binary of the primary (ekern) payload")
    ap.add_argument("--kinfo", required=True, help="the primary: comma-separated k=v ints "
                    "(place_at,entry,code_size,data_lma,data_size,bss_size,kern_data_base,"
                    "stack,heap_max,uid1,uid3,trace_mask). Values accept 0x hex.")
    ap.add_argument("--variant", help="add the variant DLL: 'entry=0x..,exports=0xa:0xb:..' "
                    "(entry=_variant_e32dll, exports=ekern variant-function addrs, ord 1..N).")
    ap.add_argument("--xip", action="append", default=[],
                    help="XIP-prelink an emitted E32Image into the ROM: 'Z-path=e32-path', "
                    r"e.g. 'Sys\Bin\euser.dll=emitter/euser_dev.dll'. Repeatable. Imports "
                    "resolve by ordinal against the bundle; efile.exe becomes iSecondaryFile.")
    ap.add_argument("--xinfo", help="XIP bundle params: comma-separated k=v ints (databss,cap1,cap2).")
    ap.add_argument("--data", action="append", default=[],
                    help="add a plain data file: 'Z-path=host-path', e.g. "
                    r"'Sys\Data\estart.txt=path/to/estart.txt'. Repeatable.")
    args = ap.parse_args(argv)

    rom_base = int(args.rom_base, 0)
    bootstrap = open(args.bootstrap, "rb").read()
    primary = open(args.primary, "rb").read()
    k = {kv.split("=")[0]: int(kv.split("=")[1], 0) for kv in args.kinfo.split(",")}
    variant = None
    if args.variant:
        vd = {kv.split("=")[0]: kv.split("=")[1] for kv in args.variant.split(",")}
        variant = (int(vd["entry"], 0), [int(x, 0) for x in vd["exports"].split(":")])
    xip_e32 = None
    if args.xip:
        imgs = []
        for spec in args.xip:
            zname, _, path = spec.partition("=")
            imgs.append(E32Xip(path, zname))
        xk = {}
        if args.xinfo:
            xk = {kv.split("=")[0]: int(kv.split("=")[1], 0) for kv in args.xinfo.split(",")}
        xip_e32 = (imgs, xk)
    data_files = []
    for spec in args.data:
        zname, _, path = spec.partition("=")
        data_files.append((zname, open(path, "rb").read()))
    rom = build_kernel_rom(bootstrap, rom_base, primary, k, variant, xip_e32, data_files)
    open(args.out, "wb").write(rom)
    print("wrote %s: %d bytes, rom_base=0x%08X" % (args.out, len(rom), rom_base))
    if LAST_XIP_MAP:
        with open(args.out + ".map", "w") as fh:
            for name, addr, size, link in LAST_XIP_MAP:
                fh.write("%s 0x%08X 0x%X 0x%X\n" % (name, addr, size, link))
    return 0


if __name__ == "__main__":
    sys.exit(main())
