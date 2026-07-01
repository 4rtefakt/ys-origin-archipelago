"""Offline tests for the New-Game starting-loadout slot-data helpers.

Run directly::

    python -m tests.test_start_loadout

Loads ``ys_origin.data_tables`` in isolation (its package ``__init__`` needs
Archipelago's ``BaseClasses``, which isn't present offline) and checks the two
pure helpers the generator publishes into slot data:

  * ``weapon_value_for_level`` maps a displayed weapon level (1-6) to the
    g_flags[0x94] record value, matching the mod's kWeaponTier ladder, and clamps;
  * ``start_item_indices`` resolves item names to g_flags indices, dropping
    unknown names and de-duplicating (so a typo can't break generation).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ys_origin_data_tables_sl", _ROOT / "ys_origin" / "data_tables.py"
)
dt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dt)


def test_weapon_value_ladder():
    # displayed Lv -> g_flags[0x94] record value (0, then 1,2,4,6,8 for Lv2..6).
    assert [dt.weapon_value_for_level(l) for l in range(1, 7)] == [0, 1, 2, 4, 6, 8]


def test_weapon_value_clamps():
    assert dt.weapon_value_for_level(0) == dt.weapon_value_for_level(1) == 0
    assert dt.weapon_value_for_level(99) == dt.weapon_value_for_level(6) == 8


def test_start_items_default_resolves():
    # the yaml default (the two warp Crystals) must resolve to real g_flags idxs.
    idxs = dt.start_item_indices(["Crystal", "Dark Crystal"])
    assert idxs == [dt.item_index["Crystal"], dt.item_index["Dark Crystal"]]
    assert all(isinstance(i, int) for i in idxs)


def test_start_items_skips_unknown_and_dedups():
    idxs = dt.start_item_indices(["Crystal", "Not A Real Item", "Crystal"])
    assert idxs == [dt.item_index["Crystal"]]        # unknown dropped, dup removed


def test_start_items_empty():
    assert dt.start_item_indices([]) == []


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
