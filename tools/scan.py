"""Interactive memory scanner for discovering unknown Ys Origin offsets.

A minimal Cheat-Engine-style workflow implemented in pure Python:

  1. Attach to the running ``yso_win.exe``.
  2. Validate the connection against a known anchor (EXP at ``+0x7028C0``).
  3. ``scan <type> <value>``  -> find every address currently holding ``value``.
  4. (change the value in-game) ``narrow <new_value>`` -> keep only addresses
     that now hold the new value.
  5. Repeat ``narrow`` until a handful of candidates remain.
  6. ``offset`` -> print survivors as module-relative offsets, ready to paste
     into ``client/offsets.py``.

Run:  python -m tools.scan         (from the repo root)
  or  python tools/scan.py

This is a *development* tool. It reads broadly but only writes if you explicitly
ask it to (``poke``), and never touches the game otherwise.
"""

from __future__ import annotations

import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

# Allow running both as `python -m tools.scan` and `python tools/scan.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client.memory import (  # noqa: E402
    ProcessMemory,
    MemoryError_,
    ReadFailed,
)
from client.offsets import (  # noqa: E402
    MODULE_NAME,
    OFFSET_FIELD_NAMES,
    OFFSETS,
)
from client.offset_store import OffsetStore  # noqa: E402

# Region enumeration now lives on ProcessMemory.iter_regions(); kept as a
# module-level alias so existing references still work.
def iter_regions(mem: ProcessMemory) -> Iterable[tuple[int, int]]:
    """Yield ``(base, size)`` for every committed, readable region of ``mem``."""
    return mem.iter_regions()


# --------------------------------------------------------------------------- #
# Value types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ValueType:
    name: str
    size: int
    pack: Callable[[object], bytes]
    unpack: Callable[[bytes], object]
    parse: Callable[[str], object]


def _approx_float(parse_str: str):
    return float(parse_str)


TYPES: dict[str, ValueType] = {
    "int": ValueType("int", 4, lambda v: struct.pack("<i", int(v)),
                     lambda b: struct.unpack("<i", b)[0], lambda s: int(s, 0)),
    "uint": ValueType("uint", 4, lambda v: struct.pack("<I", int(v)),
                      lambda b: struct.unpack("<I", b)[0], lambda s: int(s, 0)),
    "short": ValueType("short", 2, lambda v: struct.pack("<h", int(v)),
                       lambda b: struct.unpack("<h", b)[0], lambda s: int(s, 0)),
    "byte": ValueType("byte", 1, lambda v: struct.pack("<b", int(v)),
                      lambda b: struct.unpack("<b", b)[0], lambda s: int(s, 0)),
    "float": ValueType("float", 4, lambda v: struct.pack("<f", float(v)),
                       lambda b: struct.unpack("<f", b)[0], _approx_float),
    "double": ValueType("double", 8, lambda v: struct.pack("<d", float(v)),
                        lambda b: struct.unpack("<d", b)[0], _approx_float),
}

# Floats rarely compare exactly; allow a small tolerance on match/narrow.
FLOAT_EPS = 1e-3


def _values_equal(vt: ValueType, a: object, b: object) -> bool:
    if vt.name in ("float", "double"):
        return abs(float(a) - float(b)) <= FLOAT_EPS
    return a == b


# --------------------------------------------------------------------------- #
# Scanner state
# --------------------------------------------------------------------------- #


class Scanner:
    def __init__(self, mem: ProcessMemory):
        self.mem = mem
        self.vt: Optional[ValueType] = None
        self.candidates: list[int] = []  # absolute addresses

    # -- full scan ---------------------------------------------------------- #

    def scan(self, type_name: str, value_str: str) -> None:
        if type_name not in TYPES:
            print(f"  unknown type {type_name!r}; choose from {', '.join(TYPES)}")
            return
        vt = TYPES[type_name]
        target = vt.parse(value_str)
        self.vt = vt
        needle = vt.pack(target)
        found: list[int] = []
        scanned_bytes = 0

        print(f"  scanning for {type_name} == {target} ...")
        for base, size in iter_regions(self.mem):
            try:
                blob = self.mem.read_bytes(base, size)
            except MemoryError_:
                continue
            scanned_bytes += size
            if vt.name in ("float", "double"):
                # Float compare with tolerance: walk aligned positions.
                step = vt.size
                for i in range(0, len(blob) - vt.size + 1, step):
                    chunk = blob[i:i + vt.size]
                    try:
                        if _values_equal(vt, vt.unpack(chunk), target):
                            found.append(base + i)
                    except struct.error:
                        pass
            else:
                start = 0
                while True:
                    idx = blob.find(needle, start)
                    if idx == -1:
                        break
                    found.append(base + idx)
                    start = idx + 1
            if len(found) > 5_000_000:
                print("  >5M hits — value is too common; pick something rarer.")
                break

        self.candidates = found
        print(f"  scanned {scanned_bytes // (1024 * 1024)} MiB, "
              f"{len(found)} candidate(s).")
        self._maybe_list()

    # -- narrow ------------------------------------------------------------- #

    def narrow(self, value_str: str) -> None:
        if self.vt is None or not self.candidates:
            print("  nothing to narrow; run `scan` first.")
            return
        target = self.vt.parse(value_str)
        survivors: list[int] = []
        for addr in self.candidates:
            try:
                cur = self.vt.unpack(self.mem.read_bytes(addr, self.vt.size))
            except MemoryError_:
                continue
            if _values_equal(self.vt, cur, target):
                survivors.append(addr)
        self.candidates = survivors
        print(f"  narrowed to {len(survivors)} candidate(s) holding {target}.")
        self._maybe_list()

    # -- changed / unchanged (no known value) ------------------------------ #

    def changed(self, want_change: bool) -> None:
        """Keep addresses whose value changed (or not) since last snapshot."""
        if self.vt is None or not self.candidates:
            print("  nothing to filter; run `scan` first.")
            return
        snap = getattr(self, "_snapshot", None)
        cur_map: dict[int, object] = {}
        for addr in self.candidates:
            try:
                cur_map[addr] = self.vt.unpack(self.mem.read_bytes(addr, self.vt.size))
            except MemoryError_:
                pass
        if snap is None:
            self._snapshot = cur_map
            print("  baseline captured; change the value in-game then re-run.")
            return
        survivors = [
            a for a, v in cur_map.items()
            if a in snap and (
                (not _values_equal(self.vt, v, snap[a])) == want_change
            )
        ]
        self.candidates = survivors
        self._snapshot = cur_map
        print(f"  {'changed' if want_change else 'unchanged'}: "
              f"{len(survivors)} candidate(s).")
        self._maybe_list()

    # -- reporting ---------------------------------------------------------- #

    def _maybe_list(self, limit: int = 20) -> None:
        if 0 < len(self.candidates) <= limit:
            self.list_candidates()

    def list_candidates(self) -> None:
        base = self.mem.base_address
        for addr in self.candidates[:50]:
            rel = addr - base
            sign = "+" if rel >= 0 else "-"
            try:
                val = self.vt.unpack(self.mem.read_bytes(addr, self.vt.size)) \
                    if self.vt else "?"
            except MemoryError_:
                val = "<unreadable>"
            in_module = 0 <= rel < 0x10_000_000
            tag = "" if in_module else "   (outside module — likely dynamic)"
            print(f"    0x{addr:012X}  {MODULE_NAME}{sign}0x{abs(rel):X}"
                  f"  = {val}{tag}")
        if len(self.candidates) > 50:
            print(f"    ... and {len(self.candidates) - 50} more")

    def emit_offsets(self) -> None:
        """Print survivors that live inside the module as a Python dict."""
        base = self.mem.base_address
        inside = [a - base for a in self.candidates if 0 <= a - base < 0x10_000_000]
        if not inside:
            print("  no in-module candidates (all look dynamic/heap).")
            return
        print("  # paste into client/offsets.py")
        print("  {")
        for i, rel in enumerate(inside[:50]):
            print(f"      \"candidate_{i}\": 0x{rel:X},")
        print("  }")

    def save_offset(self, field_name: str, index: Optional[int] = None) -> None:
        """Persist a candidate's module-relative offset into offsets.json.

        Requires exactly one candidate, or an explicit ``index`` to pick from
        several. The address must lie inside the module image (a static offset);
        dynamic/heap addresses are refused.
        """
        if field_name not in OFFSET_FIELD_NAMES:
            print(f"  unknown offset field {field_name!r}. valid fields:")
            print("    " + ", ".join(sorted(OFFSET_FIELD_NAMES)))
            return
        if not self.candidates:
            print("  no candidates; run `scan`/`narrow` first.")
            return
        if index is None:
            if len(self.candidates) != 1:
                print(f"  {len(self.candidates)} candidates — narrow to 1, or "
                      f"use `save {field_name} <index>` (see `list`).")
                return
            addr = self.candidates[0]
        else:
            if not (0 <= index < len(self.candidates)):
                print(f"  index out of range (0..{len(self.candidates) - 1}).")
                return
            addr = self.candidates[index]

        rel = addr - self.mem.base_address
        if not (0 <= rel < 0x10_000_000):
            print(f"  refusing: 0x{addr:012X} is outside the module "
                  "(looks dynamic, not a static offset).")
            return

        store = OffsetStore.load()
        store.set(field_name, rel)
        print(f"  saved {field_name} = {MODULE_NAME}+0x{rel:X} "
              f"to {store.path.name}. It loads automatically next client start.")

    def show_saved(self) -> None:
        store = OffsetStore.load()
        if not store.offsets:
            print(f"  {store.path.name}: no saved offsets yet.")
            return
        print(f"  {store.path.name} (module={store.module}, "
              f"version={store.version}):")
        for name, off in sorted(store.offsets.items()):
            print(f"    {name:18s} = {MODULE_NAME}+0x{off:X}")

    def forget(self, field_name: str) -> None:
        store = OffsetStore.load()
        if store.remove(field_name):
            print(f"  removed {field_name} from {store.path.name}.")
        else:
            print(f"  {field_name!r} was not saved.")

    def poke(self, value_str: str) -> None:
        """Write a value to the single surviving candidate (testing only)."""
        if self.vt is None:
            print("  set a type via `scan` first.")
            return
        if len(self.candidates) != 1:
            print(f"  refusing to poke: need exactly 1 candidate, "
                  f"have {len(self.candidates)}.")
            return
        target = self.vt.parse(value_str)
        self.mem.write_bytes(self.candidates[0], self.vt.pack(target))
        print(f"  wrote {target} to 0x{self.candidates[0]:012X}.")


# --------------------------------------------------------------------------- #
# Anchor validation
# --------------------------------------------------------------------------- #


def validate_anchor(mem: ProcessMemory) -> None:
    """Print attach info and read any currently-mapped offsets as a sanity check."""
    print(f"  attached: base=0x{mem.base_address:012X}  "
          f"module size=0x{mem.module_size:X}")
    mapped = OFFSETS.mapped()
    if not mapped:
        print("  no offsets mapped yet — use snapdiff/scan to discover them.")
        return
    print("  currently-mapped offsets read live:")
    for name, off in mapped.items():
        try:
            iv = mem.read_offset_int32(off)
            print(f"    {name:14s} (+0x{off:X}) = {iv}")
        except MemoryError_ as e:
            print(f"    {name:14s} (+0x{off:X}) read failed: {e}")


# --------------------------------------------------------------------------- #
# REPL
# --------------------------------------------------------------------------- #

HELP = """
commands:
  scan <type> <value>   full scan for a value   (types: int uint short byte float double)
  narrow <value>        keep candidates now holding <value>
  changed               keep candidates that changed since last snapshot
  unchanged             keep candidates that did NOT change
  list                  show current candidates
  offset                print in-module candidates as offsets.py dict
  save <field> [idx]    persist a candidate's offset to offsets.json (auto-loaded)
  saved                 list offsets already saved in offsets.json
  forget <field>        remove a saved offset from offsets.json
  poke <value>          write <value> to the sole candidate (test writes)
  count                 print candidate count
  reset                 clear candidates
  anchor                re-run the EXP anchor check
  help                  this text
  quit                  exit
"""


def repl(mem: ProcessMemory) -> None:
    sc = Scanner(mem)
    print(HELP)
    while True:
        try:
            line = input("scan> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        parts = line.split()
        cmd, rest = parts[0].lower(), parts[1:]
        try:
            if cmd in ("quit", "exit", "q"):
                return
            elif cmd == "help":
                print(HELP)
            elif cmd == "scan" and len(rest) >= 2:
                sc.scan(rest[0], rest[1])
            elif cmd == "narrow" and rest:
                sc.narrow(rest[0])
            elif cmd == "changed":
                sc.changed(True)
            elif cmd == "unchanged":
                sc.changed(False)
            elif cmd == "list":
                sc.list_candidates()
            elif cmd == "offset":
                sc.emit_offsets()
            elif cmd == "save" and rest:
                idx = int(rest[1]) if len(rest) > 1 else None
                sc.save_offset(rest[0], idx)
            elif cmd == "saved":
                sc.show_saved()
            elif cmd == "forget" and rest:
                sc.forget(rest[0])
            elif cmd == "poke" and rest:
                sc.poke(rest[0])
            elif cmd == "count":
                print(f"  {len(sc.candidates)} candidate(s)")
            elif cmd == "reset":
                sc.candidates = []
                sc._snapshot = None  # type: ignore[attr-defined]
                print("  cleared.")
            elif cmd == "anchor":
                validate_anchor(mem)
            else:
                print("  ? type `help`")
        except MemoryError_ as e:
            print(f"  memory error: {e}")


def main() -> int:
    print(f"Ys Origin offset scanner — attaching to {MODULE_NAME} ...")
    try:
        mem = ProcessMemory.attach(MODULE_NAME)
    except MemoryError_ as e:
        print(f"  failed to attach: {e}")
        print("  Make sure the game is running and you launched this as Admin.")
        return 1
    print(f"  attached: pid={mem.pid}, base=0x{mem.base_address:012X}")
    validate_anchor(mem)
    try:
        repl(mem)
    finally:
        mem.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
