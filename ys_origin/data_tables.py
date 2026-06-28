"""Load the extracted location set and derive the apworld tables.

``data/locations.json`` (built by ``tools/build_locations.py`` from the game's
event scripts + item table + scene list) is the single source of truth. Five
location categories: ``chest`` and ``event`` always count (they carry the
vanilla items, incl. progression); ``boss`` / ``floor`` / ``room`` are optional
"sanity" checks toggled per-YAML.

This module exposes stable name→id maps for *all* locations/items (AP needs
those fixed) plus helpers that select the active subset for a given option set.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set

_DATA_PATH = Path(__file__).parent / "data" / "locations.json"
_ITEMS_PATH = Path(__file__).parent / "data" / "items.json"

LOC_BASE_ID = 0x59_6000
ITEM_BASE_ID = 0x59_5000
MENU = "Menu"

ZONE_ORDER: List[str] = [
    "Wailing Blue", "Flooded Prison", "Flames of Guilt",
    "Silent Sands", "Corrupted Blood", "Demonic Core",
]

# Boss medallion gating entry to each zone (medallion from the zone below).
ZONE_GATE: Dict[str, str] = {
    "Flooded Prison": "Beast Medallion",
    "Flames of Guilt": "Arthropod Medallion",
    "Silent Sands": "Construct Medallion",
    "Corrupted Blood": "Creeper Medallion",
    "Demonic Core": "Mantid Medallion",        # now in pool via the S_5102 event
}

GOAL_ITEM = "Devil Medallion"

CATEGORIES = ("chest", "event", "statue", "blessing", "boss", "floor", "room")
ALWAYS_ON: Set[str] = {"chest", "event"}        # carry the real item pool

# Categories not yet confirmed live-detectable (scene offset unmapped) or whose
# index<->name map is provisional (blessings). Marked EXCLUDED so AP only puts
# FILLER there -> seeds stay beatable via the confirmed checks. Upgrade once
# their live detection / mapping is pinned.
EXCLUDED_TYPES: Set[str] = {"blessing", "boss", "room"}   # floor now live (0x36BC58)

# Varied filler for sanity locations (real INVINFO names; counts not gated).
FILLER_POOL: List[str] = [
    "Roda Fruit", "Celcetan Panacea", "Recovery Drop 1", "Recovery Drop 2",
    "Strength Drop 1", "Defense Drop 1", "MP Drop 1", "Cleria Ore",
    "100G", "500G", "1000G", "50G",
]


def _load() -> List[dict]:
    return json.loads(_DATA_PATH.read_text(encoding="utf-8"))


_LOCS = _load()

# -- stable maps over ALL locations / items --------------------------------- #

LOC_META: Dict[str, dict] = {l["name"]: l for l in _LOCS}
location_name_to_id: Dict[str, int] = {
    name: LOC_BASE_ID + i for i, name in enumerate(sorted(LOC_META))
}

# Region chain over the zones that actually appear.
_present = [z for z in ZONE_ORDER if any(l["zone"] == z for l in _LOCS)]
ALL_REGIONS: List[str] = [MENU] + _present
CONNECTIONS: List[tuple] = [(MENU, _present[0])] + list(zip(_present, _present[1:])) \
    if _present else []

# Vanilla item per location (canonical = first granted item), and the item
# universe (every item that can be created: vanilla + filler + goal).
location_vanilla_item: Dict[str, str] = {
    l["name"]: (l["items"][0]["name"] if l["items"] else "") for l in _LOCS
}
_item_class: Dict[str, str] = {}
for _l in _LOCS:
    for _it in _l["items"]:
        _item_class[_it["name"]] = _it["class"]

_universe = set(v for v in location_vanilla_item.values() if v) \
    | set(FILLER_POOL) | {GOAL_ITEM}
item_name_to_id: Dict[str, int] = {
    nm: ITEM_BASE_ID + i for i, nm in enumerate(sorted(_universe))
}


def item_classification(name: str) -> str:
    if name == GOAL_ITEM:
        return "progression"
    return _item_class.get(name, "filler")


# -- per-world selection helpers -------------------------------------------- #

def enabled_categories(opts) -> Set[str]:
    """Resolve which optional categories are on from the options dataclass."""
    on = set(ALWAYS_ON)
    if getattr(opts, "statue_checks", 1):
        on.add("statue")
    if getattr(opts, "blessing_checks", 1):
        on.add("blessing")
    if getattr(opts, "boss_checks", 1):
        on.add("boss")
    if getattr(opts, "floor_checks", 1):
        on.add("floor")
    if getattr(opts, "room_checks", 0):
        on.add("room")
    return on


def locations_by_region(enabled: Set[str]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = defaultdict(list)
    for l in _LOCS:
        if l["type"] not in enabled:
            continue
        # tower-zone locations go in their zone; the rest (e.g. blessings, bought
        # anywhere) attach to Menu, which is always reachable.
        region = l["zone"] if l["zone"] in _present else MENU
        out[region].append(l["name"])
    return dict(out)


# Detection methods the live client can actually observe today. A location whose
# method isn't one of these can't be checked in-game yet, so it must stay
# filler-only regardless of its category (e.g. an event that fell back to
# scene-method because it had no unique non-item flag).
LIVE_DETECT_METHODS: Set[str] = {"flag", "floor", "bit"}


def is_excluded(loc_name: str) -> bool:
    """True if AP should keep this location FILLER-only.

    Either its category is provisional (``EXCLUDED_TYPES``) or its detection is
    not yet live (scene-method). Placing progression where we can't detect the
    check live would soft-lock the seed; the location still contributes its
    vanilla item to the pool, it just won't *hold* progression.
    """
    meta = LOC_META.get(loc_name)
    if not meta:
        return False
    if meta["type"] in EXCLUDED_TYPES:
        return True
    return meta.get("detect", {}).get("method") not in LIVE_DETECT_METHODS


# item name -> g_flags item index (== INVINFO id); the client grants by writing
# that array entry. Published in slot_data so the client needs no local table.
item_index: Dict[str, int] = json.loads(_ITEMS_PATH.read_text(encoding="utf-8"))


def vanilla_items(enabled: Set[str]) -> List[str]:
    """The real items to seed the pool (one per enabled chest/event location)."""
    return [location_vanilla_item[l["name"]] for l in _LOCS
            if l["type"] in enabled and location_vanilla_item[l["name"]]]


def active_gates() -> Dict[str, str]:
    return {z: i for z, i in ZONE_GATE.items()
            if i in item_name_to_id and z in ALL_REGIONS}
