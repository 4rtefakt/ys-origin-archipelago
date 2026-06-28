"""Disassembler for Ys Origin's XSO event-script bytecode -> g_flags grant dump.

Extracts, offline, which ``g_flags[]`` index each chest/event writes and to what
value — i.e. the flag-index <-> item/location map that otherwise needs a play-
through. Pair with ``tools/ni_unpack.py`` (which extracts the ``.XSO`` files).

Container (``XSR\\0``), reverse-engineered from the VM (``FUN_004472e0``) and
validated against extracted scripts:

  * 0x24-byte header. uint32 at **+0x1C = code length in words**; code starts at
    **+0x24**. A label table follows the code (not needed here).
  * Code is a stream of 32-bit words. ``class = word >> 24``:
      0       nop                         len 1
      1       end/return                  len 1
      2       function (sub-op switch)    len 1 + ((w>>8)&0xf) + (w&0xff)
              sub-op = (w>>12)&0xfff; operands start at +1+((w>>8)&0xf),
              count = (w&0xff). Operands are int32 immediates.
      3,0xb   jump                        len 2
      4       reg op (imm)                len 2
      5..0xa  conditional jump            len 2
      0xc,0xd reg = 0                     len 1
      0xe     reg = imm                   len 2
      0xf..13 reg <op>= imm               len 2
  * The grant is **class 2, sub-op 100 (0x64): g_flags[op0] = op1** (set index
    to immediate). 0x65 copies a flag, 0x66 stores the accumulator, 0x67.. do
    arithmetic. Indices < 0x200 are g_flags; >= 0x200 are script-local vars.

Run from repo root:
    python -m tools.xso_dis <file.XSO>               # disassemble one script
    python -m tools.xso_dis <dir> --grants [out.csv] # dump grants under a tree
"""

from __future__ import annotations

import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client.offsets import ITEM_OFFSETS, LOCATION_FLAG_OFFSETS  # noqa: E402

GFLAGS_OFFSET = 0x36B91C
GFLAGS_COUNT = 0x200  # indices < 0x200 are g_flags; >= are script-local vars

# Class-2 sub-ops that write a g_flags entry, and how to describe them.
STORE_OPS = {
    0x64: "set",        # g_flags[op0] = op1            (immediate)  <- main grant
    0x65: "copy",       # g_flags[op0] = g_flags[op1]
    0x66: "set_reg",    # g_flags[op0] = accumulator
    0x67: "add",        # g_flags[op0] += op1
    0x69: "sub",        # g_flags[op0] -= op1
    0x6B: "mul",        # g_flags[op0] *= op1
    0x71: "and",
    0x73: "or",
    0x75: "xor",
    0x77: "zero",       # g_flags[op0] = 0
}


def _idx_names() -> dict[int, str]:
    names: dict[int, str] = {}
    for nm, off in ITEM_OFFSETS.items():
        names[(off - GFLAGS_OFFSET) // 4] = nm
    for nm, off in LOCATION_FLAG_OFFSETS.items():
        names[(off - GFLAGS_OFFSET) // 4] = nm
    return names


IDX_NAMES = _idx_names()

# Per-class instruction length in words (class 2 is computed, not in this table).
_FIXED_LEN = {
    0: 1, 1: 1, 3: 2, 4: 2, 5: 2, 6: 2, 7: 2, 8: 2, 9: 2, 0xA: 2,
    0xB: 2, 0xC: 1, 0xD: 1, 0xE: 2, 0xF: 2, 0x10: 2, 0x11: 2, 0x12: 2, 0x13: 2,
}


@dataclass
class Instr:
    pc: int               # word index within the code section
    word: int
    cls: int
    sub: Optional[int]    # class-2 sub-op, else None
    operands: List[int]
    length: int


class XSO:
    def __init__(self, data: bytes, name: str = "?"):
        self.name = name
        if data[:4] != b"XSR\0":
            raise ValueError(f"{name}: not an XSR/XSO script")
        self.code_words = struct.unpack_from("<I", data, 0x1C)[0]
        code_bytes = self.code_words * 4
        start = 0x24
        end = start + code_bytes
        if end > len(data):
            raise ValueError(f"{name}: code region {end} exceeds file {len(data)}")
        self.code = list(struct.unpack(f"<{self.code_words}i",
                                       data[start:end]))

    def disasm(self) -> Iterator[Instr]:
        pc = 0
        n = self.code_words
        while pc < n:
            w = self.code[pc] & 0xFFFFFFFF
            cls = w >> 24
            if cls == 2:
                argoff = 1 + ((w >> 8) & 0xF)
                nargs = w & 0xFF
                length = argoff + nargs
                ops = self.code[pc + argoff: pc + argoff + nargs]
                yield Instr(pc, w, cls, (w >> 12) & 0xFFF, ops, length)
            else:
                length = _FIXED_LEN.get(cls)
                if length is None:
                    # Unknown class: stop (avoids desync); report via caller.
                    yield Instr(pc, w, cls, None, [], 1)
                    return
                yield Instr(pc, w, cls, None, self.code[pc + 1: pc + length],
                            length)
            pc += length

    def grants(self) -> List[Tuple[str, int, Optional[int]]]:
        """Return (op_name, flag_idx, value) for every g_flags store.

        ``value`` is ``None`` when it isn't a plain immediate (copy/set_reg).
        Only indices < 0x200 (real g_flags) are returned.
        """
        out: List[Tuple[str, int, Optional[int]]] = []
        for ins in self.disasm():
            if ins.cls != 2 or ins.sub not in STORE_OPS:
                continue
            if not ins.operands:
                continue
            idx = ins.operands[0]
            if not (0 <= idx < GFLAGS_COUNT):
                continue
            op = STORE_OPS[ins.sub]
            val = ins.operands[1] if (op in ("set", "add", "sub", "mul")
                                      and len(ins.operands) > 1) else None
            if op == "zero":
                val = 0
            out.append((op, idx, val))
        return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _xso_files(root: Path) -> List[Path]:
    """All .xso files under root, deduped (Windows globbing is case-insensitive)."""
    seen: dict[str, Path] = {}
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() == ".xso":
            seen[str(p).lower()] = p
    return sorted(seen.values())


def _annot(idx: int) -> str:
    nm = IDX_NAMES.get(idx)
    return f"  [{nm}]" if nm else ""


def cmd_disasm(path: Path) -> int:
    xso = XSO(path.read_bytes(), path.name)
    print(f"{path.name}: {xso.code_words} code words")
    for ins in xso.disasm():
        if ins.cls == 2:
            extra = f" sub=0x{ins.sub:X} ops={ins.operands}"
            note = ""
            if ins.sub in STORE_OPS and ins.operands:
                note = f"   STORE {STORE_OPS[ins.sub]} g_flags[0x{ins.operands[0]:X}]" \
                       + _annot(ins.operands[0])
        else:
            extra = f" ops={ins.operands}"
            note = ""
        print(f"  {ins.pc:4d}: 0x{ins.word:08X} class={ins.cls:<2}{extra}{note}")
    return 0


def cmd_grants(root: Path, out_csv: Optional[Path]) -> int:
    import csv
    files = _xso_files(root)
    rows = []
    errors = 0
    for f in files:
        try:
            xso = XSO(f.read_bytes(), str(f.relative_to(root)))
        except Exception as e:  # noqa: BLE001
            errors += 1
            continue
        rel = str(f.relative_to(root)).replace("\\", "/")
        scene = rel.split("/")[1] if rel.upper().startswith("MAP/") else ""
        for op, idx, val in xso.grants():
            rows.append((scene, rel, f"0x{idx:X}", idx, op,
                         "" if val is None else val, IDX_NAMES.get(idx, "")))

    rows.sort(key=lambda r: (r[0], r[3]))
    if out_csv:
        with out_csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["scene", "script", "flag_idx_hex", "flag_idx",
                        "op", "value", "known_name"])
            w.writerows(rows)
        print(f"  wrote {len(rows)} grant(s) from {len(files)} script(s) "
              f"({errors} unreadable) to {out_csv}")
    else:
        for r in rows[:200]:
            print(f"  {r[0]:8} idx {r[2]:>6} {r[4]:7} val={r[5]:<6} "
                  f"{r[6]}  {r[1]}")
        print(f"  ({len(rows)} grants; {errors} unreadable; "
              "pass an output path to write CSV)")
    return 0


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    target = Path(argv[1])
    if "--grants" in argv:
        i = argv.index("--grants")
        out = Path(argv[i + 1]) if len(argv) > i + 1 else None
        return cmd_grants(target, out)
    return cmd_disasm(target)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
