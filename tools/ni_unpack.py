"""Unpacker for Ys Origin's Falcom NI/NA archives (release/data.ni + data.na).

Format (cracked against v1.1.1.0; matches Kyuuhachi's Ys I/II/Origin/VI tool):

  data.ni  — index. 16-byte header: ``"NNI\\0"`` + uint32 ``n_entries`` +
             uint32 ``names_size`` + uint32 ``flags`` (bit0 = incremental link,
             unsupported). Then an encrypted TOC (``n_entries`` x 16 bytes) and
             an encrypted names blob (``names_size`` bytes). **The two sections
             are encrypted independently — the stream cipher key resets at the
             start of each.**
  cipher   — multiplicative stream cipher, per byte i (0-based):
                 k = (k * 0x3D09) & 0xFFFFFFFF      # k starts at 0x7C53F961
                 plain[i] = (cipher[i] - (k >> 16)) & 0xFF
  TOC entry— 4x uint32: ``hash``, ``size`` (decompressed), ``pos`` (offset in
             data.na), ``namepos`` (offset into names blob).
  names    — NUL-terminated CP932 paths (backslashes; we keep them as-is).
  data.na  — concatenated files at ``pos``. Files whose name ends in ``.z`` are
             zlib with an 8-byte prefix (uint32 CRC32, uint32 uncompressed size)
             then the raw zlib stream; everything else is stored raw.

Usage (from repo root):
    python -m tools.ni_unpack <data.ni> --list [--filter SUBSTR]
    python -m tools.ni_unpack <data.ni> --extract <OUTDIR> [--filter SUBSTR]
    python -m tools.ni_unpack <data.ni> --stats

``--filter`` matches a case-insensitive substring of the (upper-cased) path,
e.g. ``--filter .XSO`` to pull just the event scripts. The trailing ``.z`` is
stripped from extracted filenames (the content is already decompressed).
"""

from __future__ import annotations

import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional

CIPHER_INIT = 0x7C53F961
CIPHER_MUL = 0x3D09
MASK32 = 0xFFFFFFFF


def decrypt(buf: bytes) -> bytes:
    """Decrypt one NI section (cipher state resets per section)."""
    out = bytearray(len(buf))
    k = CIPHER_INIT
    for i, b in enumerate(buf):
        k = (k * CIPHER_MUL) & MASK32
        out[i] = (b - (k >> 16)) & 0xFF
    return bytes(out)


@dataclass
class Entry:
    name: str          # original path, e.g. "MAP\\S_00\\S_0003\\EVT_0001_HUGO.XSO.Z"
    size: int          # decompressed size in bytes
    pos: int           # offset into the .na file
    hash: int

    @property
    def compressed(self) -> bool:
        return self.name.lower().endswith(".z")

    @property
    def clean_name(self) -> str:
        """Path with the trailing .z removed and slashes normalised."""
        n = self.name[:-2] if self.compressed else self.name
        return n.replace("\\", "/")


class NIArchive:
    def __init__(self, ni_path: Path):
        self.ni_path = ni_path
        self.na_path = ni_path.with_suffix(".na")
        if not self.na_path.exists():
            raise FileNotFoundError(f"data file not found: {self.na_path}")
        self.entries: List[Entry] = []
        self._end_by_pos: dict[int, int] = {}
        self._load()

    def _load(self) -> None:
        d = self.ni_path.read_bytes()
        if d[:4] != b"NNI\0":
            raise ValueError(f"not an NNI index: {self.ni_path}")
        n_entries, names_size, flags = struct.unpack_from("<III", d, 4)
        if flags & 0x01:
            raise ValueError("incremental-link NI (flag 0x01) is unsupported")
        toc_bytes = n_entries * 16
        toc = decrypt(d[16:16 + toc_bytes])
        names = decrypt(d[16 + toc_bytes:16 + toc_bytes + names_size])

        def name_at(np: int) -> str:
            end = names.find(b"\0", np)
            return names[np:end].decode("cp932", "replace")

        for i in range(n_entries):
            h, size, pos, namepos = struct.unpack_from("<IIII", toc, i * 16)
            self.entries.append(Entry(name_at(namepos), size, pos, h))

        # Compressed files don't store their packed length; bound each by the
        # next file's start offset (zlib stops at the real stream end anyway).
        na_size = self.na_path.stat().st_size
        positions = sorted({e.pos for e in self.entries})
        nxt = {p: positions[i + 1] if i + 1 < len(positions) else na_size
               for i, p in enumerate(positions)}
        self._end_by_pos = nxt

    # -- access ------------------------------------------------------------- #

    def filtered(self, substr: Optional[str]) -> Iterator[Entry]:
        if not substr:
            yield from self.entries
            return
        s = substr.upper()
        for e in self.entries:
            if s in e.name.upper():
                yield e

    def read(self, entry: Entry, na_fh) -> bytes:
        na_fh.seek(entry.pos)
        if not entry.compressed:
            return na_fh.read(entry.size)
        end = self._end_by_pos.get(entry.pos, entry.pos + entry.size * 4 + 4096)
        blob = na_fh.read(end - entry.pos)
        # 8-byte prefix: CRC32, uncompressed size; then the zlib stream.
        usize = struct.unpack_from("<I", blob, 4)[0]
        raw = zlib.decompressobj().decompress(blob[8:])
        if len(raw) != entry.size:
            # size in TOC should equal both the prefix usize and the real length
            if len(raw) != usize:
                raise ValueError(
                    f"{entry.name}: decompressed {len(raw)} bytes, "
                    f"expected {entry.size} (prefix says {usize})")
        return raw


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def cmd_list(ar: NIArchive, substr: Optional[str]) -> int:
    count = 0
    for e in ar.filtered(substr):
        print(f"  {e.size:>10}  {e.clean_name}")
        count += 1
    print(f"  ({count} file(s))")
    return 0


def cmd_stats(ar: NIArchive) -> int:
    by_ext: dict[str, int] = {}
    for e in ar.entries:
        # real type = extension before the .z
        name = e.clean_name
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else "(none)"
        by_ext[ext] = by_ext.get(ext, 0) + 1
    print(f"  {len(ar.entries)} entries in {ar.ni_path.name}")
    for ext, c in sorted(by_ext.items(), key=lambda x: -x[1])[:40]:
        print(f"    {c:6d}  .{ext}")
    return 0


def cmd_extract(ar: NIArchive, substr: Optional[str], outdir: Path) -> int:
    outdir.mkdir(parents=True, exist_ok=True)
    n = 0
    with self_open(ar) as na_fh:
        for e in ar.filtered(substr):
            data = ar.read(e, na_fh)
            dest = outdir / e.clean_name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            n += 1
            if n % 200 == 0:
                print(f"  ... {n} files")
    print(f"  extracted {n} file(s) to {outdir}")
    return 0


def self_open(ar: NIArchive):
    return open(ar.na_path, "rb")


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    ni_path = Path(argv[1])
    if not ni_path.exists():
        print(f"  index not found: {ni_path}")
        return 1

    substr: Optional[str] = None
    if "--filter" in argv:
        substr = argv[argv.index("--filter") + 1]

    ar = NIArchive(ni_path)

    if "--stats" in argv:
        return cmd_stats(ar)
    if "--extract" in argv:
        outdir = Path(argv[argv.index("--extract") + 1])
        return cmd_extract(ar, substr, outdir)
    # default: list
    return cmd_list(ar, substr)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
