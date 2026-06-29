"""Find the player's real (entity) HP cell + a stable pointer to it — for the
DeathLink trigger. The combat HP lives in the heap (entity+0x98), not the static
mirrors, so this scans the WHOLE process by known value and narrows as you take
damage (read-only — no writes, so no crashes until you choose to test).

    python -m tools.entfind find 120      # your current HP from the HUD
    # take a hit, then:
    python -m tools.entfind narrow 105
    python -m tools.entfind narrow 98     # repeat until 1-2 remain
    python -m tools.entfind ptr 0xADDR    # find static pointers to that entity
    python -m tools.entfind reset

Addresses are ABSOLUTE (heap moves between sessions/rooms), so narrow within the
same room without transitioning. Exact-value match via fast byte search.
"""

from __future__ import annotations

import json
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client.memory import ProcessMemory, MemoryError_  # noqa: E402
from client.offsets import MODULE_NAME  # noqa: E402

STATE = Path(tempfile.gettempdir()) / "yso_entfind.json"


def _read(mem, base, size):
    try:
        return mem.read_bytes(base, size)
    except MemoryError_:
        out = bytearray(size)
        CH = 0x100000
        for o in range(0, size, CH):
            try:
                out[o:o + min(CH, size - o)] = mem.read_bytes(base + o, min(CH, size - o))
            except MemoryError_:
                pass
        return bytes(out)


def _find_all(buf: bytes, pat: bytes):
    """Aligned offsets in buf where the 4-byte pattern occurs."""
    hits, i = [], buf.find(pat)
    while i != -1:
        if (i & 3) == 0:
            hits.append(i)
        i = buf.find(pat, i + 1)
    return hits


def _scan(mem, value: float):
    """Absolute addresses matching value as int32 or float32 (exact)."""
    ipat = struct.pack("<i", int(value))
    fpat = struct.pack("<f", float(value))
    addrs = set()
    for base, size in mem.iter_regions():
        if size > 0x8000000:           # skip absurdly large regions (reserved)
            continue
        buf = _read(mem, base, size)
        for off in _find_all(buf, ipat):
            addrs.add(base + off)
        if fpat != ipat:
            for off in _find_all(buf, fpat):
                addrs.add(base + off)
    return addrs


def _val(mem, a):
    raw = mem.read_bytes(a, 4)
    return struct.unpack("<i", raw)[0], struct.unpack("<f", raw)[0]


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1].lower()
    if cmd == "reset":
        STATE.unlink(missing_ok=True)
        print("  reset.")
        return 0
    try:
        mem = ProcessMemory.attach(MODULE_NAME)
    except MemoryError_ as e:
        print(f"  attach failed: {e}")
        return 1

    if cmd == "find":
        value = float(argv[2])
        addrs = _scan(mem, value)
        STATE.write_text(json.dumps(sorted(addrs)))
        print(f"  find {value}: {len(addrs)} candidates. Take damage, then narrow.")
        return 0

    if cmd == "narrow":
        value = float(argv[2])
        prev = json.loads(STATE.read_text()) if STATE.exists() else []
        ival = int(value)
        keep = []
        for a in prev:
            try:
                i, f = _val(mem, a)
            except MemoryError_:
                continue
            if i == ival or abs(f - value) < 0.01:
                keep.append(a)
        STATE.write_text(json.dumps(keep))
        print(f"  narrow {value}: {len(keep)} remain.")
        if len(keep) <= 40:
            for a in keep:
                i, f = _val(mem, a)
                tag = "MODULE" if 0x400000 <= a < 0x81B000 else "heap"
                print(f"    0x{a:08X}  [{tag}]  int={i} float={f:.2f}")
        return 0

    if cmd == "ptr":
        target = int(argv[2], 16)
        # look for a 4-byte value pointing at the entity HP cell, or at the
        # entity base (cell - 0x98), anywhere in the process.
        wants = {target: "->hp", target - 0x98: "->entity(base=hp-0x98)"}
        pats = {struct.pack("<I", t & 0xFFFFFFFF): lbl for t, lbl in wants.items()}
        found = []
        for base, size in mem.iter_regions():
            if size > 0x8000000:
                continue
            buf = _read(mem, base, size)
            for pat, lbl in pats.items():
                for off in _find_all(buf, pat):
                    found.append((base + off, lbl))
        print(f"  ptr 0x{target:X}: {len(found)} pointer(s)")
        for a, lbl in found[:40]:
            tag = "MODULE" if 0x400000 <= a < 0x81B000 else "heap"
            print(f"    0x{a:08X}  [{tag}]  {lbl}")
        return 0

    print(f"  unknown command {cmd!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
