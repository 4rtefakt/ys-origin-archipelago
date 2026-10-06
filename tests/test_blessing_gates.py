"""Offline tests for the blessing shop's logic gates (dt.blessing_slot_floors).

A shop slot is in logic only once the player can reach the tower depth its
price implies. Two holes let progression land where a player could not buy it:

  * tiers were gated independently, so a cheap LV3 sat in sphere 1 behind two
    22F-priced lower tiers the statue menu makes you buy first — "the Blue Moon
    Crest was behind level 3 of the most expensive item in the store" (Discord,
    Aug 2026);
  * gates were by rank on the seed's own ladder, so a wide range (min 5000) left
    25,000-SP slots ungated; and vanilla prices were never gated at all.

Run directly::

    python -m tests.test_blessing_gates
"""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ys_origin_data_tables_bg", _ROOT / "ys_origin" / "data_tables.py")
dt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dt)

_SLOTS = dt.blessing_bit_location_names({l["name"] for l in dt._LOCS})


def _roll(rng: random.Random, cmin: int, cmax: int):
    slots = list(_SLOTS)
    rng.shuffle(slots)
    ladder = dt.blessing_price_ladder(len(slots), cmin, cmax)
    prices = dict(zip(slots, ladder))
    ranks = {n: i / (len(slots) - 1) for i, n in enumerate(slots)}
    return prices, ranks, dt.blessing_slot_floors(prices, ranks)


def _f(x):
    return x or 0


def test_there_are_tiered_blessings_to_guard():
    assert len(_SLOTS) >= 20
    assert dt.PROGRESSIVE_BLESSINGS, "the tier groups must be derived from the data"


def test_a_tier_is_never_easier_than_the_tiers_below_it():
    by_bit = {dt.blessing_bit_of(n): n for n in _SLOTS}
    rng = random.Random(7)
    for cmin, cmax in ((100, 8000), (100, 100_000), (100, 500_000), (5000, 500_000)):
        for _ in range(300):
            _, _, floors = _roll(rng, cmin, cmax)
            for bits in dt.PROGRESSIVE_BLESSINGS.values():
                tiers = [by_bit[b] for b in bits if b in by_bit]
                for lo, hi in zip(tiers, tiers[1:]):
                    assert _f(floors[hi]) >= _f(floors[lo]), (cmin, cmax, lo, hi, floors)


def test_real_money_is_gated_whatever_the_range():
    rng = random.Random(11)
    for cmin, cmax in ((100, 100_000), (5000, 500_000), (10, 500_000)):
        for _ in range(300):
            prices, _, floors = _roll(rng, cmin, cmax)
            for n, p in prices.items():
                if p >= 10_000:
                    assert _f(floors[n]) >= 14, (cmin, cmax, n, p, floors[n])


def test_tiers_pay_for_the_tiers_below_them():
    """LVn costs LV1..LVn together: three 4,000-SP tiers are a 12,000-SP buy."""
    family = max(dt.PROGRESSIVE_BLESSINGS.values(), key=len)
    by_bit = {dt.blessing_bit_of(n): n for n in _SLOTS}
    tiers = [by_bit[b] for b in family if b in by_bit]
    prices = {n: 100 for n in _SLOTS}
    for n in tiers:
        prices[n] = 4_000
    floors = dt.blessing_slot_floors(prices, {n: 0.0 for n in _SLOTS})
    assert _f(floors[tiers[0]]) == 10            # 4,000 SP on its own
    assert _f(floors[tiers[-1]]) == _f(dt.sp_gate_floor(4_000 * len(tiers)))
    assert len(tiers) < 3 or _f(floors[tiers[-1]]) == 14   # 12,000 SP together


def test_cheap_untiered_slots_stay_open():
    prices = {n: 100 for n in _SLOTS}
    floors = dt.blessing_slot_floors(prices, {n: 0.0 for n in _SLOTS})
    assert all(f is None for f in floors.values())


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
