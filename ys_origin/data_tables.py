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
import pkgutil
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


def _read_data(relpath: str) -> str:
    """Read a bundled data file as UTF-8 text. Works both on disk (dev) AND
    inside a zipped ``.apworld`` (where a filesystem ``Path`` can't reach into
    the archive — must go through the package loader)."""
    if __package__:
        raw = pkgutil.get_data(__package__, relpath)
        if raw is not None:
            return raw.decode("utf-8")
    return (Path(__file__).parent / relpath).read_text(encoding="utf-8")

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
    return json.loads(_read_data("data/locations.json"))


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
_room_logic_doc: dict = json.loads(_read_data("data/room_logic.json"))
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

# -- character-aware item selection ----------------------------------------- #
# Yunica / Hugo / Toal climb the SAME tower but receive different equipment
# variants (the multi-item chests) + a few unique key items. character_items.json
# maps every pool item name -> the characters who can receive it (or ["shared"]).
# The item-id universe stays FULL (all variants, stable ids AP needs); per-world
# we only CREATE the selected character's items.
_CHAR_ITEMS: Dict[str, list] = json.loads(_read_data("data/character_items.json"))
_CHAR_BY_VALUE = {0: "yunica", 1: "hugo", 2: "toal"}

# All item variants at each location (for the full universe + per-char pick).
LOCATION_VARIANTS: Dict[str, List[str]] = {
    l["name"]: [it["name"] for it in l["items"]] for l in _LOCS
}
_item_class: Dict[str, str] = {}
for _l in _LOCS:
    for _it in _l["items"]:
        _item_class[_it["name"]] = _it["class"]


def char_name(opts_or_value) -> str:
    """'yunica' | 'hugo' | 'toal' from an options object or a raw int value."""
    v = getattr(opts_or_value, "character", opts_or_value)
    v = getattr(v, "value", v)
    try:
        return _CHAR_BY_VALUE.get(int(v), "hugo")
    except (TypeError, ValueError):
        return "hugo"


def item_allowed(name: str, char: str) -> bool:
    """True if item `name` can be received by character `char` (shared/filler/
    goal -> all characters)."""
    who = _CHAR_ITEMS.get(name)
    if who is None or "shared" in who:
        return True
    return char in who


def location_vanilla_item(loc_name: str, char: str = "hugo") -> str:
    """The vanilla item a location grants the given character — its per-character
    variant among the chest's items (fallback: the first variant)."""
    variants = LOCATION_VARIANTS.get(loc_name, [])
    if len(variants) <= 1:
        return variants[0] if variants else ""  # single item = shared by all
    for v in variants:                          # multi = pick the char's variant
        if item_allowed(v, char):
            return v
    return variants[0]


# Goddess-statue warp unlocks (optional, statue_warp_locks). One item per statue;
# receiving it lets the mod enable warping to that statue. Bonus/useful (not
# progression) -> they never change reachability (everything stays reachable on
# foot), so seeds are beatable regardless of where they land.
STATUE_UNLOCKS: Dict[str, dict] = {}
for _l in _LOCS:
    if _l["type"] != "statue":
        continue
    _m = re.match(r"(S_\d+)", _l.get("id", ""))
    _sc = _m.group(1) if _m else ""
    _nm = f"Statue Warp: {_l['room']} ({_sc})"
    STATUE_UNLOCKS[_nm] = {
        "scene": int(_sc[2:]) if _sc else 0,         # scene leaf number (g_flags[0x1F9])
        "flag": _l["detect"].get("offset", ""),      # the statue's activation flag
        "location": _l["name"],
    }

_universe = {v for vs in LOCATION_VARIANTS.values() for v in vs} \
    | set(FILLER_POOL) | {GOAL_ITEM} | set(STATUE_UNLOCKS)
item_name_to_id: Dict[str, int] = {
    nm: ITEM_BASE_ID + i for i, nm in enumerate(sorted(_universe))
}


def statue_unlock_items() -> List[str]:
    """The 21 statue-warp-unlock item names (added to the pool when the option
    is on, displacing filler)."""
    return list(STATUE_UNLOCKS)


def statue_unlock_slot_data() -> Dict[str, dict]:
    """item name -> {scene, flag} so the mod can map a received unlock to its
    statue."""
    return {k: {"scene": v["scene"], "flag": v["flag"]} for k, v in STATUE_UNLOCKS.items()}


def statue_location_scenes() -> Dict[str, str]:
    """statue LOCATION name -> "S_<scene>" scene tag. When warp-locks are on the
    statue check is detected by ENTERING the room (scene-method) instead of by
    the activation flag, so blocking purification no longer hides the check."""
    return {v["location"]: f"S_{v['scene']}" for v in STATUE_UNLOCKS.values()}


def statue_scenes() -> List[int]:
    """All statue scene leaf numbers (for picking a random start statue)."""
    return sorted({v["scene"] for v in STATUE_UNLOCKS.values()})


# Expected character level per tower floor, from the bundled Hugo guide's
# per-boss "Your Level" recommendations, interpolated for the floors between.
# Used by the mod's catch-up level scaling (level floor + EXP multiplier) so
# warping far ahead doesn't strand you under-leveled. Floor number == the game's
# current_floor (g_flags[0xCF]).
# Recommended player level per floor, from the guide's per-boss "Your Level" plus
# the Steam "Recommended Boss Levels" reference (they agree). Boss/chamber floors
# are the anchors; non-boss floors interpolated. NOTE the steep Demonic Core spike
# (21F~32 -> 22F~41) and that useful levels run to ~52 at the summit (max 60).
FLOOR_LEVELS: Dict[int, int] = {
    1: 1, 2: 4, 3: 6, 4: 8, 5: 9,           # Wailing Blue (5F Beast 9)
    6: 11, 7: 13, 8: 15, 9: 17,             # Flooded Prison (9F Arthropod 17)
    10: 18, 11: 19, 12: 20, 13: 21,         # Flames of Guilt (13F Monk 20, win 23)
    14: 22, 15: 23, 16: 24, 17: 25,         # Silent Sands (17F Construct 25; Rado 29)
    18: 28, 19: 30, 20: 31, 21: 32,         # Corrupted Blood (21F Creeper 32)
    22: 41, 23: 44, 24: 47, 25: 51,         # Demonic Core / Summit (Mantid 41 -> Darm 52)
}


def floor_levels() -> Dict[str, int]:
    """floor number (as str, for JSON) -> expected character level."""
    return {str(k): v for k, v in FLOOR_LEVELS.items()}


CLERIA_ORE = "Cleria Ore"

# Cleria Ore (= weapon-upgrade) count required to ENTER each zone, per the
# weapon_requirements option: (casual, strict). 5 ore exist (one per zone in
# vanilla), so a normal climb hands you up to 4 before the final zone. Gating on
# zone ENTRY (not just bosses) because regular enemies hit just as hard.
ZONE_ORE_REQ: Dict[str, Tuple[int, int]] = {
    "Flooded Prison": (0, 1),
    "Flames of Guilt": (1, 2),
    "Silent Sands": (1, 2),
    "Corrupted Blood": (2, 3),
    "Demonic Core": (2, 4),
}


def zone_ore_requirements(mode: int) -> Dict[str, int]:
    """zone -> required Cleria Ore count for weapon_requirements mode
    (0 off, 1 casual, 2 strict). Empty when off."""
    if mode not in (1, 2):
        return {}
    idx = 0 if mode == 1 else 1
    return {z: n for z, (c, s) in ZONE_ORE_REQ.items()
            for n in ((c, s)[idx],) if n > 0 and z in ALL_REGIONS}


def scene_levels() -> Dict[str, int]:
    """scene leaf number (as str, e.g. '6000') -> expected character level, via
    the scene's floor. Keyed by SCENE on purpose: the live current_floor
    (g_flags[0xCF]) is unreliable for warp destinations (reads the climbed-to
    floor, not the warped-to one), while current_scene (g_flags[0x1F9]) is exact."""
    out: Dict[str, int] = {}
    for _sc, _fl in SCENE_FLOOR.items():
        _m = re.match(r"\s*(\d+)\s*[Ff]", str(_fl))
        if not _m:
            continue
        _lvl = FLOOR_LEVELS.get(int(_m.group(1)))
        if _lvl:
            out[str(int(_sc[2:]))] = _lvl
    return out

# Per-character room-logic gate substitutions for items a character lacks. Toal
# gets Cleria Ring where Yunica/Hugo get Mask of Eyes (same chest = the
# hidden-door ability). Lacked items with no substitute are simply RELAXED (the
# edge becomes free for that character) — AP-safe (only ever more permissive).
_GATE_SUBST: Dict[str, Dict[str, str]] = {
    "toal": {"Mask of Eyes": "Cleria Ring"},
}
# substitution targets must also count as progression
GATE_ITEMS |= {v for m in _GATE_SUBST.values() for v in m.values()}


def character_req(req: list, char: str) -> list:
    """Transform a room-logic requirement for a character: substitute or drop
    items the character cannot receive; a term that fully drops becomes free."""
    subst = _GATE_SUBST.get(char, {})
    out: list = []
    for term in req:
        if isinstance(term, (list, tuple)):
            opts = [subst.get(x, x) for x in term]
            opts = [x for x in opts if item_allowed(x, char)]
            if opts:
                out.append(opts if len(opts) > 1 else opts[0])
        else:
            x = subst.get(term, term)
            if item_allowed(x, char):
                out.append(x)
    return out


def item_classification(name: str) -> str:
    if name == GOAL_ITEM or name in GATE_ITEMS:
        return "progression"
    if name in STATUE_UNLOCKS:
        return "useful"
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
item_index: Dict[str, int] = json.loads(_read_data("data/items.json"))


def vanilla_items(enabled: Set[str], char: str = "hugo") -> List[str]:
    """The real items to seed the pool (one per enabled chest/event location),
    using the selected character's variant at each location."""
    out: List[str] = []
    for l in _LOCS:
        if l["type"] not in enabled:
            continue
        it = location_vanilla_item(l["name"], char)
        if it:
            out.append(it)
    return out


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
