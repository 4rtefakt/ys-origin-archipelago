"""Offline tests for the blessing shop rando + overlay tracker slot data.

Run directly::

    python -m tests.test_shop_tracker

Covers:

  * blessings are no longer EXCLUDED (real/progression items can sit there);
    boss/room stay filler-only;
  * ``scene_locations_map`` / ``floor_locations_map`` — the per-room and
    per-floor tracker feeds — resolve active locations to AP ids sensibly;
  * ``blessing_location_names`` yields the 24 short shop names.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ys_origin_data_tables_shop", _ROOT / "ys_origin" / "data_tables.py"
)
dt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dt)

ALL_ACTIVE = set(dt.LOC_META)                 # every location active
IDS = dt.location_name_to_id


def test_blessings_not_excluded_anymore():
    assert "blessing" not in dt.EXCLUDED_TYPES
    assert {"boss", "room"} <= dt.EXCLUDED_TYPES
    assert not dt.is_excluded("Divine Blessing: Increase SP gain")
    assert dt.is_excluded("Boss: 5F Velagunder (S_1099)")


def test_scene_locations_map():
    m = dt.scene_locations_map(ALL_ACTIVE, IDS)
    assert m, "expected scene-tied locations"
    # a known chest lands under its scene
    assert IDS["Wailing Blue: 2F Path 1"] in m["1001"]
    # ids are ints, keys are numeric scene strings
    for k, v in m.items():
        assert k.isdigit() and v and all(isinstance(i, int) for i in v), k
    # blessings/floors have no scene -> never included
    bless_ids = {IDS[n] for n, l in dt.LOC_META.items() if l["type"] == "blessing"}
    assert not bless_ids & {i for v in m.values() for i in v}


def test_floor_locations_map():
    m = dt.floor_locations_map(ALL_ACTIVE, IDS)
    assert set(m) <= {str(f) for f in range(1, 26)}
    assert IDS["Reach 5F"] in m["5"]                       # floor checks belong to N
    assert IDS["Wailing Blue: 2F Path 1"] in m["2"]        # chests via their scene
    # respects the active set: nothing appears when its category is off
    no_floor = dt.floor_locations_map(
        {n for n, l in dt.LOC_META.items() if l["type"] != "floor"}, IDS)
    assert IDS["Reach 5F"] not in {i for v in no_floor.values() for i in v}


def test_blessing_names_map():
    m = dt.blessing_location_names(ALL_ACTIVE, IDS)
    assert len(m) == 24
    for k, v in m.items():
        assert k.isdigit(), k
        assert not v.startswith("Divine Blessing"), v      # short shop names
    assert "Increase SP gain" in m.values()


def test_blessing_bit_location_ids():
    ids = dt.blessing_bit_location_ids(ALL_ACTIVE, IDS)
    # 23 bit-method blessings (the armor blessing is flag-method -> excluded,
    # it stays vanilla-menu-only)
    assert len(ids) == 23
    armor = IDS["Divine Blessing: Strengthen current armor"]
    assert armor not in ids
    assert ids == sorted(ids, key=lambda i: {v: k for k, v in IDS.items()}[i])
    # deterministic: same call, same order
    assert ids == dt.blessing_bit_location_ids(ALL_ACTIVE, IDS)


def test_blessing_price_ladder_shape():
    """Cheap early, steep only at the end, and never below the floor.

    A LINEAR ladder over the default 100..100,000 range would put the midpoint at
    ~50k, which is exactly the "grind 30k SP on Floor 4" complaint. The geometric
    curve has to keep the bottom of the shop in the low hundreds.
    """
    n = len(dt.blessing_bit_location_names({l["name"] for l in dt._LOCS}))
    assert n >= 10, n
    lad = dt.blessing_price_ladder(n, 100, 100_000)
    assert len(lad) == n
    assert lad == sorted(lad), "ladder must be cheapest-first"
    assert lad[0] == 100 and lad[-1] == 100_000
    # the cheapest third stays affordable for an early-tower player
    assert lad[n // 3] <= 1500, lad[:n // 3 + 1]
    # and the midpoint is nowhere near half the maximum (that would be linear)
    assert lad[n // 2] < 10_000, lad[n // 2]


def test_expensive_blessing_slots_are_gated():
    """Every slot priced for the endgame must require reaching real tower depth.

    This is the rail that stops the fill parking a sphere-1 progression item
    behind a five-figure SP wall (the playtest saw a 500k slot holding one).
    """
    n = len(dt.blessing_bit_location_names({l["name"] for l in dt._LOCS}))
    lad = dt.blessing_price_ladder(n, 100, 100_000)
    for i, price in enumerate(lad):
        rank = i / (n - 1)
        floor = dt.price_gate_floor(rank)
        if price >= 10_000:
            assert floor, f"{price} SP slot has no depth gate"
        if price >= 50_000:
            assert floor and floor >= 19, (price, floor)
    # the cheapest slot is always free to reach
    assert dt.price_gate_floor(0.0) is None
    assert dt.price_gate_region(0.0) is None


def test_price_gate_is_depth_not_medallion():
    """The gate must be a reachable REGION, never an item.

    Medallions are not a depth proxy: with statue warps on, _set_rules_open drops
    the medallion backbone and the tower is traversable without them, so a
    medallion gate would lock a warped-ahead player out of a slot they can
    trivially afford. Regressing to an item gate would silently reintroduce that.
    """
    assert not hasattr(dt, "price_gate_item"), \
        "price_gate_item is the old medallion gate; use price_gate_region"
    n = len(dt.blessing_bit_location_names({l["name"] for l in dt._LOCS}))
    seen = []
    for i in range(n):
        reg = dt.price_gate_region(i / (n - 1))
        if reg:
            seen.append(reg)
            # a region, not an item name
            assert ":" in reg and reg.startswith("S_"), reg
    assert seen, "no slot is gated at all"
    # bands must get strictly deeper as price rises (never step backwards)
    floors = [dt.price_gate_floor(i / (n - 1)) or 0 for i in range(n)]
    assert floors == sorted(floors), floors


def test_gate_regions_exist_in_both_graph_modes():
    """A rule naming a region the world never creates fails generation."""
    linear = set(dt.ALL_REGIONS)
    open_regions = set(dt.open_regions())
    n = len(dt.blessing_bit_location_names({l["name"] for l in dt._LOCS}))
    for i in range(n):
        reg = dt.price_gate_region(i / (n - 1))
        if reg:
            assert reg in linear, f"{reg} missing from linear-mode ALL_REGIONS"
            assert reg in open_regions, f"{reg} missing from open-mode regions"


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
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
