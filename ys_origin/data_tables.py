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
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

_DATA_PATH = Path(__file__).parent / "data" / "locations.json"
_ITEMS_PATH = Path(__file__).parent / "data" / "items.json"
_ROOM_LOGIC_PATH = Path(__file__).parent / "data" / "room_logic.json"

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

# Filler for sanity locations. ONLY items that are safe to grant in unbounded
# quantity may go here:
#   * the "X Drop N" items (Strength/Defense/MP/Recovery Drop) are transient enemy
#     combat orbs, not held inventory — excluded (enemies still drop them).
#   * Roda Fruit and Cleria Ore are count-capped in vanilla; granting extra copies
#     bugs out / overpowers the player — excluded (they still appear at their
#     vanilla locations at the correct count).
#   * Celcetan Panacea and Gold are safe to give in any quantity. (SP would be a
#     nice filler too but it's a stat, not an item — needs a custom grant path.)
FILLER_POOL: List[str] = [
    "Celcetan Panacea",
    "50G", "100G", "500G", "1000G",
]


def _load() -> List[dict]:
    return json.loads(_DATA_PATH.read_text(encoding="utf-8"))


_LOCS = _load()

# -- stable maps over ALL locations / items --------------------------------- #

LOC_META: Dict[str, dict] = {l["name"]: l for l in _LOCS}
location_name_to_id: Dict[str, int] = {
    name: LOC_BASE_ID + i for i, name in enumerate(sorted(LOC_META))
}

# --------------------------------------------------------------------------- #
# Scene (room) graph.
#
# Region identity is the SCENE id: scene -> room and scene -> zone are clean
# functions, while room NAMES repeat across zones. current_scene (+0x36C100)
# exposes the scene live, so it is also the detection key.
#
# Two tiers of regions:
#   * the 6 tower ZONE regions + Menu — a linear, boss-medallion-gated backbone
#     (the coarse beatability backstop, unchanged from the old design);
#   * one region per SCENE. By default every scene connects from its zone region
#     (free) -> reachable as soon as the zone is. `room_logic.json` can AUTHOR a
#     scene to instead require an explicit adjacency + per-edge items/skills.
# --------------------------------------------------------------------------- #

def _scene_of(loc: dict) -> str:
    """Canonical scene id for a location ('S_1004'), or '' if it has none
    (blessings; floor checks are mapped to a representative scene separately)."""
    s = loc.get("detect", {}).get("scene")
    if s:
        return s.split("/")[0]
    m = re.match(r"(S_\d+)", loc.get("id", ""))
    return m.group(1) if m else ""


# scene -> room name / zone / floor (functions over the dataset).
SCENE_ROOM: Dict[str, str] = {}
SCENE_ZONE: Dict[str, str] = {}
SCENE_FLOOR: Dict[str, str] = {}
for _l in _LOCS:
    _sc = _scene_of(_l)
    if not _sc:
        continue
    SCENE_ROOM.setdefault(_sc, _l.get("room", "") or _sc)
    SCENE_ZONE.setdefault(_sc, _l.get("zone", ""))
    if _l.get("floor"):
        SCENE_FLOOR.setdefault(_sc, _l["floor"])

# zones that actually appear, in tower order.
_present = [z for z in ZONE_ORDER if any(z == SCENE_ZONE.get(s) for s in SCENE_ROOM)]


def scene_region(scene: str) -> str:
    """Region name for a scene, e.g. 'S_1004: 2F Path 2 (Wind Skill)'."""
    room = SCENE_ROOM.get(scene, scene)
    return f"{scene}: {room}"


def scene_names() -> Dict[str, str]:
    """Scene leaf number (as a string, e.g. '1004') -> room name, published in
    slot_data so the in-game overlay can show the current room from
    g_flags[0x1F9] (the mod has no scene table of its own)."""
    return {str(int(s[2:])): SCENE_ROOM[s] for s in SCENE_ROOM}


# representative scene per (zone, floor): the lowest-numbered scene on that
# floor — reaching it == reaching the floor, so "Reach NF" attaches there.
_floor_rep: Dict[Tuple[str, str], str] = {}
for _sc in sorted(SCENE_ROOM, key=lambda s: int(s[2:])):
    _key = (SCENE_ZONE.get(_sc, ""), SCENE_FLOOR.get(_sc, ""))
    _floor_rep.setdefault(_key, _sc)

# Authored room logic. `scenes`: a scene present here is "authored" — it gets
# only its listed incoming edges (default zone edge suppressed). `locations`: a
# per-location region OVERRIDE (location name -> scene id or zone name) for
# locations physically reached from a different room than their script scene
# (e.g. an elevated chest entered from above, or a boss drop mis-scened to a
# chest's room).
_room_logic_doc: dict = json.loads(_ROOM_LOGIC_PATH.read_text("utf-8"))
_room_logic: dict = _room_logic_doc.get("scenes", {})
_loc_region_override: Dict[str, str] = _room_logic_doc.get("locations", {})
# zone -> the authored scene you physically EXIT through to the next zone. For an
# authored zone we route the next zone's entry from this room (so the next zone
# is only reachable after the full intra-zone traversal), instead of the coarse
# zone->zone "highway" that would let AP bypass the room gates.
_zone_exits: Dict[str, str] = _room_logic_doc.get("zone_exits", {})


def _src_region(src: str) -> str:
    """An edge source is either a scene id (-> its region) or a zone name."""
    return scene_region(src) if re.match(r"S_\d+$", src) else src


# Build the region list, the connection edges, and per-edge requirements.
# Requirement expr: list of terms (AND); a term that is a list is an OR-group.
ALL_REGIONS: List[str] = [MENU] + _present + [scene_region(s) for s in sorted(SCENE_ROOM)]
CONNECTIONS: List[Tuple[str, str]] = []
EDGE_REQS: Dict[Tuple[str, str], list] = {}


def _add_edge(src: str, dst: str, req: Optional[list] = None) -> None:
    CONNECTIONS.append((src, dst))
    if req:
        EDGE_REQS[(src, dst)] = req


# Zone backbone (Menu -> z0 -> z1 -> ...). For an AUTHORED zone (one with a
# zone_exit), the next zone is entered from that physical exit ROOM, so you can't
# skip the zone's internal gates; un-authored zones use the coarse zone->zone
# edge. Boss-medallion gates are applied in rules.py via active_gates() (and for
# an authored zone the medallion is already required deeper in, e.g. at the boss
# door, so the coarse gate is just a redundant backstop).
if _present:
    _add_edge(MENU, _present[0])
    for _a, _b in zip(_present, _present[1:]):
        _exit = _zone_exits.get(_a)
        _add_edge(scene_region(_exit) if _exit else _a, _b)

# Scene edges: authored -> explicit; otherwise default from the zone region.
for _sc in sorted(SCENE_ROOM):
    _dst = scene_region(_sc)
    _spec = _room_logic.get(_sc)
    if _spec and _spec.get("from"):
        for _edge in _spec["from"]:
            _src, _req = (_edge[0], _edge[1] if len(_edge) > 1 else [])
            _add_edge(_src_region(_src), _dst, _req)
    else:
        _zone = SCENE_ZONE.get(_sc)
        _add_edge(_zone if _zone in _present else MENU, _dst)


# Every item named in a room-logic requirement MUST be treated as progression,
# else AP may place it out of logic (as filler/useful) and soft-lock the seed.
def _collect_gate_items() -> Set[str]:
    out: Set[str] = set()
    for _req in EDGE_REQS.values():
        for _term in _req:
            if isinstance(_term, (list, tuple)):
                out.update(_term)
            else:
                out.add(_term)
    return out


GATE_ITEMS: Set[str] = _collect_gate_items()

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
    if name == GOAL_ITEM or name in GATE_ITEMS:
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


def _region_of_location(l: dict) -> str:
    """The scene-region a location lives in.

    * scene-bearing (chest/event/statue/boss/room) -> its scene region;
    * floor checks ("Reach NF", no scene) -> the representative scene of that
      (zone, floor) so they inherit the room logic on the way up;
    * blessings / anything with no tower scene -> Menu (always reachable).

    A `locations` override in room_logic.json wins over the scene-derived region
    (for chests/drops reached from a different room than their script scene).
    """
    override = _loc_region_override.get(l["name"])
    if override:
        return _src_region(override)
    scene = _scene_of(l)
    if scene and scene in SCENE_ROOM:
        return scene_region(scene)
    if l.get("type") == "floor":
        rep = _floor_rep.get((l.get("zone", ""), l.get("floor", "")))
        if rep:
            return scene_region(rep)
    zone = l.get("zone", "")
    return zone if zone in _present else MENU


def locations_by_region(enabled: Set[str]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = defaultdict(list)
    for l in _LOCS:
        if l["type"] not in enabled:
            continue
        out[_region_of_location(l)].append(l["name"])
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


def req_satisfied(req: list, state, player) -> bool:
    """Evaluate a room-logic requirement expr against an AP CollectionState.

    ``req`` is a list of terms ANDed together; a term that is itself a list is
    an OR-group (any one suffices). Unknown item names are treated as
    unobtainable (so a typo fails closed rather than silently passing)."""
    for term in req:
        if isinstance(term, (list, tuple)):
            if not any(state.has(x, player) for x in term):
                return False
        elif not state.has(term, player):
            return False
    return True


def edge_requirements() -> Dict[Tuple[str, str], list]:
    """(src_region, dst_region) -> requirement expr, for every authored edge."""
    return dict(EDGE_REQS)
