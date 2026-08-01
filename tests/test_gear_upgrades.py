"""Offline guards for the gear-upgrade blessings ("Strengthen <piece>").

Live-confirmed on a Toal run: these do NOT set a bit like every other blessing.
The 0xAF armor/leggings cases call FUN_00420200 with a selector read from
0x76BB7C / 0x76BB80, and those hold the EQUIPPED piece's own item index — so the
upgrade writes a level into the raval array at 0x36A654 + item_idx*4 (Riveted
Leather 0x12 -> slot 18, Riveted Boots 0x2A -> slot 42, both observed directly).

That makes every piece its own repeatable-once check, and it makes the old single
"Strengthen current armor" location wrong: its hardcoded 0x36A684 is raval slot
12 = Leather Tunic = HUGO's starting armor, so it could never fire for Yunica or
Toal.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ys_origin_data_tables_gear", _ROOT / "ys_origin" / "data_tables.py"
)
dt = importlib.util.module_from_spec(_spec)
sys.modules["ys_origin_data_tables_gear"] = dt
_spec.loader.exec_module(dt)

_LOCS = json.loads((_ROOT / "ys_origin" / "data" / "locations.json")
                   .read_text(encoding="utf-8"))
_ITEMS = json.loads((_ROOT / "ys_origin" / "data" / "items.json")
                    .read_text(encoding="utf-8"))
RAVAL = 0x36A654
CHARS = ("yunica", "hugo", "toal")

_GEAR = [l for l in _LOCS if l["id"].startswith("blessing/gear/")]


def test_ten_upgrades_per_character():
    """5 armor + 5 boots each; the EX-character variant in every 6-slot band is
    excluded (it is never obtainable by the three playable characters)."""
    assert len(_GEAR) == 30, len(_GEAR)
    for ch in CHARS:
        mine = [l for l in _GEAR if l["char"] == ch]
        assert len(mine) == 10, (ch, len(mine))


def test_detect_slot_is_the_pieces_own_item_index():
    """The whole design rests on this identity — if it ever drifts the checks
    silently stop firing."""
    for l in _GEAR:
        piece = l["name"].split("Strengthen ", 1)[1]
        idx = _ITEMS[piece]
        assert int(l["detect"]["offset"], 16) == RAVAL + idx * 4, l["name"]
    # the two slots measured in-game
    by_name = {l["name"]: l for l in _GEAR}
    assert int(by_name["Divine Blessing: Strengthen Riveted Leather"]
               ["detect"]["offset"], 16) == RAVAL + 18 * 4
    assert int(by_name["Divine Blessing: Strengthen Riveted Boots"]
               ["detect"]["offset"], 16) == RAVAL + 42 * 4


def test_characters_do_not_share_a_slot():
    """Armor/boots bands are disjoint per character, so no two characters' checks
    can collide — which is what lets these be character-scoped locations."""
    seen = {}
    for l in _GEAR:
        off = l["detect"]["offset"]
        assert off not in seen, (off, l["name"], seen[off])
        seen[off] = l["name"]


def test_locations_are_character_scoped():
    enabled = {"blessing"}
    for ch in CHARS:
        names = {n for v in dt.locations_by_region(enabled, ch).values() for n in v}
        mine = {l["name"] for l in _GEAR if l["char"] == ch}
        others = {l["name"] for l in _GEAR if l["char"] != ch}
        assert mine <= names, ch
        assert not (others & names), (ch, sorted(others & names)[:3])


def test_every_gate_names_a_real_item():
    """A gate naming a non-item is never satisfiable, and AP's Has() reads the
    PROGRESSION counter — so anything named here must also be promoted to
    progression in create_item (see YsOriginWorld.gear_gate_items) or the
    location is unreachable and generation fails."""
    for ch in CHARS:
        for progressive in (False, True):
            gates = dt.gear_upgrade_gates(ch, progressive)
            assert len(gates) == 10, (ch, progressive, len(gates))
            ungated = [n for n, (i, _) in gates.items() if not i]
            # exactly one piece is worn from the start: the character's base armor
            assert len(ungated) == 1, (ch, progressive, ungated)
            for name, (item, count) in gates.items():
                if not item:
                    continue
                assert item in _ITEMS or item in (dt.PROGRESSIVE_ARMOR,
                                                  dt.PROGRESSIVE_BOOTS), item
                assert count >= 1
                if progressive:
                    assert item in (dt.PROGRESSIVE_ARMOR, dt.PROGRESSIVE_BOOTS), item


def _run_all():
    for k, v in sorted(globals().items()):
        if k.startswith("test_") and callable(v):
            v()
            print(f"ok  {k}")


if __name__ == "__main__":
    _run_all()
