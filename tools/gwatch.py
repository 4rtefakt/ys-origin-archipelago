"""High-frequency g_flags transition watcher (live suppression verification).

Unlike ``flaglog.py`` (which coalesces change-bursts into pickup *events* at a
slow poll), ``gwatch`` polls a *specific* set of g_flags indices at ~100 ms and
prints **every** transition with a timestamp. That resolution is what catches a
vanilla grant that flashes for one client poll (~500 ms) and is then reverted by
the suppressor — the exact thing we want to confirm live.

Usage (from the repo root, with the game running):

    python -m tools.gwatch 0x59 0x7D 0x12E           # watch specific indices
    python -m tools.gwatch --names Panacea 500G       # by item name (items.json)
    python -m tools.gwatch --range 0x50 0x80          # watch a contiguous range
    python -m tools.gwatch                            # default: the 2F-Path-1 test set

Each line: ``+1.234s  idx 0x59 (Celcetan Panacea)  -1 -> 1``. Ctrl-C to stop.
Read-only (ReadProcessMemory) — safe to run alongside the AP client.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from client.memory import ProcessMemory, MemoryError_
from client.offsets import GFLAGS_BASE, MODULE_NAME

_ITEMS_PATH = Path(__file__).parent.parent / "ys_origin" / "data" / "items.json"


def _load_names() -> dict[int, str]:
    """idx -> item name, from the apworld's items.json (best-effort)."""
    try:
        m = json.loads(_ITEMS_PATH.read_text(encoding="utf-8"))
    except OSError:
        return {}
    out: dict[int, str] = {}
    for name, idx in m.items():
        try:
            out[int(idx)] = name
        except (ValueError, TypeError):
            pass
    return out


def _parse_idx(tok: str) -> int:
    return int(tok, 16) if tok.lower().startswith("0x") else int(tok)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="High-freq g_flags transition watcher")
    ap.add_argument("indices", nargs="*", help="g_flags indices (hex 0x.. or dec)")
    ap.add_argument("--names", nargs="*", default=[],
                    help="item names to resolve to indices via items.json")
    ap.add_argument("--range", nargs=2, metavar=("LO", "HI"),
                    help="watch a contiguous index range [LO, HI)")
    ap.add_argument("--interval", type=float, default=0.1, help="poll seconds")
    args = ap.parse_args(argv)

    names = _load_names()
    name_to_idx = {v: k for k, v in names.items()}

    idx_set: set[int] = set()
    for tok in args.indices:
        idx_set.add(_parse_idx(tok))
    for nm in args.names:
        if nm in name_to_idx:
            idx_set.add(name_to_idx[nm])
        else:
            print(f"warning: name {nm!r} not in items.json", file=sys.stderr)
    if args.range:
        lo, hi = _parse_idx(args.range[0]), _parse_idx(args.range[1])
        idx_set.update(range(lo, hi))
    if not idx_set:
        # Default: the 2F Path 1 suppression test set.
        # 0x59 = vanilla Celcetan Panacea, 0x7D = 500G (sample AP item),
        # 0x12E = the chest's box-open flag.
        idx_set = {0x59, 0x7D, 0x12E}

    indices = sorted(idx_set)

    def label(i: int) -> str:
        nm = names.get(i)
        return f"idx 0x{i:X} ({nm})" if nm else f"idx 0x{i:X}"

    print(f"attaching to {MODULE_NAME} ...", file=sys.stderr)
    mem = ProcessMemory.attach(MODULE_NAME)
    print(f"attached pid={mem.pid} base=0x{mem.base_address:X}; "
          f"watching {len(indices)} indices: "
          f"{', '.join(label(i) for i in indices)}", file=sys.stderr)

    def read_all() -> dict[int, int]:
        out: dict[int, int] = {}
        for i in indices:
            try:
                out[i] = mem.read_offset_int32(GFLAGS_BASE + i * 4)
            except MemoryError_:
                pass
        return out

    prev = read_all()
    print(f"  initial: "
          f"{', '.join(f'0x{i:X}={prev.get(i)}' for i in indices)}",
          file=sys.stderr)
    t0 = time.time()
    try:
        while True:
            time.sleep(args.interval)
            cur = read_all()
            for i in indices:
                a, b = prev.get(i), cur.get(i)
                if a is not None and b is not None and a != b:
                    print(f"+{time.time() - t0:7.3f}s  {label(i):40} {a:>4} -> {b}",
                          flush=True)
            prev = cur
    except KeyboardInterrupt:
        print("\nstopped.", file=sys.stderr)
    finally:
        mem.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
