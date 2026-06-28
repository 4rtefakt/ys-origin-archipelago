"""Load the extracted chest dataset and derive the apworld tables.

``data/chests.json`` (built by ``tools/build_dataset.py`` from the game's own
event scripts + item table + scene list) is the single source of truth. This
module turns it into the regions / locations / items / gating the apworld needs.

Scope note: multi-item chests grant a per-character variant set; until the
character split is finalised we take each chest's **first** granted item as its
canonical vanilla content. The result is one location per chest, a vanilla item
pool, a linear tower of zone regions, and boss-medallion gates between them.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_DATA_PATH = Path(__file__).parent / "data" / "chests.json"

LOC_BASE_ID = 0x59_6000
ITEM_BASE_ID = 0x59_5000

MENU = "Menu"

# Darm Tower zones in ascent order (matches the dataset's zone strings' prefix).
ZONE_ORDER: List[str] = [
    "Wailing Blue",
    "Flooded Prison",
    "Flames of Guilt",
    "Silent Sands",
    "Corrupted Blood",
    "Demonic Core",
]

# Boss medallion that gates entry to each zone (medallion from the zone below).
# Only applied if the medallion is actually in the item pool (defensive).
ZONE_GATE: Dict[str, str] = {
    "Flooded Prison": "Beast Medallion",
    "Flames of Guilt": "Arthropod Medallion",
    "Silent Sands": "Construct Medallion",
    "Corrupted Blood": "Creeper Medallion",
}

GOAL_ITEM = "Devil Medallion"          # found in Demonic Core; in the pool
FILLER_ITEM_NAME = "Roda Fruit"


def _zone_short(zone_field: str) -> str:
    """'Wailing Blue (2-5F)' -> 'Wailing Blue'; '' -> 'Wailing Blue' fallback."""
    return zone_field.split(" (")[0].strip() if zone_field else ""


def _load() -> List[dict]:
    return json.loads(_DATA_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Build everything once at import.
# --------------------------------------------------------------------------- #


class _Tables:
    def __init__(self, chests: List[dict]):
        self.regions: List[str] = [MENU]
        self.connections: List[Tuple[str, str]] = []
        self.locations_by_region: Dict[str, List[str]] = defaultdict(list)
        self.location_name_to_id: Dict[str, int] = {}
        self.location_box_flag: Dict[str, Optional[str]] = {}
        self.location_vanilla_item: Dict[str, str] = {}

        present = [z for z in ZONE_ORDER
                   if any(_zone_short(c["zone"]) == z for c in chests)]
        # Region chain: Menu -> first zone -> ... (only zones we have chests for)
        prev = MENU
        for z in present:
            self.regions.append(z)
            self.connections.append((prev, z))
            prev = z

        # Locations: one per chest, named uniquely & readably.
        used: Counter = Counter()
        vanilla_items: Counter = Counter()
        for c in chests:
            zone = _zone_short(c["zone"]) or present[0]
            room = c.get("room") or c["id"]
            base = f"{zone}: {room}"
            used[base] += 1
            name = base if used[base] == 1 else f"{base} #{used[base]}"
            self.locations_by_region[zone].append(name)
            self.location_box_flag[name] = c.get("box_flag")
            item = c["items"][0]["name"] if c["items"] else FILLER_ITEM_NAME
            self.location_vanilla_item[name] = item
            vanilla_items[item] += 1

        for i, name in enumerate(sorted(self.location_box_flag)):
            self.location_name_to_id[name] = LOC_BASE_ID + i

        # Items: the vanilla pool (canonical item per chest), with classes.
        self._item_class: Dict[str, str] = {}
        for c in chests:
            if c["items"]:
                it = c["items"][0]
                self._item_class[it["name"]] = it["class"]
        self.item_counts: Dict[str, int] = dict(vanilla_items)
        self.item_name_to_id: Dict[str, int] = {
            nm: ITEM_BASE_ID + i for i, nm in enumerate(sorted(self.item_counts))
        }

    def item_classification(self, name: str) -> str:
        # Force the goal item to progression so logic can require it.
        if name == GOAL_ITEM:
            return "progression"
        return self._item_class.get(name, "filler")

    def active_gates(self) -> Dict[str, str]:
        """Zone gates whose required item is actually in the pool."""
        return {z: itm for z, itm in ZONE_GATE.items()
                if itm in self.item_counts and z in self.regions}


_T = _Tables(_load())

# Public surface (mirrors the names the apworld modules import).
ALL_REGIONS = _T.regions
CONNECTIONS = _T.connections
locations_by_region = dict(_T.locations_by_region)
location_name_to_id = _T.location_name_to_id
location_box_flag = _T.location_box_flag
location_vanilla_item = _T.location_vanilla_item
item_counts = _T.item_counts
item_name_to_id = _T.item_name_to_id
item_classification = _T.item_classification
active_gates = _T.active_gates
