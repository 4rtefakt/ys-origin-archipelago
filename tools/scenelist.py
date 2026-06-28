"""Parse Ys Origin's scene table (``MAP/SCENELIST.SL``) -> scene → room name.

Extract it first from either archive (English for English names)::

    python -m tools.ni_unpack <release/data_us.ni> --filter SCENELIST --extract <dir>

Format (decompressed): 8-byte header (`u32 0`, `u32 count`=0xCD) then fixed
456-byte records. Within the record stream, the scene **path** (e.g.
``s_10\\s_1001``) is a NUL-terminated string at ``+0x1D4 + 456*N`` and the
display **name** (e.g. ``2F Path 1``) at ``+0x294 + 456*N`` (offsets validated by
constant stride across records). The path's leaf (``S_1001``) joins to the
script tree (``MAP/S_10/S_1001/…``) and to the chest catalog's sub-scene.

Names carry the floor (``2F Path 1``, ``4F Lower (Medal)``) and tag bosses /
saves / key rooms — ideal for naming apworld locations without a playthrough.

Usage:
    python -m tools.scenelist <SCENELIST.SL>        # print scene -> name
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict

PATH_OFF = 0x1D4
NAME_OFF = 0x294
STRIDE = 456


def _cstr(data: bytes, off: int) -> str:
    end = data.find(b"\0", off)
    return data[off:end].decode("cp932", "replace")


def _clean_path(s: str) -> str:
    # A 1-byte record field can precede the path; keep from the first alnum.
    m = re.search(r"[A-Za-z0-9_].*", s)
    return (m.group(0) if m else s).replace("\\", "/").rstrip("/")


def parse(path: Path) -> Dict[str, str]:
    """Return ``{scene_leaf_upper: room_name}`` e.g. ``{"S_1001": "2F Path 1"}``.

    Also includes the full key (``"S_10/S_1001"``) for disambiguation.
    """
    data = path.read_bytes()
    out: Dict[str, str] = {}
    n = 0
    while NAME_OFF + STRIDE * n < len(data):
        p = _clean_path(_cstr(data, PATH_OFF + STRIDE * n))
        name = _cstr(data, NAME_OFF + STRIDE * n).strip()
        n += 1
        if not p or not name:
            continue
        out[p.upper()] = name
        leaf = p.upper().rsplit("/", 1)[-1]
        out.setdefault(leaf, name)   # leaf key (S_1001); full key wins on clash
    return out


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    table = parse(Path(argv[1]))
    # Print only the full "x/y" keys, sorted, to avoid leaf duplicates.
    for k in sorted(k for k in table if "/" in k):
        print(f"  {k:18} {table[k]}")
    print(f"  ({sum('/' in k for k in table)} scenes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
