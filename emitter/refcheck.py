"""Reference checker — a faithful port of EKA2L1's E32Image acceptance logic.

Mirrors `parse_e32img` / `parse_e32img_header` / `is_e32img` /
`validate_e32img_layout` from EKA2L1 @47b4ad76a (src/emu/loader/src/e32img.cpp),
so we can confirm an emitted image would be accepted WITHOUT building EKA2L1.

This is a checking aid, not a substitute for the real loader: it reproduces the
parser's accept/reject decisions for the uncompressed path. Final confirmation
still comes from the real emulator/harness. Keep this in sync with the spec.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from e32image import E32IMG_SIGNATURE, uid_checksum

# error codes (utils/err.h semantics; we only care about none vs corrupt here)
ERR_NONE = 0
ERR_CORRUPT = -20  # value irrelevant; we branch on == ERR_NONE


@dataclass
class CheckResult:
    ok: bool
    stage: str                 # where it would have failed / "accepted"
    messages: list[str]
    fields: dict


def _u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def check_image(data: bytes) -> CheckResult:
    msgs: list[str] = []
    f: dict = {}

    # ── is_e32img / header sig+checksum (parse_e32img_header) ────────────────
    if len(data) < 124:
        return CheckResult(False, "size", [f"file is {len(data)} bytes, < 124 header"], f)

    uid1, uid2, uid3, check, sig = struct.unpack_from("<IIIII", data, 0)
    f.update(uid1=uid1, uid2=uid2, uid3=uid3, check=check, sig=sig)

    if sig != E32IMG_SIGNATURE:
        return CheckResult(False, "signature",
                           [f"sig=0x{sig:08X} != 0x{E32IMG_SIGNATURE:08X} ('EPOC')"], f)
    msgs.append(f"sig OK ('EPOC')")

    want = uid_checksum(uid1, uid2, uid3)
    if check != want:
        return CheckResult(False, "uid_checksum",
                           [f"check=0x{check:08X} != computed 0x{want:08X}"], f)
    msgs.append(f"UID checksum OK (0x{check:08X})")

    # ── EKA1 vs modern branch (e32img.cpp:265) ───────────────────────────────
    temp = _u32(data, 24)
    f["module_version_word"] = temp
    if temp in (0x1000, 0x2000):
        return CheckResult(False, "eka1_landmine",
                           [f"bytes@24 = 0x{temp:08X} → parsed as ancient EKA1, "
                            f"not modern. Must avoid 0x1000/0x2000."], f)
    msgs.append(f"modern-image branch (word@24 = 0x{temp:08X})")

    # modern path reads the full 124-byte header struct
    (compression_type,) = struct.unpack_from("<I", data, 28)
    flags = _u32(data, 44)
    code_size = _u32(data, 48)
    entry_point = _u32(data, 72)
    dll_ref_table_count = _u32(data, 84)
    export_dir_offset = _u32(data, 88)
    export_dir_count = _u32(data, 92)
    text_size = _u32(data, 96)
    code_offset = _u32(data, 100)
    import_offset = _u32(data, 108)
    code_reloc_offset = _u32(data, 112)
    data_reloc_offset = _u32(data, 116)
    cpu = struct.unpack_from("<H", data, 122)[0]
    f.update(compression_type=compression_type, flags=flags, code_size=code_size,
             entry_point=entry_point, dll_ref_table_count=dll_ref_table_count,
             export_dir_offset=export_dir_offset, export_dir_count=export_dir_count,
             text_size=text_size, code_offset=code_offset, import_offset=import_offset,
             code_reloc_offset=code_reloc_offset, data_reloc_offset=data_reloc_offset,
             cpu=cpu)

    header_format = (flags >> 24) & 0xF
    f["header_format"] = header_format
    msgs.append(f"flags=0x{flags:08X} header_format={header_format} "
                f"(ABI={'EABI' if (flags>>3)&3==1 else 'GCC98r2'}, "
                f"EPT={'EKA2' if (flags>>5)&7 else 'EKA1'})")

    if compression_type != 0:
        # our refcheck only models the uncompressed path
        msgs.append(f"NOTE: compression_type=0x{compression_type:08X} (not modelled)")

    # In the uncompressed path, data.size() == file size (e32img.cpp:442-448).
    size = len(data)

    # ── validate_e32img_layout (e32img.cpp:173) ──────────────────────────────
    def within(what: str, off: int, length: int) -> bool:
        if off + length > size:
            msgs.append(f"LAYOUT FAIL: {what} at {off} spans {length}, past {size}")
            return False
        return True

    if text_size > code_size:
        return CheckResult(False, "layout",
                           msgs + [f"text_size {text_size} > code_size {code_size}"], f)
    if not within("code", code_offset, code_size):
        return CheckResult(False, "layout", msgs, f)
    if export_dir_offset != 0 and not within("export dir", export_dir_offset, export_dir_count * 4):
        return CheckResult(False, "layout", msgs, f)
    if dll_ref_table_count != 0 and not within("import section", import_offset, 4):
        return CheckResult(False, "layout", msgs, f)
    if code_reloc_offset != 0 and not within("code relocs", code_reloc_offset, 8):
        return CheckResult(False, "layout", msgs, f)
    if data_reloc_offset != 0 and not within("data relocs", data_reloc_offset, 8):
        return CheckResult(False, "layout", msgs, f)
    msgs.append("layout bounds OK")

    # Import section is read unconditionally (e32img.cpp:462); with
    # dll_ref_table_count==0 no blocks are walked. Just sanity-check the read.
    if import_offset + 4 <= size:
        (imp_size,) = struct.unpack_from("<I", data, import_offset)
        msgs.append(f"import section size word = {imp_size} "
                    f"({dll_ref_table_count} DLL block(s))")
    else:
        msgs.append("import section size word out of range (harmless when count==0)")

    return CheckResult(True, "accepted", msgs, f)


def format_result(r: CheckResult) -> str:
    lines = [("[ACCEPTED]" if r.ok else f"[REJECTED] at: {r.stage}")]
    lines += [f"  - {m}" for m in r.messages]
    lines.append("  fields: " + ", ".join(
        f"{k}=0x{v:X}" if isinstance(v, int) else f"{k}={v}"
        for k, v in r.fields.items()))
    return "\n".join(lines)
