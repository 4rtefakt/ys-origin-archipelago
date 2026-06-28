"""Parse Ys Origin's item table (``MISC/INVINFO.DAT``) -> id → English name.

Extract it first from the **English** archive::

    python -m tools.ni_unpack <release/data_us.ni> --filter INVINFO --extract <dir>

Format (decompressed): 16-byte header (`hash, hash, uint32 record_size=0xB8,
uint32 count=0x80`) then ``count`` fixed 184-byte records. **The record index is
the item id**, which equals the give-item operand (class-2 sub-op ``0x116``) and
the g_flags item index — verified: id 0x57 Roda Fruit, 0x59 Celcetan Panacea,
0x6B Cerulean Flabellum, 0x6F Blue Moon Crest. Each record starts with a
NUL-terminated ASCII English name (the Shift-JIS JP name and a longer English
description follow later in the record).

Usage:
    python -m tools.invinfo <INVINFO.DAT>            # print id -> name
    python -m tools.invinfo <INVINFO.DAT> --desc     # also print descriptions
"""

from __future__ import annotations

import re
import struct
import sys
from pathlib import Path
from typing import Dict, Tuple

REC_SIZE = 0xB8
HEADER = 0x10

_ASCII_RUN = re.compile(rb"[ -~][ -~\r\n]{5,}")


def _first_string(rec: bytes) -> str:
    return rec.split(b"\0", 1)[0].decode("ascii", "replace").strip()


def _description(rec: bytes, name: str) -> str:
    """Best-effort: the longest printable run that isn't the name/icon tag."""
    best = ""
    for m in _ASCII_RUN.finditer(rec):
        s = m.group().decode("ascii", "replace").replace("\r\n", " ").strip()
        if s == name or re.fullmatch(r"[a-z]{2}_\d+", s):  # skip name / "sw_00"
            continue
        if len(s) > len(best):
            best = s
    return best


def parse(path: Path) -> Dict[int, Tuple[str, str]]:
    """Return ``{item_id: (name, description)}`` from an INVINFO.DAT."""
    data = path.read_bytes()
    _, _, rec_size, count = struct.unpack_from("<IIII", data, 0)
    if rec_size != REC_SIZE:
        # Tolerate variants; trust the header's record size.
        pass
    out: Dict[int, Tuple[str, str]] = {}
    for i in range(count):
        off = HEADER + i * rec_size
        rec = data[off:off + rec_size]
        if len(rec) < rec_size:
            break
        name = _first_string(rec)
        out[i] = (name, _description(rec, name))
    return out


def names(path: Path) -> Dict[int, str]:
    """Just ``{item_id: name}``."""
    return {i: nd[0] for i, nd in parse(path).items()}


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    table = parse(Path(argv[1]))
    show_desc = "--desc" in argv
    for i, (name, desc) in table.items():
        line = f"  0x{i:02X} ({i:3})  {name}"
        if show_desc and desc:
            line += f"\n           {desc}"
        print(line)
    print(f"  ({len(table)} items)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
