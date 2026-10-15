"""Rooms a character can never enter hold no location of theirs.

Since 2.2 room checks hold any item, so a location in a room the character cannot
reach could take their progression and make the seed unbeatable.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("ys_origin_data_tables_rooms", _ROOT / "ys_origin" / "data_tables.py")
dt = importlib.util.module_from_spec(_spec)
sys.modules["ys_origin_data_tables_rooms"] = dt
_spec.loader.exec_module(dt)

_LOCS = json.loads((_ROOT / "ys_origin" / "data" / "locations.json").read_text(encoding="utf-8"))

# scene -> characters who can never enter it (checked in the game)
_CLOSED = {
    "S_1012": {"toal"},   # 2F Gemma Room
}


def test_closed_rooms_hold_no_location_of_the_character():
    for loc in _LOCS:
        scene = loc.get("detect", {}).get("scene") or loc["id"].split("/")[0]
        for char in _CLOSED.get(scene, ()):
            assert not dt.location_for_char(loc, char), f"{loc['name']} exists for {char}"


def test_the_others_keep_them():
    gemma = [l for l in _LOCS if l["id"].startswith("S_1012/")]
    assert len(gemma) == 2
    for char in ("yunica", "hugo"):
        assert all(dt.location_for_char(l, char) for l in gemma)
