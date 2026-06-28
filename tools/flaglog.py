"""Passive g_flags change-logger for fast location/item discovery.

Run it, then just **play the game normally**. It watches the whole unified
``g_flags[]`` array (base ``+0x36B91C``, 512 int32) and logs every change in
real time, grouped into *events* (a burst of writes that happen together — i.e.
one chest open / altar / plate). For each event it pre-computes the likely
flag <-> item pairing, so opening a chest produces a single line like::

    EVENT 7  (+2.31s)
      FLAG  idx 0x12E  +0x36BDD4   0 -> 1      [Chest 1 - Panacea]   <- location
      ITEM  idx 0x59   +0x36BA80  -1 -> 1                            <- granted
      => pairs location flag idx 0x12E with item idx 0x59

The only manual step left is naming each new index with the room you were in.
Everything is appended to a CSV (default ``flaglog.csv``) you can open later and
fill in the ``label`` column.

Why this beats snapshot-diff: no pause/toggle dance per cell — one playthrough
records every index that ever flips, plus the item each chest grants, with the
0->1 / -1->N value transition telling flags and items apart automatically.

Classification (from the value transition, not address ranges):
  *  0 -> 1            -> FLAG   (events/locations start at 0)
  * -1 -> N (N>=1)     -> ITEM   (item slots start at -1 = never obtained)
  *  N -> M (M>N>=1)   -> ITEM+  (consumable count increment)
  *  M -> N (N<M)      -> DROP   (consumption / flag cleared)

Run from the repo root:  python -m tools.flaglog [csv_path]
(launch as Administrator if attach fails). Ctrl+C to stop.
"""

from __future__ import annotations

import csv
import struct
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client.memory import ProcessMemory, MemoryError_  # noqa: E402
from client.offsets import (  # noqa: E402
    ITEM_OFFSETS,
    LOCATION_FLAG_OFFSETS,
    MODULE_NAME,
)

# g_flags[] layout (see RE_FINDINGS.md).
GFLAGS_OFFSET = 0x36B91C
GFLAGS_COUNT = 512
GFLAGS_BYTES = GFLAGS_COUNT * 4

POLL_S = 0.20            # fast enough to catch a multi-write pickup burst
QUIET_FLUSH_S = 0.40     # a quiet gap this long ends (flushes) an event
EVENT_MAX_S = 4.0        # force-flush a never-quiet event (chatty churn)
CHATTY_MUTE = 25         # mute an index after this many changes (runtime noise)


def _known_names() -> dict[int, str]:
    """Map g_flags index -> human name for already-confirmed entries."""
    names: dict[int, str] = {}
    for name, off in ITEM_OFFSETS.items():
        names[(off - GFLAGS_OFFSET) // 4] = name
    for name, off in LOCATION_FLAG_OFFSETS.items():
        names[(off - GFLAGS_OFFSET) // 4] = name
    return names


def _classify(old: int, new: int) -> str:
    if old == 0 and new == 1:
        return "FLAG"
    if old <= 0 and new >= 1:
        return "ITEM"          # -1 (or 0) -> N : obtained
    if new > old >= 1:
        return "ITEM+"         # count increment
    return "DROP"              # decrement / cleared


class Change:
    __slots__ = ("idx", "old", "new", "kind")

    def __init__(self, idx: int, old: int, new: int):
        self.idx = idx
        self.old = old
        self.new = new
        self.kind = _classify(old, new)

    @property
    def offset(self) -> int:
        return GFLAGS_OFFSET + self.idx * 4


class FlagLogger:
    def __init__(self, mem: ProcessMemory, csv_path: Path):
        self.mem = mem
        self.known = _known_names()
        self.prev: list[int] | None = None
        self.start = time.monotonic()
        self.session = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.event_id = 0
        self.change_counts: dict[int, int] = {}
        self.muted: set[int] = set()

        # event coalescing buffer: idx -> (first_old, latest_new)
        self.buf: dict[int, tuple[int, int]] = {}
        self.buf_started: float | None = None
        self.last_change_at: float | None = None

        self.csv_path = csv_path
        new_file = not csv_path.exists()
        self.fh = csv_path.open("a", newline="", encoding="utf-8")
        self.writer = csv.writer(self.fh)
        if new_file:
            self.writer.writerow([
                "session", "t_elapsed", "event", "idx", "idx_hex",
                "offset", "old", "new", "kind", "paired_idx",
                "known_name", "label",
            ])
            self.fh.flush()

    # -- polling ------------------------------------------------------------ #

    def _read(self) -> list[int] | None:
        try:
            raw = self.mem.read_bytes(self.mem.resolve(GFLAGS_OFFSET), GFLAGS_BYTES)
        except MemoryError_:
            return None
        return list(struct.unpack(f"<{GFLAGS_COUNT}i", raw))

    def poll_once(self) -> None:
        cur = self._read()
        if cur is None:
            return
        if self.prev is None:
            self.prev = cur
            return

        now = time.monotonic()
        for idx in range(GFLAGS_COUNT):
            o, n = self.prev[idx], cur[idx]
            if o == n or idx in self.muted:
                continue
            self.change_counts[idx] = self.change_counts.get(idx, 0) + 1
            if self.change_counts[idx] == CHATTY_MUTE:
                self.muted.add(idx)
                print(f"  (muted chatty idx 0x{idx:X} after {CHATTY_MUTE} "
                      "changes — looks like runtime noise, not a pickup)")
                continue
            # coalesce: keep the FIRST old we saw and the LATEST new.
            first_old = self.buf[idx][0] if idx in self.buf else o
            self.buf[idx] = (first_old, n)
            if self.buf_started is None:
                self.buf_started = now
            self.last_change_at = now

        self.prev = cur

        # flush the event on a quiet gap or if it has run too long.
        if self.buf and self.last_change_at is not None:
            quiet = now - self.last_change_at >= QUIET_FLUSH_S
            too_long = self.buf_started is not None and \
                now - self.buf_started >= EVENT_MAX_S
            if quiet or too_long:
                self._flush()

    # -- event output ------------------------------------------------------- #

    def _flush(self) -> None:
        changes = [Change(i, o, n) for i, (o, n) in sorted(self.buf.items())]
        self.buf.clear()
        self.buf_started = None
        self.last_change_at = None
        # drop pure no-ops just in case
        changes = [c for c in changes if c.old != c.new]
        if not changes:
            return

        self.event_id += 1
        t = time.monotonic() - self.start

        flags = [c for c in changes if c.kind == "FLAG"]
        items = [c for c in changes if c.kind in ("ITEM", "ITEM+")]
        paired_idx = ""
        if flags and items:
            paired_idx = ",".join(f"0x{c.idx:X}"
                                  for c in (flags[0:1] + items))

        print(f"\nEVENT {self.event_id}  (+{t:.2f}s)")
        for c in changes:
            name = self.known.get(c.idx, "")
            tag = f"  [{name}]" if name else ""
            arrow = ""
            if c.kind == "FLAG":
                arrow = "   <- location/event"
            elif c.kind in ("ITEM", "ITEM+"):
                arrow = "   <- granted item"
            print(f"  {c.kind:5s} idx 0x{c.idx:<4X} +0x{c.offset:X}"
                  f"  {c.old} -> {c.new}{tag}{arrow}")
            self.writer.writerow([
                self.session, f"{t:.2f}", self.event_id, c.idx,
                f"0x{c.idx:X}", f"+0x{c.offset:X}", c.old, c.new, c.kind,
                paired_idx, name, "",
            ])
        if flags and items:
            fi = flags[0]
            print(f"  => pairs location flag idx 0x{fi.idx:X} with "
                  f"item idx {', '.join('0x%X' % c.idx for c in items)}")
        self.fh.flush()

    def close(self) -> None:
        if self.buf:
            self._flush()
        self.fh.close()


def run(mem: ProcessMemory, csv_path: Path) -> None:
    fl = FlagLogger(mem, csv_path)
    print(f"  attached pid={mem.pid} base=0x{mem.base_address:X}")
    print(f"  watching g_flags[{GFLAGS_COUNT}] at +0x{GFLAGS_OFFSET:X}, "
          f"poll {int(POLL_S * 1000)}ms")
    print(f"  logging to {csv_path}")
    print("  Play the game; pickups/plates/events will print here. Ctrl+C to stop.\n")
    try:
        while True:
            fl.poll_once()
            if not mem.is_alive():
                print("  game process gone — stopping.")
                break
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        print("\n  stopping (Ctrl+C).")
    finally:
        fl.close()
        print(f"  wrote {fl.event_id} event(s) to {csv_path}. "
              "Open it and fill in the 'label' column.")


def main() -> int:
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("flaglog.csv")
    print(f"Ys Origin g_flags logger — attaching to {MODULE_NAME} ...")
    try:
        mem = ProcessMemory.attach(MODULE_NAME)
    except MemoryError_ as e:
        print(f"  failed to attach: {e}")
        print("  Make sure the game is running (launch this as Admin if needed).")
        return 1
    try:
        run(mem, csv_path)
    finally:
        mem.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
