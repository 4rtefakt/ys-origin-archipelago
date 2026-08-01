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
    """Every slot priced for the endgame must carry a zone-gate requirement.

    This is the rail that stops the fill parking a sphere-1 progression item
    behind a five-figure SP wall (the playtest saw a 500k slot holding one).
    """
    n = len(dt.blessing_bit_location_names({l["name"] for l in dt._LOCS}))
    lad = dt.blessing_price_ladder(n, 100, 100_000)
    for i, price in enumerate(lad):
        rank = i / (n - 1)
        gate = dt.price_gate_item(rank)
        if price >= 10_000:
            assert gate is not None, f"{price} SP slot has no logic gate"
        if price >= 50_000:
            # deep-tower medallions only
            assert gate in ("Creeper Medallion", "Mantid Medallion"), (price, gate)
    # the cheapest slot is always free to reach
    assert dt.price_gate_item(0.0) is None


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
