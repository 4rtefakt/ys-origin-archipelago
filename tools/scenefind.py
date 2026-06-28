"""Find the current-scene / current-floor memory global (for boss/floor/room
detection). Scriptable snap/narrow so it can be driven step-by-step while you
just walk between rooms in-game.

A scene/floor variable is *stable while you stand in a room* and *changes to a
new stable value when you cross into another room* — unlike position/camera/
timers which change every frame. So: snapshot, move rooms, narrow to cells that
changed; repeat across a few rooms; then "still" (stand put) to drop the
frame-by-frame changers. The survivor whose values line up with the rooms you
visited is the scene/floor global.

State persists between invocations (so each command is one shot you can run
between moves). Scoped to the static-data window by default (where such globals
live: g_flags 0x36B91C, blessings 0x36A634, stats 0x36A7xx are all here).

Usage (run from repo root, game running):
    python -m tools.scenefind base   "2F Path 1"      # first snapshot
    # walk to another room, then:
    python -m tools.scenefind narrow "5F Save"        # keep cells that changed
    python -m tools.scenefind narrow "2F Gemma Room"  # ...repeat a few rooms
    python -m tools.scenefind still  "(standing put)" # drop frame-by-frame noise
    python -m tools.scenefind list                    # show survivors + values
    python -m tools.scenefind reset                   # start over
"""

from __future__ import annotations

import array
import json
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client.memory import ProcessMemory, MemoryError_  # noqa: E402
from client.offsets import MODULE_NAME  # noqa: E402

# Default scan window (module-relative) — the static state region.
DEF_LO, DEF_HI = 0x300000, 0x3C0000

STATE = Path(tempfile.gettempdir()) / "yso_scenefind"
SNAP = STATE / "last.bin"
META = STATE / "meta.json"


def _load_meta() -> dict:
    return json.loads(META.read_text()) if META.exists() else {}


def _save_meta(m: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    META.write_text(json.dumps(m))


def _read_window(mem: ProcessMemory, lo: int, hi: int) -> bytes:
    return mem.read_bytes(mem.resolve(lo), hi - lo)


def _ints(buf: bytes) -> array.array:
    a = array.array("i")
    a.frombytes(buf[: len(buf) // 4 * 4])
    return a


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1].lower()

    if cmd == "reset":
        for p in (SNAP, META):
            if p.exists():
                p.unlink()
        print("  reset.")
        return 0

    try:
        mem = ProcessMemory.attach(MODULE_NAME)
    except MemoryError_ as e:
        print(f"  attach failed: {e} (is the game running? try Admin)")
        return 1

    meta = _load_meta()
    lo = meta.get("lo", DEF_LO)
    hi = meta.get("hi", DEF_HI)

    if cmd == "base":
        if len(argv) >= 4:
            lo, hi = int(argv[2], 0), int(argv[3], 0)
        buf = _read_window(mem, lo, hi)
        STATE.mkdir(parents=True, exist_ok=True)
        SNAP.write_bytes(buf)
        _save_meta({"lo": lo, "hi": hi, "cands": None,
                    "label": argv[2] if len(argv) > 2 and argv[2] not in (hex(lo),) else "base"})
        print(f"  base snapshot @ [+0x{lo:X}..+0x{hi:X}] ({hi-lo} bytes). "
              "Move to another room, then `narrow <room>`.")
        return 0

    if not SNAP.exists():
        print("  no base snapshot — run `base <room>` first.")
        return 0
    prev = _ints(SNAP.read_bytes())
    curr = _ints(_read_window(mem, lo, hi))
    n = min(len(prev), len(curr))
    cands = meta.get("cands")  # list of word-indices, or None = "all"
    label = argv[2] if len(argv) > 2 else "?"

    if cmd in ("narrow", "still"):
        want_change = (cmd == "narrow")
        if cands is None:
            survivors = [i for i in range(n) if (prev[i] != curr[i]) == want_change]
        else:
            survivors = [i for i in cands if i < n and (prev[i] != curr[i]) == want_change]
        SNAP.write_bytes(curr.tobytes())
        meta["cands"] = survivors
        _save_meta(meta)
        verb = "changed" if want_change else "unchanged"
        print(f"  {cmd} '{label}': {len(survivors)} candidate(s) {verb}.")
        if len(survivors) <= 40:
            _show(survivors, curr, lo)
        else:
            print("  (narrow again across more rooms, or `still` while standing put)")
        return 0

    if cmd == "list":
        if not cands:
            print("  no candidates yet."); return 0
        _show(cands, curr, lo)
        return 0

    print(f"  unknown command {cmd!r}"); return 2


def _show(idxs, curr, lo) -> None:
    for i in idxs[:40]:
        off = lo + i * 4
        v = curr[i]
        print(f"    +0x{off:06X}  int={v}  (0x{v & 0xFFFFFFFF:X})")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
