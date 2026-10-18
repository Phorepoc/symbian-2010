"""refcheck -- sanity check of an emitted E32Image: the signature, the UID checksum, the header CRC, and that every
section offset the header names lies inside the file. A guard against a writer bug, not a substitute for the loader."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from e32image import E32IMG_SIGNATURE, KIMAGE_CRC_INITIALISER, mem_crc32, uid_checksum


@dataclass
class CheckResult:
    ok: bool
    messages: list


def _u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def check_image(data: bytes) -> CheckResult:
    msgs = []
    if len(data) < 128:
        return CheckResult(False, ["image shorter than its header"])
    if _u32(data, 16) != E32IMG_SIGNATURE:
        msgs.append("bad signature")
    if _u32(data, 12) != uid_checksum(_u32(data, 0), _u32(data, 4), _u32(data, 8)):
        msgs.append("UID checksum mismatch")
    code_offset = _u32(data, 0x64)
    code_size = _u32(data, 48)
    if code_offset + code_size > len(data):
        msgs.append("code section runs past the end of the file")
    else:
        head = bytearray(data[:code_offset])
        struct.pack_into("<I", head, 0x14, KIMAGE_CRC_INITIALISER)
        if mem_crc32(bytes(head)) != _u32(data, 0x14):
            msgs.append("header CRC mismatch")
    for name, off in (("import", 108), ("data", 104), ("code reloc", 112), ("data reloc", 116), ("export", 88)):
        v = _u32(data, off)
        if v > len(data):
            msgs.append("%s offset 0x%X is outside the file" % (name, v))
    return CheckResult(not msgs, msgs)


def format_result(r: CheckResult) -> str:
    return "[ACCEPTED]" if r.ok else "[REJECTED] " + "; ".join(r.messages)
