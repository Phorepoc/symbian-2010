"""euser_def — parse a Symbian EABI .def export map (ordinal -> mangled symbol).

The frozen export ordinals of a Symbian DLL live in its `.def` file; for a GCC
EABI build that is `eabi/<name>u.def`, whose left column is the Itanium-mangled
symbol our own toolchain emits. Example line:

    _ZN10CCirBuffer3PutEi @ 3 NONAME ; CCirBuffer::Put(int)

We parse `<mangled> @ <ordinal>` and ignore the NONAME/flags/comment tail. A
DLL that exports symbol S at ordinal N is a drop-in for callers that bind S by
ordinal, so reproducing this table is what makes our from-source euser a real
euser.dll rather than a bespoke one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_LINE = re.compile(r"^\s*([A-Za-z_][\w.]*)\s+@\s*(\d+)\b")   # some defs write `@4496` (AVKONU.def)


@dataclass
class DefMap:
    by_ordinal: dict[int, str]   # ordinal -> mangled name
    max_ordinal: int
    data_names: frozenset = frozenset()   # exports flagged DATA (vtables, consts)
    # ordinals marked ABSENT: the slot is kept (callers' numbering) but the DLL no longer provides the
    # symbol (it moved to another DLL). Never an import provider.
    absent: frozenset = frozenset()

    def name(self, ordinal: int) -> str | None:
        return self.by_ordinal.get(ordinal)


def parse_def(path: str) -> DefMap:
    by_ordinal: dict[int, str] = {}
    data_names = set()
    absent = set()
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = _LINE.match(line)
            if not m:
                continue
            name, ordinal = m.group(1), int(m.group(2))
            by_ordinal[ordinal] = name
            if re.search(r"\bDATA\b", line.split(";")[0][m.end():]):
                data_names.add(name)
            if re.search(r"\bABSENT\b", line.split(";")[0][m.end():]):
                absent.add(name)
    if not by_ordinal:
        # an EMPTY .def is valid (an EXE that exports nothing: drmhelperserver) -- anything else that
        # yields no ordinals is a misparse
        if re.fullmatch(rb"\s*(EXPORTS\s*)?", open(path, "rb").read()):
            return DefMap({}, 0)
        raise ValueError(f"no exports parsed from {path}")
    return DefMap(by_ordinal, max(by_ordinal), frozenset(data_names), frozenset(absent))
