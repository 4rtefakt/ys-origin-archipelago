"""Snapshot-diff scanner for discovering Ys Origin offsets.

This is the workhorse for finding *persistent* values (equipment, flags, gem
counts, spell tiers) — anything you can change in-game but whose value or type
you don't know up front. It complements ``tools/scan.py`` (which is for values
you *do* know).

Why snapshot-diff: many Ys Origin values are stored in the module's static
save-state ``.data`` section, and the game keeps several *mirror* copies. A plain
value scan drowns in mirrors and transient buffers. Diffing two whole-region
snapshots around a single deliberate change isolates exactly the cells that
moved.

Method (game stays PAUSED in a menu between snaps, so nothing else drifts):

    snap a                 # capture state
    (change ONE thing in-game, e.g. equip a different armor)
    snap b
    diff a b changed       # -> candidate cells that changed
    (revert the change)
    snap c
    narrow changed         # keep cells that changed again (toggled back)
    narrow unchanged       # (do nothing first) drop self-ticking cells
    xref a b               # show each candidate's value in a / b / live
    save armor_id          # persist the winner to client/offsets.json
    poke int 257           # optionally test-write to find the *master* copy

The candidate set is held in memory; snapshots persist for the REPL session.

Run from the repo root:  python -m tools.snapdiff
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client.memory import ProcessMemory, MemoryError_  # noqa: E402
from client.offsets import MODULE_NAME, OFFSET_FIELD_NAMES  # noqa: E402
from client.offset_store import OffsetStore  # noqa: E402

# value type -> (struct format, size)
TYPES = {
    "int": ("<i", 4), "uint": ("<I", 4), "short": ("<h", 2),
    "byte": ("<b", 1), "float": ("<f", 4), "double": ("<d", 8),
}

# diff/narrow predicates over (old, new) integer cell values
PREDICATES = {
    "changed": lambda o, n: o != n,
    "unchanged": lambda o, n: o == n,
    "inc": lambda o, n: n > o,
    "dec": lambda o, n: n < o,
}


class Snapshot:
    """A captured set of memory segments: ``{base: bytes}``."""

    def __init__(self, segments: dict[int, bytes]):
        self.segments = segments

    def value_at(self, addr: int):
        """Return the int32 at ``addr`` if covered, else ``None``."""
        for base, data in self.segments.items():
            if base <= addr < base + len(data) - 3:
                return struct.unpack_from("<i", data, addr - base)[0]
        return None


class SnapDiff:
    def __init__(self, mem: ProcessMemory):
        self.mem = mem
        self.scope = ("module", mem.base_address, mem.module_end)
        self.snaps: dict[str, Snapshot] = {}
        self.cands: list[int] = []           # absolute addresses
        self.last: dict[int, int] = {}       # addr -> last-seen int32 value

    # -- scope & capture ---------------------------------------------------- #

    def set_scope(self, spec: list[str]) -> None:
        if not spec or spec[0] == "module":
            self.scope = ("module", self.mem.base_address, self.mem.module_end)
        elif spec[0] == "committed":
            self.scope = ("committed", 0, 0)
        elif len(spec) == 2:
            lo = int(spec[0], 0); hi = int(spec[1], 0)
            # accept either absolute addresses or module-relative offsets
            if lo < self.mem.base_address:
                lo += self.mem.base_address; hi += self.mem.base_address
            self.scope = ("range", lo, hi)
        else:
            print("  usage: scope module | committed | <start> <end>")
            return
        print(f"  scope = {self.scope[0]} "
              f"[0x{self.scope[1]:X}..0x{self.scope[2]:X}]"
              if self.scope[0] != "committed" else "  scope = committed (all)")

    def _capture(self) -> dict[int, bytes]:
        kind = self.scope[0]
        segs: dict[int, bytes] = {}
        if kind == "committed":
            for base, size in self.mem.iter_regions():
                try:
                    segs[base] = self.mem.read_bytes(base, size)
                except MemoryError_:
                    pass
        else:
            lo, hi = self.scope[1], self.scope[2]
            try:
                segs[lo] = self.mem.read_bytes(lo, hi - lo)
            except MemoryError_ as e:
                print(f"  capture failed: {e}")
        return segs

    def snap(self, name: str) -> None:
        segs = self._capture()
        total = sum(len(d) for d in segs.values())
        self.snaps[name] = Snapshot(segs)
        print(f"  snapshot '{name}': {len(segs)} segment(s), {total} bytes")

    # -- diff & narrow ------------------------------------------------------ #

    def diff(self, a: str, b: str, mode: str = "changed") -> None:
        if a not in self.snaps or b not in self.snaps:
            print("  unknown snapshot name; see `snaps`"); return
        pred = PREDICATES.get(mode)
        if not pred:
            print(f"  mode must be one of {', '.join(PREDICATES)}"); return
        sa, sb = self.snaps[a], self.snaps[b]
        cands, last = [], {}
        for base, da in sa.segments.items():
            db = sb.segments.get(base)
            if db is None or len(db) != len(da):
                continue
            for i in range(0, len(da) - 3, 4):
                va = struct.unpack_from("<i", da, i)[0]
                vb = struct.unpack_from("<i", db, i)[0]
                if pred(va, vb):
                    addr = base + i
                    cands.append(addr); last[addr] = vb
        self.cands, self.last = cands, last
        print(f"  diff {a} vs {b} ({mode}): {len(cands)} candidate(s)")
        self._maybe_list()

    def narrow(self, mode: str = "changed") -> None:
        if not self.cands:
            print("  no candidates; run `diff` first"); return
        pred = PREDICATES.get(mode)
        if not pred:
            print(f"  mode must be one of {', '.join(PREDICATES)}"); return
        survivors, last = [], {}
        for addr in self.cands:
            try:
                cur = struct.unpack("<i", self.mem.read_bytes(addr, 4))[0]
            except MemoryError_:
                continue
            if pred(self.last.get(addr, cur), cur):
                survivors.append(addr); last[addr] = cur
            else:
                last[addr] = cur
        # keep last-seen current for ALL survivors so chained narrows compare
        # against the latest value
        self.cands = survivors
        self.last = {a: last[a] for a in survivors}
        print(f"  narrow ({mode}): {len(survivors)} candidate(s)")
        self._maybe_list()

    # -- reporting ---------------------------------------------------------- #

    def _fmt_addr(self, addr: int) -> str:
        if self.mem.in_module(addr):
            return f"{MODULE_NAME}+0x{addr - self.mem.base_address:X}"
        return f"0x{addr:X} (dynamic)"

    def _maybe_list(self, limit: int = 30) -> None:
        if 0 < len(self.cands) <= limit:
            self.list_cands()

    def list_cands(self) -> None:
        for addr in self.cands[:60]:
            try:
                b = self.mem.read_bytes(addr, 4)
                iv = struct.unpack("<i", b)[0]; fv = struct.unpack("<f", b)[0]
                fd = f"{fv:.3f}" if 1e-6 < abs(fv) < 1e7 else "~"
            except MemoryError_:
                iv = fd = "?"
            print(f"    {self._fmt_addr(addr):28s} int={iv:<12} float={fd}")
        if len(self.cands) > 60:
            print(f"    ... and {len(self.cands) - 60} more")

    def xref(self, a: str, b: str) -> None:
        """Show each candidate's value in snapshot a, snapshot b, and live."""
        if a not in self.snaps or b not in self.snaps:
            print("  unknown snapshot name; see `snaps`"); return
        sa, sb = self.snaps[a], self.snaps[b]
        for addr in self.cands[:80]:
            va, vb = sa.value_at(addr), sb.value_at(addr)
            try:
                cur = struct.unpack("<i", self.mem.read_bytes(addr, 4))[0]
            except MemoryError_:
                cur = "?"
            print(f"    {self._fmt_addr(addr):28s} {a}={va:<8} {b}={vb:<8} live={cur}")
        if len(self.cands) > 80:
            print(f"    ... and {len(self.cands) - 80} more")

    def gdiff(self, a: str, b: str) -> None:
        """Diff ONLY the g_flags array (item/flag state) between two snapshots.

        g_flags is the unified item/event array (base +0x36B91C, 512 int32). This
        is the cleanest mapping view: it ignores all cutscene/combat/render noise
        elsewhere and prints exactly which flags/items changed, with their index
        (index = (off - 0x36B91C) / 4). Ideal for pinning a location/item after a
        single in-game action.
        """
        if a not in self.snaps or b not in self.snaps:
            print("  unknown snapshot name; see `snaps`"); return
        gbase = self.mem.base_address + 0x36B91C
        sa, sb = self.snaps[a], self.snaps[b]
        changes = 0
        for i in range(512):
            addr = gbase + i * 4
            va, vb = sa.value_at(addr), sb.value_at(addr)
            if va is None or vb is None or va == vb:
                continue
            changes += 1
            print(f"    idx {i:3d} (0x{i:X})  {self._fmt_addr(addr):26s} {va} -> {vb}")
        print(f"  ({changes} g_flags entries changed)")

    # -- persistence & write test ------------------------------------------ #

    def save(self, field: str, idx: int = 0) -> None:
        if field not in OFFSET_FIELD_NAMES:
            print(f"  unknown field {field!r}. valid: {', '.join(sorted(OFFSET_FIELD_NAMES))}")
            return
        if not (0 <= idx < len(self.cands)):
            print(f"  index out of range (0..{len(self.cands) - 1})"); return
        addr = self.cands[idx]
        if not self.mem.in_module(addr):
            print(f"  refusing: {self._fmt_addr(addr)} is dynamic, not a static offset")
            return
        off = addr - self.mem.base_address
        st = OffsetStore.load(); st.set(field, off)
        print(f"  saved {field} = {MODULE_NAME}+0x{off:X} to {st.path.name}")

    def poke(self, type_name: str, value: str, idx: int = 0) -> None:
        if type_name not in TYPES:
            print(f"  unknown type; choose from {', '.join(TYPES)}"); return
        if not (0 <= idx < len(self.cands)):
            print(f"  index out of range (0..{len(self.cands) - 1})"); return
        fmt, size = TYPES[type_name]
        val = float(value) if type_name in ("float", "double") else int(value, 0)
        addr = self.cands[idx]
        self.mem.write_bytes(addr, struct.pack(fmt, val))
        print(f"  wrote {val} ({type_name}) to {self._fmt_addr(addr)} "
              "-> check the in-game effect to tell master from mirror")


HELP = """
commands:
  scope module|committed|<start> <end>   set capture region (default: module)
  snap <name>            capture current memory into a named snapshot
  snaps                  list captured snapshots
  diff <a> <b> [mode]    candidates where a->b matches mode
                         (modes: changed unchanged inc dec; default changed)
  gdiff <a> <b>          diff ONLY g_flags (item/event array) — cleanest mapping
                         view, ignores cutscene/combat noise; shows index
  narrow [mode]          re-read live, keep candidates matching mode vs last seen
  list                   show current candidates (int + float)
  xref <a> <b>           show each candidate's value in a / b / live
  save <field> [idx]     persist candidate offset to client/offsets.json
  poke <type> <val> [idx]  write to a candidate (find master vs mirror)
  count / reset / help / quit
"""


def repl(mem: ProcessMemory) -> None:
    sd = SnapDiff(mem)
    print(f"  attached pid={mem.pid} base=0x{mem.base_address:X} "
          f"module=0x{mem.module_size:X} bytes")
    print(f"  default scope = module [0x{mem.base_address:X}..0x{mem.module_end:X}]")
    print(HELP)
    while True:
        try:
            line = input("snapdiff> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); return
        if not line:
            continue
        p = line.split()
        cmd, rest = p[0].lower(), p[1:]
        try:
            if cmd in ("quit", "exit", "q"):
                return
            elif cmd == "help":
                print(HELP)
            elif cmd == "scope":
                sd.set_scope(rest)
            elif cmd == "snap" and rest:
                sd.snap(rest[0])
            elif cmd == "snaps":
                print("  " + (", ".join(sd.snaps) or "(none)"))
            elif cmd == "diff" and len(rest) >= 2:
                sd.diff(rest[0], rest[1], rest[2] if len(rest) > 2 else "changed")
            elif cmd == "gdiff" and len(rest) >= 2:
                sd.gdiff(rest[0], rest[1])
            elif cmd == "narrow":
                sd.narrow(rest[0] if rest else "changed")
            elif cmd == "list":
                sd.list_cands()
            elif cmd == "xref" and len(rest) >= 2:
                sd.xref(rest[0], rest[1])
            elif cmd == "save" and rest:
                sd.save(rest[0], int(rest[1]) if len(rest) > 1 else 0)
            elif cmd == "poke" and len(rest) >= 2:
                sd.poke(rest[0], rest[1], int(rest[2]) if len(rest) > 2 else 0)
            elif cmd == "count":
                print(f"  {len(sd.cands)} candidate(s)")
            elif cmd == "reset":
                sd.cands, sd.last = [], {}
                print("  cleared candidates")
            else:
                print("  ? type `help`")
        except MemoryError_ as e:
            print(f"  memory error: {e}")


def main() -> int:
    print(f"Ys Origin snapshot-diff scanner — attaching to {MODULE_NAME} ...")
    try:
        mem = ProcessMemory.attach(MODULE_NAME)
    except MemoryError_ as e:
        print(f"  failed to attach: {e}")
        print("  Make sure the game is running (launch this as Admin if needed).")
        return 1
    try:
        repl(mem)
    finally:
        mem.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
