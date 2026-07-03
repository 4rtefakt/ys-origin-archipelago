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
WARP_HUB = "Warp Network"          # open-mode (random-spawn) reachability origin

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
# blessing left this set with the shop rando: purchases are live-detected (the
# bitfield poll), so blessings can hold real/progression items — buy a blessing,
# get a check. boss stays excluded (detection is arena ENTRY, not the kill);
# room stays excluded (scene-entry checks hold filler by design).
EXCLUDED_TYPES: Set[str] = {"boss", "room"}   # floor live 0x36BC58; blessing live 0x36BC80

# Filler for sanity locations. ONLY items that are safe to grant in unbounded
# quantity may go here:
#   * the "X Drop N" items (Strength/Defense/MP/Recovery Drop) are transient enemy
#     combat orbs, not held inventory — excluded (enemies still drop them).
#   * Roda Fruit and Cleria Ore are count-capped in vanilla; granting extra copies
#     bugs out / overpowers the player — excluded (they still appear at their
#     vanilla locations at the correct count).
#   * Celcetan Panacea is safe to give in any quantity.
#   * SP is the game's only currency (blessings + gear upgrades at statues) —
#     granted straight into the SP cell g_flags[0xD8] via SP_FILLER below.
#   * Gold ("50G".."1000G") was removed: the flags exist in the item table but
#     Ys Origin has no money, so they granted nothing.
FILLER_POOL: List[str] = [
    "Celcetan Panacea",
    "SP: 50", "SP: 150", "SP: 500",
]

# SP filler grants: item name -> amount added to the SP currency (g_flags[0xD8],
# the cell the blessing/upgrade GROWnn.XSO scripts deduct from). Published in
# slot_data so the mod/client can apply them (they're stat writes, not items).
SP_FILLER: Dict[str, int] = {"SP: 50": 50, "SP: 150": 150, "SP: 500": 500}
SP_FLAG_IDX = 0xD8


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


# -- progressive gear (optional, progressive_armor) -------------------------- #
# Ys Origin has two defensive slots, Armor and Boots, each a strict tier ladder
# per character (chests in tower order). With the option on, every gear chest
# seeds a "Progressive Armor" / "Progressive Boots" instead of the raw piece;
# receiving one grants your character's NEXT unowned tier, so pickups can't skip
# ahead (finding the 22F armor first still gives you tier 1). The 4th variant in
# each gear chest (Chain Mail / Wooden Shield / ...) belongs to the unlockable EX
# character and is never seeded for Yunica/Hugo/Toal.
PROGRESSIVE_ARMOR = "Progressive Armor"
PROGRESSIVE_BOOTS = "Progressive Boots"

GEAR_LADDERS: Dict[str, Dict[str, List[str]]] = {
    "yunica": {
        PROGRESSIVE_ARMOR: ["Ring Mail", "Half Plate", "Reflex", "Silver Dress"],
        PROGRESSIVE_BOOTS: ["Leather Boots", "Hard Leggings", "Leg Guards",
                            "Battle Guards", "Silver Leggings"],
    },
    "hugo": {
        PROGRESSIVE_ARMOR: ["Ebony Robe", "Chain Cloak", "Elder Robe",
                            "Cleria Garb"],
        PROGRESSIVE_BOOTS: ["Leather Greaves", "Ebony Shoes", "Shell Greaves",
                            "Moon Greaves", "Dark Falcon"],
    },
    "toal": {
        PROGRESSIVE_ARMOR: ["Black Chain", "Banded Mail", "Gothic Suit",
                            "Brave Armor"],
        PROGRESSIVE_BOOTS: ["Riveted Boots", "Black Leggings", "Banded Boots",
                            "Phantom Boots", "Brave Guards"],
    },
}


def progressive_gear_slot_data(char: str) -> Dict[str, List[int]]:
    """Progressive item name -> the character's tier ladder as g_flags indices
    (tier order). The mod/client grants the first index whose cell is unowned."""
    ladders = GEAR_LADDERS.get(char, GEAR_LADDERS["hugo"])
    return {nm: [item_index[i] for i in tiers if i in item_index]
            for nm, tiers in ladders.items()}


def _progressive_name_for(item: str, char: str) -> Optional[str]:
    """The progressive item replacing `item` in the pool (None if not gear)."""
    for nm, tiers in GEAR_LADDERS.get(char, {}).items():
        if item in tiers:
            return nm
    return None


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
    | set(FILLER_POOL) | {GOAL_ITEM} | set(STATUE_UNLOCKS) \
    | {PROGRESSIVE_ARMOR, PROGRESSIVE_BOOTS}
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


def start_statue_candidates(max_floor: int) -> List[int]:
    """Statue scene leaves eligible as a Random-start spawn under ``max_floor``:
    only statues on a tower floor <= max_floor. Never empty — if the cap excludes
    every statue (or a floor can't be resolved), the lowest-floor statue is used so
    a spawn is always available."""
    scenes = statue_scenes()
    eligible = [s for s in scenes if (scene_floor(s) or 1) <= max_floor]
    if eligible:
        return eligible
    lowest = min(scenes, key=lambda s: (scene_floor(s) or 1, s))
    return [lowest]


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
    1: 1, 2: 2, 3: 3, 4: 4, 5: 6,           # Wailing Blue (gentle opening; 5F Beast 6)
    6: 9, 7: 12, 8: 15, 9: 17,              # Flooded Prison (9F Arthropod 17)
    10: 18, 11: 19, 12: 20, 13: 21,         # Flames of Guilt (13F Monk 20, win 23)
    14: 22, 15: 23, 16: 24, 17: 25,         # Silent Sands (17F Construct ~23-25)
    18: 27, 19: 32, 20: 36, 21: 41,         # Corrupted Blood (21F Mantid = guide "Lv41")
    22: 44, 23: 47, 24: 49, 25: 51,         # Demonic Core / Summit (25F Dalles 51)
}


# Tower floor for each goddess-statue scene whose location data carries NO floor
# label (save-point rooms tagged only by zone). Random spawn can drop you at any
# of these, so every one needs a floor -> the catch-up level-floor has a target.
# EXACT floors, extracted from the game's own scene->floor logic (the map-index
# -> floor-id switch @0x574970 in yso_win.exe v1.1.1.0; each statue's map matched
# by its warp path and live-verified). Replaces the old zone-range guesses, which
# were off by up to 3 floors (e.g. S_2012 is the 9F Arthropod-Chamber save, not 7F).
STATUE_FLOOR: Dict[int, int] = {
    2013: 7, 2100: 8, 2012: 9,                 # Flooded Prison (S_2012 = 9F Arthropod Chamber)
    3000: 10, 3006: 11, 3015: 12, 3014: 13,    # Flames of Guilt
    4000: 14, 4020: 17, 4104: 18,              # Silent Sands (S_4104 Rado's Annex = 18F in-game)
    5000: 18, 5010: 20, 5014: 21,              # Corrupted Blood
}


# Top (boss-approach) floor of each zone. Rooms with no floor label — boss rooms,
# corridors, vaults, save points near the zone end — fall back to this so the
# catch-up level-floor always has a target (a random spawn can roam the whole
# zone, and these unlabeled rooms cluster at the tough top end). The level-floor
# only ever RAISES, so an on-level normal climber is barely affected.
ZONE_TOP_FLOOR: Dict[str, int] = {
    "Wailing Blue": 5, "Flooded Prison": 9, "Flames of Guilt": 13,
    "Silent Sands": 17, "Corrupted Blood": 21, "Demonic Core": 25,
}


def scene_floor(scene_leaf: int) -> Optional[int]:
    """Tower floor number for a scene leaf, from the parsed floor label, the
    STATUE_FLOOR fallback, or the zone-top fallback; None if zone unknown."""
    sc = f"S_{scene_leaf}"
    fl = SCENE_FLOOR.get(sc)
    m = re.match(r"\s*(\d+)\s*[Ff]", str(fl)) if fl else None
    if m:
        return int(m.group(1))
    if scene_leaf in STATUE_FLOOR:
        return STATUE_FLOOR[scene_leaf]
    return ZONE_TOP_FLOOR.get(SCENE_ZONE.get(sc, ""))


def floor_weapon_value(floor: int) -> int:
    """The vanilla weapon record value (g_flags[0x94]) appropriate for a floor —
    used as the spawn loadout when random spawn drops you ahead. Vanilla weapon by
    floor (guide): Lv2~5F, Lv3~12F, Lv4~16F, Lv5~20F, Lv6~23F; the record value
    maps to displayed Lv = value/2 + 2 (0=starter Lv1)."""
    if floor <= 5:
        return 0          # starter (Lv1-2)
    if floor <= 9:
        return 1          # Lv2
    if floor <= 13:
        return 2          # Lv3
    if floor <= 17:
        return 4          # Lv4
    if floor <= 21:
        return 6          # Lv5
    return 8              # Lv6


def floor_levels() -> Dict[str, int]:
    """floor number (as str, for JSON) -> expected character level."""
    return {str(k): v for k, v in FLOOR_LEVELS.items()}


# Displayed weapon level (1-6) -> g_flags[0x94] record value. Mirrors the mod's
# kWeaponTier ladder (ore N -> value {1,2,4,6,8} = Lv2..Lv6); Lv1 = starter = 0.
_WEAPON_LEVEL_VALUE: Dict[int, int] = {1: 0, 2: 1, 3: 2, 4: 4, 5: 6, 6: 8}


def weapon_value_for_level(level: int) -> int:
    """g_flags[0x94] weapon record value for a displayed weapon level (1-6),
    clamped into range. Used to publish the starting-weapon floor in slot data."""
    lvl = max(1, min(6, int(level)))
    return _WEAPON_LEVEL_VALUE[lvl]


CLERIA_ORE = "Cleria Ore"

# Cleria Ore (= weapon-upgrade) count required to ENTER each zone, per the
# weapon_requirements option: (casual, strict). 5 ore exist (one per zone in
# vanilla), so a normal climb hands you up to 4 before the final zone. Gating on
# zone ENTRY (not just bosses) because regular enemies hit just as hard.
# Cleria Ore required to ENTER each zone = the vanilla weapon level for the zone's
# entry floor (weapon Lv = ore + 1: vanilla Lv2~5F, Lv3~12F, Lv4~16F, Lv5~20F,
# Lv6~23-24F). Guarantees the floor-appropriate weapon is obtainable first, so a
# warp ahead can't drop you somewhere your weapon deals 1 damage.
ZONE_ORE_REQ: Dict[str, int] = {
    "Flooded Prison": 1,    # 6F  -> vanilla Lv2 (1 ore by 5F)
    "Flames of Guilt": 1,   # 10F -> vanilla Lv2 (the Lv3 ore is at 12F, inside)
    "Silent Sands": 2,      # 14F -> vanilla Lv3 (12F ore)
    "Corrupted Blood": 3,   # 18F -> vanilla Lv4 (16F ore)
    "Demonic Core": 4,      # 22F -> vanilla Lv5 (20F ore)
}


def zone_ore_requirements(enabled) -> Dict[str, int]:
    """zone -> required Cleria Ore count when weapon_requirements is on; {} off."""
    if not enabled:
        return {}
    return {z: n for z, n in ZONE_ORE_REQ.items() if n > 0 and z in ALL_REGIONS}


def scene_levels() -> Dict[str, int]:
    """scene leaf number (as str, e.g. '6000') -> expected character level, via
    the scene's floor. Keyed by SCENE on purpose: the live current_floor
    (g_flags[0xCF]) is unreliable for warp destinations (reads the climbed-to
    floor, not the warped-to one), while current_scene (g_flags[0x1F9]) is exact."""
    out: Dict[str, int] = {}
    for _sc in SCENE_ROOM:
        _leaf = int(_sc[2:])
        _floor = scene_floor(_leaf)          # label / STATUE_FLOOR / zone-top
        _lvl = FLOOR_LEVELS.get(_floor) if _floor else None
        if _lvl:
            out[str(_leaf)] = _lvl
    return out


def scene_floors() -> Dict[str, int]:
    """scene leaf number (as str) -> tower floor number. Same rationale as
    scene_levels: the mod derives "distinct floors visited" (blessing-shop
    one-per-floor pacing) from the reliable current_scene, not the warp-unreliable
    current_floor cell."""
    out: Dict[str, int] = {}
    for _sc in SCENE_ROOM:
        _leaf = int(_sc[2:])
        _floor = scene_floor(_leaf)
        if _floor:
            out[str(_leaf)] = _floor
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
    if name in STATUE_UNLOCKS or name in (PROGRESSIVE_ARMOR, PROGRESSIVE_BOOTS):
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


# -- overlay tracker maps (published in slot_data) --------------------------- #

def scene_locations_map(active: Set[str], name_to_id: Dict[str, int]
                        ) -> Dict[str, List[int]]:
    """scene leaf number (str) -> AP location ids of the ACTIVE locations tied to
    that scene (chests, events, statues, rooms, bosses). Drives the overlay's
    per-room "checks here" tracker; floor/blessing checks have no scene."""
    out: Dict[str, List[int]] = defaultdict(list)
    for l in _LOCS:
        if l["name"] not in active:
            continue
        sc = _scene_of(l)
        if sc:
            out[str(int(sc[2:]))].append(name_to_id[l["name"]])
    return dict(out)


def floor_locations_map(active: Set[str], name_to_id: Dict[str, int]
                        ) -> Dict[str, List[int]]:
    """tower floor (str) -> AP location ids of the ACTIVE locations on it (via
    each location's scene; "Reach NF" belongs to floor N). Drives the overlay's
    per-floor remaining-checks list shown at statues (the warp menu's floors)."""
    out: Dict[str, List[int]] = defaultdict(list)
    for l in _LOCS:
        if l["name"] not in active:
            continue
        fl: Optional[int] = None
        if l["type"] == "floor":
            fl = l.get("detect", {}).get("floor")
        else:
            sc = _scene_of(l)
            if sc:
                fl = scene_floor(int(sc[2:]))
        if fl:
            out[str(int(fl))].append(name_to_id[l["name"]])
    return dict(out)


def blessing_location_names(active: Set[str], name_to_id: Dict[str, int]
                            ) -> Dict[str, str]:
    """AP location id (str) -> short blessing name ("Increase SP gain") for the
    active blessing locations. Drives the shop-hints overlay panel (what item a
    blessing purchase actually gives, per the scout data)."""
    out: Dict[str, str] = {}
    for l in _LOCS:
        if l["type"] != "blessing" or l["name"] not in active:
            continue
        short = l["name"].split(": ", 1)[-1]
        out[str(name_to_id[l["name"]])] = short
    return out


def blessing_bit_location_ids(active: Set[str], name_to_id: Dict[str, int]
                              ) -> List[int]:
    """AP location ids of the ACTIVE bit-method blessing locations (the 23 the
    overlay shop can sell), sorted by location name for deterministic cost
    rolls. The armor blessing (flag-method; a different grant mechanism) is
    excluded — it stays vanilla-menu-only."""
    return [name_to_id[l["name"]] for l in sorted(_LOCS, key=lambda x: x["name"])
            if l["type"] == "blessing" and l["name"] in active
            and l.get("detect", {}).get("method") == "bit"]


# Depth (tower floor) at which each progression item first matters, derived from
# the gated edges: item -> lowest floor of an edge it unlocks. Gives a natural
# "how late is this item" score so blessing costs can track power (early skills
# cheap, late medallions dear). A few progression items gate zone entries / are
# collectibles with no room edge -> supplemented by hand.
ITEM_GATE_FLOOR: Dict[str, int] = {}
for (_src, _dst), _terms in EDGE_REQS.items():
    _m = re.search(r"S_(\d+)", _src)
    _fl = scene_floor(int(_m.group(1))) if _m else None
    if _fl is None:
        continue
    for _t in _terms:
        for _it in (_t if isinstance(_t, list) else [_t]):
            ITEM_GATE_FLOOR[_it] = min(ITEM_GATE_FLOOR.get(_it, 99), _fl)
# Progression items with no room-edge gate (zone-entry medallions / collectibles).
ITEM_GATE_FLOOR.update({
    "Arthropod Medallion": 9, "Cerulean Flabellum": 2, "Ignis Bracelet": 10,
    "Terra Bracelet": 14, "Black Pearl": 22, "Dreaming Idol": 24,
})


def weighted_blessing_cost(item_name: str, local: bool, advancement: bool,
                           rng, cmin: int, cmax: int) -> int:
    """SP price for one overlay-shop slot, weighted by the tower depth of the item
    placed there so filler/early upgrades are cheap and late medallions/skills are
    dear. Depth 0..1 -> price across [cmin, cmax] with a small deterministic jitter
    (rounded to 10s). Foreign (multiworld) items: we only know importance, so
    progression rides the high band, everything else the low-mid band."""
    if local:
        floor = ITEM_GATE_FLOOR.get(item_name)
        cls = _item_class.get(item_name, "useful")
        if floor is not None:
            d = (floor - 2) / 23.0            # 2F -> 0.0 .. 25F -> 1.0
        elif cls == "progression":
            d = 0.70
        elif cls == "filler":
            d = 0.12
        else:                                  # useful
            d = 0.38
    else:
        d = 0.70 if advancement else 0.38
    d = min(1.0, max(0.0, d))
    span = cmax - cmin
    cost = cmin + d * span + rng.uniform(-0.10, 0.10) * span
    cost = min(cmax, max(cmin, cost))
    return int(round(cost / 10.0)) * 10


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


def start_item_indices(names) -> List[int]:
    """g_flags indices for the named starting items (resolved via item_index).
    Unknown names are skipped (so a typo can't break generation); order preserved,
    deduped. Published in slot data for the mod/client to grant at New Game."""
    out: List[int] = []
    seen: Set[int] = set()
    for nm in names:
        idx = item_index.get(str(nm).strip())
        if idx is None or int(idx) in seen:
            continue
        seen.add(int(idx))
        out.append(int(idx))
    return out


def vanilla_items(enabled: Set[str], char: str = "hugo",
                  progressive_gear: bool = False) -> List[str]:
    """The real items to seed the pool (one per enabled chest/event location),
    using the selected character's variant at each location. With
    ``progressive_gear`` on, armor/boots pieces seed Progressive Armor/Boots
    instead (receiving one grants the character's next unowned tier)."""
    out: List[str] = []
    for l in _LOCS:
        if l["type"] not in enabled:
            continue
        it = location_vanilla_item(l["name"], char)
        if not it:
            continue
        if progressive_gear:
            prog = _progressive_name_for(it, char)
            if prog:
                it = prog
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


# --------------------------------------------------------------------------- #
# OPEN (random-spawn) graph — Phase B.
#
# When `random_start` is on, the linear Menu->zone backbone is dropped and
# reachability instead spreads from the SPAWN statue through a BIDIRECTIONAL
# room graph plus a warp hub. The forward graph above is left completely
# untouched (zero regression for normal seeds).
#
# Each authored `from`-edge may carry an optional 3rd element describing its
# REVERSE semantics (the forward builder reads only [0]/[1], so this is a no-op
# for normal seeds):
#   (bare) / "sym" : symmetric  — both directions need the reqs (e.g. a wind gap)
#   "up"           : forward needs reqs, REVERSE FREE (doors, climbs, boss doors)
#   "down"         : reverse needs reqs, FORWARD FREE (rare; a gated descent)
#   "oneway"       : forward only, NO reverse (a drop you can't climb back)
#
# On top of the per-scene edges we re-create the inter-zone links the dropped
# backbone used to provide (exit room of zone z -> entry scene of z+1), gated on
# the next zone's medallion and "up" (free to descend between zones), so any
# spawn can fall back down to 1F and climb the tower normally.
# --------------------------------------------------------------------------- #

# zone -> the scene whose authored source is the zone name (its entry room).
_zone_entry_scene: Dict[str, str] = {}
for _sc, _spec in _room_logic.items():
    for _e in _spec.get("from", []):
        if not re.match(r"S_\d+$", _e[0]):
            _zone_entry_scene[_e[0]] = _sc


def _edge_mode(edge: list) -> str:
    return edge[2] if len(edge) > 2 else "sym"


def _build_open_scene_graph() -> Tuple[List[Tuple[str, str]], Dict[Tuple[str, str], list]]:
    """Bidirectional scene graph + inter-zone climb links. On a duplicate
    (src,dst) the LEAST restrictive requirement wins (a free edge beats a gated
    one) so a reverse-free edge is never accidentally re-gated."""
    edges: Dict[Tuple[str, str], list] = {}   # (src,dst) -> req ([] = free)

    def add(a: str, b: str, req: list) -> None:
        if a == b:
            return
        if (a, b) in edges:
            if not req or not edges[(a, b)]:
                edges[(a, b)] = []            # free wins
            elif len(req) < len(edges[(a, b)]):
                edges[(a, b)] = list(req)
        else:
            edges[(a, b)] = list(req)

    for sc in sorted(SCENE_ROOM):
        spec = _room_logic.get(sc)
        if not (spec and spec.get("from")):
            continue
        dst = scene_region(sc)
        for edge in spec["from"]:
            src = edge[0]
            if re.match(r"S_\d+$", src) is None:
                continue                       # zone-name source -> via hub/inter-zone
            req = list(edge[1]) if len(edge) > 1 else []
            mode = _edge_mode(edge)
            srcr = scene_region(src)
            if mode == "oneway":
                add(srcr, dst, req)
            elif mode == "up":
                add(srcr, dst, req); add(dst, srcr, [])
            elif mode == "down":
                add(dst, srcr, req); add(srcr, dst, [])
            else:                              # "sym" / bare
                add(srcr, dst, req); add(dst, srcr, list(req))

    # un-authored scenes (no authored 'from' -> in forward they hang free off
    # the zone region): connect them free to/from the zone's entry statue so they
    # stay reachable in open mode (matters only when room_checks is on).
    for sc in sorted(SCENE_ROOM):
        spec = _room_logic.get(sc)
        if spec and spec.get("from"):
            continue
        entry = _zone_entry_scene.get(SCENE_ZONE.get(sc, ""))
        if entry and entry != sc:
            add(scene_region(entry), scene_region(sc), [])
            add(scene_region(sc), scene_region(entry), [])

    # inter-zone climb links (replace the dropped backbone): exit(z) -> entry(z+1)
    # gated on z+1's medallion, "up" (free descent back down the tower).
    for a, b in zip(_present, _present[1:]):
        ex, en = _zone_exits.get(a), _zone_entry_scene.get(b)
        if not (ex and en):
            continue
        med = ZONE_GATE.get(b)
        req = [med] if med and med in item_name_to_id else []
        add(scene_region(ex), scene_region(en), req)
        add(scene_region(en), scene_region(ex), [])

    conns = list(edges.keys())
    reqs = {k: v for k, v in edges.items() if v}
    return conns, reqs


OPEN_SCENE_CONNECTIONS, OPEN_SCENE_EDGE_REQS = _build_open_scene_graph()

# forward (src,dst) pairs of the inter-zone climb edges — handled with an added
# Cleria-Ore requirement in rules.py, so they're excluded from the generic
# scene-requirement loop there.
OPEN_INTERZONE_EDGES: Set[Tuple[str, str]] = {
    (scene_region(_zone_exits[a]), scene_region(_zone_entry_scene[b]))
    for a, b in zip(_present, _present[1:])
    if _zone_exits.get(a) and _zone_entry_scene.get(b)
}


def open_regions() -> List[str]:
    """Region list for open mode: Menu + the warp hub + every scene (NO coarse
    zone regions — nothing routes through them once the backbone is dropped)."""
    return [MENU, WARP_HUB] + [scene_region(s) for s in sorted(SCENE_ROOM)]


def open_scene_connections() -> List[Tuple[str, str]]:
    return list(OPEN_SCENE_CONNECTIONS)


def open_scene_edge_requirements() -> Dict[Tuple[str, str], list]:
    """Per-scene edge reqs EXCLUDING the inter-zone climb edges (those get an
    extra ore requirement applied in rules.py)."""
    return {k: v for k, v in OPEN_SCENE_EDGE_REQS.items()
            if k not in OPEN_INTERZONE_EDGES}


def _statue_targets() -> List[Tuple[str, dict]]:
    """(unlock-item name, info) for every statue whose scene exists, deduped by
    scene (first wins)."""
    seen: Set[int] = set()
    out: List[Tuple[str, dict]] = []
    for nm, info in STATUE_UNLOCKS.items():
        sc = f"S_{info['scene']}"
        if sc in SCENE_ROOM and info["scene"] not in seen:
            seen.add(info["scene"])
            out.append((nm, info))
    return out


def warp_connections() -> List[Tuple[str, str]]:
    """Static hub edges: Menu -> hub, hub -> each statue scene. The per-statue
    gating (spawn-free / unlock item / zone ore) is applied in rules.py."""
    conns = [(MENU, WARP_HUB)]
    for _nm, info in _statue_targets():
        conns.append((WARP_HUB, scene_region(f"S_{info['scene']}")))
    return conns


def _floor_anchor_regions() -> Dict[int, str]:
    """tower floor -> a representative scene region present on it (lowest leaf).
    Used by the warp-skip limit: reaching this region == "you can reach floor N"."""
    out: Dict[int, str] = {}
    for sc in sorted(SCENE_ROOM, key=lambda s: int(s[2:])):
        fl = scene_floor(int(sc[2:]))
        if fl and fl not in out:
            out[fl] = scene_region(sc)
    return out


_FLOOR_ANCHOR = _floor_anchor_regions()


def warp_skip_anchor(target_floor: Optional[int], max_skip: int) -> Optional[str]:
    """Region a player must already reach before a warp to ``target_floor`` is in
    logic, under the ``max_skip`` floors-ahead limit. Returns the anchor region for
    the lowest floor in ``[target_floor - max_skip, target_floor)`` that exists, or
    None when the limit doesn't bind (``max_skip <= 0``, unknown floor, or the
    window reaches the base of the tower so any spawn already qualifies)."""
    if max_skip <= 0 or not target_floor:
        return None
    low = target_floor - max_skip
    if low <= 1:
        return None                                  # within reach of a base spawn
    for f in range(low, target_floor):               # lowest existing floor in window
        if f in _FLOOR_ANCHOR:
            return _FLOOR_ANCHOR[f]
    return None                                      # no scene in window -> don't block


def warp_edge_rules(spawn_scene: int, locks: bool, weapon_on, max_skip: int = 0
                    ) -> Dict[Tuple[str, str], Tuple[Optional[str], int, Optional[str]]]:
    """(hub,statue) -> (unlock-item or None, ore-count, skip-anchor region or None).
    The spawn statue is free; other statues need their unlock item (when warp locks
    are on), the warped-to zone's Cleria-Ore count (when weapon requirements are
    on), and — under ``max_skip`` > 0 — a reachable floor within ``max_skip`` of the
    destination so a lone unlock can't teleport you across the tower."""
    ore = zone_ore_requirements(weapon_on)
    out: Dict[Tuple[str, str], Tuple[Optional[str], int, Optional[str]]] = {}
    for nm, info in _statue_targets():
        sc = f"S_{info['scene']}"
        dst = scene_region(sc)
        if info["scene"] == spawn_scene:
            out[(WARP_HUB, dst)] = (None, 0, None)      # spawn: always free
        else:
            unlock = nm if locks else None
            anchor = warp_skip_anchor(scene_floor(info["scene"]), max_skip)
            out[(WARP_HUB, dst)] = (unlock, ore.get(SCENE_ZONE.get(sc, ""), 0), anchor)
    return out


def interzone_climb_rules(weapon_on) -> Dict[Tuple[str, str], Tuple[Optional[str], int]]:
    """(exit_z, entry_z+1) -> (medallion or None, ore-count) for the open-mode
    inter-zone climb edges."""
    ore = zone_ore_requirements(weapon_on)
    out: Dict[Tuple[str, str], Tuple[Optional[str], int]] = {}
    for a, b in zip(_present, _present[1:]):
        ex, en = _zone_exits.get(a), _zone_entry_scene.get(b)
        if not (ex and en):
            continue
        med = ZONE_GATE.get(b)
        med = med if med and med in item_name_to_id else None
        out[(scene_region(ex), scene_region(en))] = (med, ore.get(b, 0))
    return out


def statue_scene_for(scene_leaf: int) -> bool:
    """True if a statue exists at this scene leaf number (a valid spawn)."""
    return any(info["scene"] == scene_leaf for _n, info in _statue_targets())
