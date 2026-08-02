"""Every chest/event location with NO vanilla item must genuinely grant nothing.

A location's vanilla item is what feeds ``suppress_item_indices``. An empty item
list therefore means "nothing to suppress" — so if the script actually hands
something over, the player gets it for free on top of the AP item. That is
exactly how the elemental gems leaked: 0x80/0x81/0x82 have no INVINFO name, the
catalog filed them as junk hex placeholders and stripped them, and eight chests
were left empty while still powering up a skill.

This audit re-derives the answer from the extracted XSO corpus, per BOX (not per
scene — a scene has several chests and matching loosely gives false hits), and
allows an empty list only for the cases that are genuinely empty:

  * gold pickups — the flags exist but Ys Origin has no money, so they are
    deliberately not pool items;
  * grants whose item index is already carried by ANOTHER location, since
    suppression is keyed on the item index and not on the location (the Hugo
    variant of the Red Moon Crest pickup is the live example);
  * scripts that only award a conversation Topic, which is not poolable.

Skipped automatically when the corpus is not present (CI has no game assets).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CORPUS = Path(r"C:\Users\kores\repos\YsOrigin-CleriaCore\assets\MAP")

_spec = importlib.util.spec_from_file_location(
    "ys_origin_data_tables_empty", _ROOT / "ys_origin" / "data_tables.py")
dt = importlib.util.module_from_spec(_spec)
sys.modules["ys_origin_data_tables_empty"] = dt
_spec.loader.exec_module(dt)

_LOCS = json.loads((_ROOT / "ys_origin" / "data" / "locations.json")
                   .read_text(encoding="utf-8"))
_ITEMS = json.loads((_ROOT / "ys_origin" / "data" / "items.json")
                    .read_text(encoding="utf-8"))

# Gold: the item ids exist in the table but the game has no currency.
GOLD_IDS = {0x7B, 0x7C, 0x7D, 0x7E, 0x7F}


def _corpus_scripts():
    if not _CORPUS.is_dir():
        pytest.skip("XSO corpus not available")
    sys.path.insert(0, str(_ROOT))
    from tools.xso_dis import XSO
    return XSO, {p.name: p for p in _CORPUS.glob("S_*/S_*") if p.is_dir()}


def test_empty_locations_really_grant_nothing():
    XSO, scene_dir = _corpus_scripts()
    # every item index claimed by SOME location -> already in the suppress set
    claimed = {int(i["id"], 16) for l in _LOCS for i in l.get("items", [])}

    offenders = []
    for l in _LOCS:
        if l["type"] not in ("chest", "event") or l.get("items"):
            continue
        parts = l["id"].split("/")
        scene, box = parts[0], (parts[1] if len(parts) > 1 else "")
        d = scene_dir.get(scene)
        script = (d / f"{box}.XSO") if d and box else None
        if not (script and script.exists()):
            continue                      # event ids that aren't a script file
        seq = [i for i in XSO(script.read_bytes(), script.name).disasm()
               if i.cls == 2 and i.sub is not None]
        granted = {i.operands[0] for i in seq if i.sub == 0x116 and i.operands}
        granted |= {i.operands[0] for i in seq if i.sub in (0x64, 0x67)
                    and len(i.operands) >= 2 and 0x40 <= i.operands[0] <= 0x82
                    and i.operands[1] != 0}
        leaked = {g for g in granted if g not in GOLD_IDS and g not in claimed}
        if leaked:
            offenders.append((l["name"], l["id"],
                              sorted(f"0x{g:02X}" for g in leaked)))

    assert not offenders, (
        "these locations record no vanilla item but their script still grants "
        "one, so nothing suppresses it and the player gets it free on top of "
        "the AP item:\n" + "\n".join(f"  {n} [{i}] grants {g}"
                                     for n, i, g in offenders))


def test_the_known_gem_chests_are_populated():
    """Regression guard for the specific bug this audit was written after."""
    for gem in ("Emerald", "Ruby", "Topaz"):
        assert gem in _ITEMS, f"{gem} must stay a real item"
        n = sum(1 for l in _LOCS for i in l.get("items", [])
                if i["name"] == gem)
        assert n >= 2, f"{gem} should sit in several chests, found {n}"
