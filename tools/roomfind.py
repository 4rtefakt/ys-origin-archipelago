"""Find the current-scene / current-room memory global (per-ROOM granularity).

The older ``scenefind.py`` does pairwise changed/unchanged narrowing and only
ever resolved per-*floor* granularity (current_floor +0x36BC58). For room logic
we need a global that is **stable while you stand in a room** and takes a
**distinct value in each distinct room** (and the SAME value when you walk back
into a room you already visited). That "injective room -> value, consistent on
revisit" signal is far stronger than a single changed/unchanged diff.

How to use it (run from repo root, game running, ideally a stable save):

    python -m tools.roomfind snap "S_1000 1F Save"     # snapshot, label = where you are
    # walk to an ADJACENT room, then:
    python -m tools.roomfind snap "S_1001 2F Path 1"
    python -m tools.roomfind snap "S_1002 2F Path 2"
    # walk BACK into a room you already labelled (revisit) — use the SAME label:
    python -m tools.roomfind snap "S_1000 1F Save"
    python -m tools.roomfind solve                      # rank discriminator cells
    python -m tools.roomfind deref +0x3A1234            # chase a candidate pointer
    python -m tools.roomfind reset

``solve`` keeps only word-indices that are:
  * constant across all snapshots sharing a label (stable within a room), and
  * take >=2 distinct values across the distinct labels (vary by room),
then ranks: best = a perfect injection (one value per room) that is ALSO a
plausible pointer (into the module, the scenelist string table, or the heap),
since the current-scene global is most likely a record/string pointer.

Default window is WIDE (0x300000..0x6A0000) so it covers both the static state
region AND the scenelist string table near +0x298A3C (abs 0x698A3C) that the
scene loader references — the older narrow window missed pointer-type globals
that point there.
"""

from __future__ import annotations

import array
import json
import struct
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client.memory import ProcessMemory, MemoryError_  # noqa: E402
from client.offsets import MODULE_NAME  # noqa: E402

# Default window (module-relative), kept INSIDE the module image (base 0x400000,
# end ~0x81B000 == module-rel 0x41B000). Starts low enough to cover the .rdata
# scenelist string table (~+0x298A3C) that the scene loader references, through
# the .data static state region (g_flags +0x36B91C, stats +0x36A7xx). Reading
# past the module end hits unmapped memory, so we clamp + read gap-tolerantly.
DEF_LO, DEF_HI = 0x200000, 0x41B000

# A 32-bit module is mapped at 0x400000 with size ~0x41B000; the heap and other
# images live above. We classify a cell's value as a pointer if it lands in a
# readable region (checked live in `deref`); for ranking we just bucket roughly.
MODULE_BASE = 0x400000
MODULE_END = 0x81B000

STATE = Path(tempfile.gettempdir()) / "yso_roomfind"
META = STATE / "meta.json"


def _meta() -> dict:
    if META.exists():
        return json.loads(META.read_text())
    return {"lo": DEF_LO, "hi": DEF_HI, "snaps": []}  # snaps: [{label, file}]


def _save_meta(m: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    META.write_text(json.dumps(m))


def _read_window(mem: ProcessMemory, lo: int, hi: int) -> bytes:
    """Read [lo,hi) module-relative, gap-tolerant: unreadable pages -> zeros so
    word indices stay aligned across snapshots regardless of mapping holes."""
    base = mem.resolve(lo)
    size = hi - lo
    try:
        return mem.read_bytes(base, size)
    except MemoryError_:
        pass
    CHUNK = 0x10000
    out = bytearray(size)
    for off in range(0, size, CHUNK):
        n = min(CHUNK, size - off)
        try:
            out[off:off + n] = mem.read_bytes(base + off, n)
        except MemoryError_:
            pass  # leave zeros for this hole
    return bytes(out)


def _ints(buf: bytes) -> array.array:
    a = array.array("i")
    a.frombytes(buf[: len(buf) // 4 * 4])
    return a


def _attach() -> ProcessMemory:
    return ProcessMemory.attach(MODULE_NAME)


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1].lower()
    m = _meta()
    lo, hi = m["lo"], m["hi"]

    if cmd == "reset":
        if STATE.exists():
            for p in STATE.glob("*"):
                p.unlink()
        print("  reset (snapshots cleared).")
        return 0

    if cmd == "window":
        if len(argv) >= 4:
            m["lo"], m["hi"] = int(argv[2], 0), int(argv[3], 0)
            m["snaps"] = []  # window change invalidates prior snaps
            _save_meta(m)
            print(f"  window set to [+0x{m['lo']:X}..+0x{m['hi']:X}]; snapshots cleared.")
        else:
            print(f"  window = [+0x{lo:X}..+0x{hi:X}], {len(m['snaps'])} snapshot(s).")
        return 0

    try:
        mem = _attach()
    except MemoryError_ as e:
        print(f"  attach failed: {e} (is the game running? try Admin)")
        return 1

    if cmd == "snap":
        label = argv[2] if len(argv) > 2 else f"snap{len(m['snaps'])}"
        buf = _read_window(mem, lo, hi)
        STATE.mkdir(parents=True, exist_ok=True)
        fn = STATE / f"snap{len(m['snaps'])}.bin"
        fn.write_bytes(buf)
        m["snaps"].append({"label": label, "file": fn.name})
        _save_meta(m)
        n_labels = len({s["label"] for s in m["snaps"]})
        print(f"  snapped '{label}'  ({len(m['snaps'])} snapshots, {n_labels} distinct rooms).")
        if n_labels >= 2:
            print("  -> run `solve` any time; more rooms + at least one revisit sharpen it.")
        return 0

    if cmd == "deref":
        # deref +0xOFFSET  -> read pointer there from the LATEST snapshot AND live,
        # then dump the target as a C-string + hex so we can spot a scene path.
        if len(argv) < 3:
            print("  usage: deref +0xOFFSET")
            return 2
        off = int(argv[2], 0)
        ptr = mem.read_uint32(mem.resolve(off))
        print(f"  +0x{off:X} -> 0x{ptr:08X}")
        _dump_target(mem, ptr)
        return 0

    if cmd == "solve":
        return _solve(mem, m)

    print(f"  unknown command {cmd!r}")
    return 2


def _dump_target(mem: ProcessMemory, ptr: int) -> None:
    if ptr < 0x10000:
        print("    (not a pointer)")
        return
    try:
        raw = mem.read_bytes(ptr, 64)
    except MemoryError_:
        print("    (unreadable)")
        return
    s = raw.split(b"\0", 1)[0]
    try:
        txt = s.decode("cp932")
    except Exception:
        txt = ""
    printable = txt if all(32 <= ord(c) < 127 or c == "\\" for c in txt) else ""
    print(f"    bytes: {raw[:32].hex(' ')}")
    if printable:
        print(f"    cstr : {printable!r}")


def _solve(mem: ProcessMemory, m: dict) -> int:
    snaps = m["snaps"]
    if len({s["label"] for s in snaps}) < 2:
        print("  need >=2 distinct rooms snapped first.")
        return 0
    lo, hi = m["lo"], m["hi"]
    arrs: List[array.array] = []
    labels: List[str] = []
    for s in snaps:
        arrs.append(_ints((STATE / s["file"]).read_bytes()))
        labels.append(s["label"])
    n = min(len(a) for a in arrs)
    distinct_labels = sorted(set(labels))

    # group snapshot indices by label
    by_label: Dict[str, List[int]] = {}
    for i, lab in enumerate(labels):
        by_label.setdefault(lab, []).append(i)

    # Walk every word index; keep those constant-within-label and varying-across.
    candidates = []  # (score, idx, value_per_label)
    for wi in range(n):
        vals = [a[wi] for a in arrs]
        # constant within each label?
        ok = True
        per_label = {}
        for lab, idxs in by_label.items():
            v0 = vals[idxs[0]]
            for j in idxs[1:]:
                if vals[j] != v0:
                    ok = False
                    break
            if not ok:
                break
            per_label[lab] = v0
        if not ok:
            continue
        uniq = set(per_label.values())
        if len(uniq) < 2:
            continue  # doesn't vary by room
        # score: prefer perfect injection (one distinct value per room), and
        # prefer pointer-looking values (module or heap range).
        injective = len(uniq) == len(distinct_labels)
        ptrish = sum(1 for v in per_label.values()
                     if (v & 0xFFFFFFFF) >= MODULE_BASE) / len(per_label)
        score = (2 if injective else 0) + ptrish
        candidates.append((score, wi, per_label))

    candidates.sort(key=lambda c: -c[0])
    print(f"  {len(candidates)} cell(s) stable-within-room and varying-across "
          f"({len(distinct_labels)} rooms, {len(snaps)} snapshots).")
    print("  top candidates (offset, score, value-per-room):")
    for score, wi, per_label in candidates[:25]:
        off = lo + wi * 4
        vstr = "  ".join(f"{lab.split()[0]}=0x{v & 0xFFFFFFFF:X}"
                         for lab, v in per_label.items())
        kind = _classify(per_label)
        print(f"    +0x{off:06X}  score={score:.2f}  [{kind}]  {vstr}")
    if candidates:
        best_off = lo + candidates[0][1] * 4
        print(f"\n  -> chase the top pointer-looking one: "
              f"python -m tools.roomfind deref +0x{best_off:X}")
    return 0


def _classify(per_label: Dict[str, int]) -> str:
    vals = [v & 0xFFFFFFFF for v in per_label.values()]
    if all(MODULE_BASE <= v < MODULE_END for v in vals):
        return "module-ptr"
    if all(v >= MODULE_END for v in vals):
        return "heap-ptr"
    if all(0 <= v < 0x10000 for v in vals):
        return "small-int/index"
    return "mixed"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
