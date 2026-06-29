"""Find the current-HP memory cell by known-value search (for DeathLink).

HP is shown on the HUD (e.g. ``111/111``). Tell this tool your current HP; it
scans the module for that number (as float AND int32). Take a hit, then
``narrow`` to the new HP — repeat until one cell remains. That cell's
module-relative offset goes into ``yso_ap.cfg`` as ``hp_offset`` (and
``hp_float=1`` if it printed as a float).

Usage (game running, from repo root):
    python -m tools.hpfind find 111      # current HP from the HUD
    # take damage, then:
    python -m tools.hpfind narrow 96
    python -m tools.hpfind narrow 96     # (heal/various) keep narrowing
    python -m tools.hpfind show
    python -m tools.hpfind reset
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

# Search the whole module image (base 0x400000, end ~0x81B000).
LO, HI = 0x000000, 0x41B000
STATE = Path(tempfile.gettempdir()) / "yso_hpfind.json"
TOL = 0.5  # float match tolerance


def _read_module(mem: ProcessMemory) -> bytes:
    base = mem.resolve(LO)
    size = HI - LO
    try:
        return mem.read_bytes(base, size)
    except MemoryError_:
        out = bytearray(size)
        CH = 0x10000
        for off in range(0, size, CH):
            try:
                out[off:off + min(CH, size - off)] = mem.read_bytes(base + off, min(CH, size - off))
            except MemoryError_:
                pass
        return bytes(out)


def _matches(buf: bytes, value: float):
    """Offsets where buf has float≈value or int32==value."""
    fhits, ihits = [], []
    ival = int(value)
    n = len(buf) - 4
    for off in range(0, n, 4):
        w = buf[off:off + 4]
        f = struct.unpack("<f", w)[0]
        if abs(f - value) < TOL:
            fhits.append(off)
        if struct.unpack("<i", w)[0] == ival:
            ihits.append(off)
    return fhits, ihits


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
        print(f"  attach failed: {e} (is the game running?)")
        return 1

    if cmd in ("find", "narrow"):
        value = float(argv[2])
        buf = _read_module(mem)
        if cmd == "find":
            fh, ih = _matches(buf, value)
            cands = {"float": fh, "int": ih}
            print(f"  find {value}: {len(fh)} float, {len(ih)} int candidates.")
        else:
            prev = json.loads(STATE.read_text()) if STATE.exists() else {"float": [], "int": []}
            fh, ih = _matches(buf, value)
            cands = {"float": [o for o in prev["float"] if o in set(fh)],
                     "int": [o for o in prev["int"] if o in set(ih)]}
            print(f"  narrow {value}: {len(cands['float'])} float, {len(cands['int'])} int remain.")
        STATE.write_text(json.dumps(cands))
        _show(mem, cands)
        return 0

    if cmd == "show":
        if not STATE.exists():
            print("  nothing yet."); return 0
        _show(mem, json.loads(STATE.read_text()))
        return 0

    print(f"  unknown command {cmd!r}")
    return 2


def _show(mem: ProcessMemory, cands: dict) -> None:
    for kind in ("float", "int"):
        offs = cands.get(kind, [])
        if 0 < len(offs) <= 30:
            for off in offs:
                a = mem.resolve(LO + off)
                raw = mem.read_bytes(a, 4)
                f = struct.unpack("<f", raw)[0]
                i = struct.unpack("<i", raw)[0]
                print(f"    [{kind}] +0x{LO + off:06X}  float={f:.3f} int={i}")
        elif offs:
            print(f"    [{kind}] {len(offs)} candidates (narrow more)")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
