"""Guards for the tiered blessing chains (LV1 -> LV2 -> LV3).

Shuffled independently, the seven levelled blessings can hand you LV3 before LV1
— nonsense in-fiction and a balance jump. Each family becomes one chain and the
mod sets the next unowned bit in list order, so the tiers can only arrive in
sequence. The groups are derived from locations.json, so these tests mostly exist
to catch the data drifting out from under that derivation.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ys_origin_data_tables_bless", _ROOT / "ys_origin" / "data_tables.py"
)
dt = importlib.util.module_from_spec(_spec)
sys.modules["ys_origin_data_tables_bless"] = dt
_spec.loader.exec_module(dt)

_LOCS = json.loads((_ROOT / "ys_origin" / "data" / "locations.json")
                   .read_text(encoding="utf-8"))
_BIT_LOCS = {l["detect"]["bit"]: l["name"] for l in _LOCS
             if l.get("type") == "blessing"
             and l.get("detect", {}).get("method") == "bit"}


def test_seven_families_are_found():
    assert len(dt.PROGRESSIVE_BLESSINGS) == 7, sorted(dt.PROGRESSIVE_BLESSINGS)


def test_bits_are_in_level_order():
    """The mod walks this list front to back — order IS the tier order."""
    for chain, bits in dt.PROGRESSIVE_BLESSINGS.items():
        levels = [int(re.search(r"LV(\d)$", _BIT_LOCS[b]).group(1)) for b in bits]
        assert levels == list(range(1, len(bits) + 1)), (chain, levels)


def test_every_bit_is_a_real_distinct_blessing_bit():
    seen = set()
    for chain, bits in dt.PROGRESSIVE_BLESSINGS.items():
        for b in bits:
            assert b in _BIT_LOCS, (chain, b)
            assert 0 <= b <= 31, (chain, b)
            assert b not in seen, (chain, b)   # a bit in two chains = lost tier
            seen.add(b)


def test_chain_replaces_exactly_its_own_tier_items():
    """Off: one item per tier. On: one chain copy per tier, same total — dropping
    or adding items here would desync the pool from the location count."""
    for ch in ("yunica", "hugo", "toal"):
        plain = dt.vanilla_items({"blessing"}, ch, blessing_items=True)
        prog = dt.vanilla_items({"blessing"}, ch, blessing_items=True,
                                progressive_blessings=True)
        assert len(plain) == len(prog), (ch, len(plain), len(prog))
        tiered = [i for i in plain if dt.progressive_blessing_for(i)]
        assert tiered, ch
        assert not (set(prog) & set(tiered)), ch
        for chain in dt.PROGRESSIVE_BLESSINGS:
            n = len([i for i in tiered if dt.progressive_blessing_for(i) == chain])
            assert prog.count(chain) == plain.count(chain) + n, (ch, chain)


def test_inert_without_blessing_items():
    """The effects have to BE items before they can be made progressive."""
    for ch in ("yunica", "hugo", "toal"):
        assert dt.vanilla_items({"blessing"}, ch, progressive_blessings=True) == \
               dt.vanilla_items({"blessing"}, ch)


def _run_all():
    for k, v in sorted(globals().items()):
        if k.startswith("test_") and callable(v):
            v()
            print(f"ok  {k}")


if __name__ == "__main__":
    _run_all()
