"""Offline reachability & integrity audit of the FORWARD (linear, non-random)
access logic — the completability guarantees for a normal seed, with no game and
no Archipelago tree.

Run directly::

    python -m tests.test_logic_reachability

Loads ``ys_origin.data_tables`` in isolation (the package ``__init__`` needs
Archipelago's ``BaseClasses``, absent offline) and reuses the module's OWN
requirement evaluator (``req_satisfied``) so the checks track the real rules
rather than a re-implementation. It rebuilds only the region-graph BFS + the
zone medallion/Cleria-Ore gates that ``rules.py`` attaches, from the same pure
data (``CONNECTIONS`` / ``active_gates`` / ``zone_ore_requirements`` /
``edge_requirements``).

Two complementary guarantees, for every playable character:

  * **All-items reachability** (placement-free) — grant the whole vanilla item
    set up front and expand: every region and the goal MUST be reachable, under
    both weapon-gating settings. This is exactly the invariant Archipelago itself
    requires (a seed where even *with every item* something is unreachable is
    broken) and it catches a disconnected graph, a missing connection, or a
    fail-closed typo in a gate/edge item name (an unknown name is never
    satisfiable, silently sealing an edge).

  * **Vanilla-placement completability** (weapon gating OFF) — place each
    location's vanilla item at that location and sweep from the start collecting
    reachable items: the goal MUST be obtainable and every location reachable.
    This proves an actual *ordering* exists for the authored medallion/key
    backbone. (It is intentionally NOT asserted with weapon gating ON: that mode
    puts Cleria Ore behind ore-gated zones on purpose and relies on AP's fill to
    relocate ore earlier — vanilla ore positions don't satisfy their own gates,
    so a vanilla-placement sweep deadlocks by design, not by bug.)

Plus cheap structural integrity guards (every requirement/gate names a real
item; every graph endpoint is a real region).

Scope: forward mode only (the default, non-``random_start`` seed). Open-mode
(random-spawn) reachability has its own graph and is a separate audit.
"""

from __future__ import annotations

import importlib.util
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ys_origin_data_tables_reach", _ROOT / "ys_origin" / "data_tables.py"
)
dt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dt)

ALL_CHARS = ("yunica", "hugo", "toal")
ITEM_LOCS = frozenset({"chest", "event"})  # the always-on, item-bearing categories


class _State:
    """Minimal stand-in for AP's CollectionState for ``dt.req_satisfied``: a
    ``has(item, player, count=1)`` backed by an inventory counter (single player,
    so the player arg is ignored)."""

    def __init__(self, inv: dict):
        self.inv = inv

    def has(self, item: str, player, count: int = 1) -> bool:
        return self.inv.get(item, 0) >= count


def _edge_open(src, dst, inv, gates, ore_req, edge_reqs, char, state) -> bool:
    """Faithful mirror of the forward rules in ``rules.py``: a zone entrance needs
    that zone's medallion + Cleria-Ore count; an authored scene edge needs its
    character-transformed room-logic requirement. Absent parts are free."""
    if dst in gates or dst in ore_req:
        need = gates.get(dst)
        if need and inv.get(need, 0) < 1:
            return False
        if inv.get(dt.CLERIA_ORE, 0) < ore_req.get(dst, 0):
            return False
    req = edge_reqs.get((src, dst))
    if req:
        creq = dt.character_req(req, char)
        if creq and not dt.req_satisfied(creq, state, 0):
            return False
    return True


def _expand(inv, char, weapon_on):
    """Region-set fixed point over CONNECTIONS given a fixed inventory. Returns
    the set of reachable regions."""
    gates = dt.active_gates()
    ore_req = dt.zone_ore_requirements(weapon_on)
    edge_reqs = dt.edge_requirements()
    state = _State(inv)
    reached = {dt.MENU}
    changed = True
    while changed:
        changed = False
        for src, dst in dt.CONNECTIONS:
            if src in reached and dst not in reached and _edge_open(
                    src, dst, inv, gates, ore_req, edge_reqs, char, state):
                reached.add(dst)
                changed = True
    return reached


def _region_locs():
    return dt.locations_by_region(ITEM_LOCS)


def _unreached_locations(reached) -> list:
    return sorted(l for rg, ls in _region_locs().items()
                  if rg not in reached for l in ls)


def _full_inventory(char) -> dict:
    """Every vanilla item across the item-bearing locations, granted at once
    (Cleria Ore accumulates to its full count)."""
    inv: dict = defaultdict(int)
    for locs in _region_locs().values():
        for loc in locs:
            it = dt.location_vanilla_item(loc, char)
            if it:
                inv[it] += 1
    return inv


def _vanilla_sweep(char, weapon_on):
    """Collect-as-you-go sweep with vanilla placement. Returns (goal_reached,
    reached_regions, unreached_locations)."""
    gates = dt.active_gates()
    ore_req = dt.zone_ore_requirements(weapon_on)
    edge_reqs = dt.edge_requirements()
    region_locs = _region_locs()
    inv: dict = defaultdict(int)
    state = _State(inv)
    reached = {dt.MENU}
    collected: set = set()
    changed = True
    while changed:
        changed = False
        for region in list(reached):
            for loc in region_locs.get(region, []):
                if loc in collected:
                    continue
                collected.add(loc)
                it = dt.location_vanilla_item(loc, char)
                if it:
                    inv[it] += 1
                    changed = True
        for src, dst in dt.CONNECTIONS:
            if src in reached and dst not in reached and _edge_open(
                    src, dst, inv, gates, ore_req, edge_reqs, char, state):
                reached.add(dst)
                changed = True
    unreached = sorted(l for rg, ls in region_locs.items()
                       if rg not in reached for l in ls)
    return inv.get(dt.GOAL_ITEM, 0) >= 1, reached, unreached


# --------------------------------------------------------------------------- #
# reachability guarantees
# --------------------------------------------------------------------------- #

def test_all_items_reach_everything():
    """With every item in hand, every location and the goal are reachable — for
    each character, under BOTH weapon-gating settings. The core AP invariant."""
    for char in ALL_CHARS:
        for weapon_on in (True, False):
            inv = _full_inventory(char)
            reached = _expand(inv, char, weapon_on)
            assert inv.get(dt.GOAL_ITEM, 0) >= 1, (char, "goal item not in pool")
            unreached = _unreached_locations(reached)
            assert not unreached, (char, f"weapon_on={weapon_on}", unreached[:10])


def test_vanilla_placement_completable_unweaponed():
    """Vanilla placement admits a real clear ordering (weapon gating off): the
    goal is collectable and nothing is stranded, for every character."""
    for char in ALL_CHARS:
        goal, _reached, unreached = _vanilla_sweep(char, weapon_on=False)
        assert goal, (char, "goal unreachable under vanilla placement")
        assert not unreached, (char, unreached[:10])


# --------------------------------------------------------------------------- #
# structural integrity guards (fail-closed typo / disconnection catchers)
# --------------------------------------------------------------------------- #

def _all_requirement_names():
    names = set()
    for reqmap in (dt.edge_requirements(), dt.open_scene_edge_requirements()):
        for req in reqmap.values():
            for term in req:
                if isinstance(term, (list, tuple)):
                    names.update(term)
                else:
                    names.add(term)
    return names


def test_requirements_name_real_items():
    """Every item named in a room-logic requirement (forward AND open graphs) is
    a real item. A typo here fails CLOSED — the edge silently becomes impassable
    and can strand the seed — so this guard matters more than it looks."""
    bad = sorted(n for n in _all_requirement_names() if n not in dt.item_name_to_id)
    assert not bad, f"requirement names that are not real items: {bad}"


def test_gates_and_goal_are_real_items():
    for zone, medallion in dt.ZONE_GATE.items():
        assert medallion in dt.item_name_to_id, (zone, medallion)
    assert dt.GOAL_ITEM in dt.item_name_to_id, dt.GOAL_ITEM
    # active_gates only surfaces gates whose medallion is a real, pooled item;
    # every configured zone gate should therefore survive into it.
    active = dt.active_gates()
    for zone, medallion in dt.ZONE_GATE.items():
        if zone in dt.ALL_REGIONS:
            assert active.get(zone) == medallion, (zone, medallion, active.get(zone))


def test_connection_endpoints_are_regions():
    regions = set(dt.ALL_REGIONS) | {dt.MENU}
    endpoints = {x for edge in dt.CONNECTIONS for x in edge}
    unknown = sorted(endpoints - regions)
    assert not unknown, f"CONNECTIONS endpoints that are not regions: {unknown}"


def test_gated_zones_have_single_incoming_edge():
    """rules.py attaches each zone gate to ``srcs[0]`` only. That's correct iff a
    gated zone has exactly one incoming edge; assert it so a future graph edit
    that adds a second entrance (which would leave an ungated backdoor) trips
    here instead of silently weakening the gate."""
    incoming = defaultdict(list)
    for src, dst in dt.CONNECTIONS:
        incoming[dst].append(src)
    multi = {z: v for z, v in incoming.items()
             if z in dt.active_gates() and len(v) > 1}
    assert not multi, f"gated zones with >1 incoming edge (ungated backdoor?): {multi}"


def test_wailing_blue_north_needs_the_barrier_item():
    """S_EVT1013 (the 4-statue barrier, 3F Transfer Room) gates the north half of
    Wailing Blue: Yunica/Hugo need the Blue Necklace, Toal needs Boost, learnt in
    S_1006 behind the Bronze Key door. With that item missing, nothing north of
    the barrier may be reachable — the Discord report was a Blue Necklace placed
    past the very room it opens (Aug 2026)."""
    north = {dt.scene_region(s) for s in ("S_1010", "S_1008", "S_1015")}
    for char, key in (("yunica", "Blue Necklace"), ("hugo", "Blue Necklace"),
                      ("toal", "Bronze Key")):
        for weapon_on in (False, True):
            inv = _full_inventory(char)
            inv.pop(key, None)
            reached = _expand(inv, char, weapon_on)
            leak = sorted(north & reached)
            assert not leak, (char, weapon_on, key, leak)


def test_dreaming_idol_checks_follow_the_story():
    """Dino's 1F gift (TALKC280) sets flag 296 — flag 285 is an unrelated chat —
    and only once the S_4017 Feena events set 293/294; it is Yunica's alone. The
    S_4017 idol (flag 270) is set by Yunica's and Hugo's events, never Toal's."""
    by_name = {l["name"]: l for l in dt._LOCS}
    dino = by_name["Wailing Blue: 1F Save — Dreaming Idol"]
    assert dino["detect"]["offset"] == "0x36BDBC"
    assert dt.location_for_char(dino, "yunica")
    assert not dt.location_for_char(dino, "hugo")
    assert not dt.location_for_char(dino, "toal")
    assert dt._region_of_location(dino) == dt.scene_region("S_4017")
    feena = by_name["Silent Sands: Rado Inside 4 (Feena) — Dreaming Idol"]
    assert [c for c in ("yunica", "hugo", "toal")
            if dt.location_for_char(feena, c)] == ["yunica", "hugo"]


def test_location_requires_name_real_things():
    """room_logic.json `location_requires`: every key is a real location, every
    scene a real region and every item a real item (a typo would silently drop
    the rule, or fail it closed)."""
    names = {l["name"] for l in dt._LOCS}
    regions = set(dt.ALL_REGIONS)
    assert dt.LOCATION_REQUIRES, "the Dreaming Idol chain must be encoded"
    for loc, reqs in dt.LOCATION_REQUIRES.items():
        assert loc in names, loc
        for r in reqs:
            if r.startswith("S_"):
                assert dt.scene_region(r) in regions, (loc, r)
            else:
                assert dt.split_term(r)[0] in dt.item_index, (loc, r)


def test_flames_noise_room_gate_per_character():
    """S_3009 drains HP until silenced: Hugo's Hammer, Yunica's Silver Harmonica
    plus the Flames Roo's song (its third Roda Fruit); Toal walks it on Boost."""
    req = dt.edge_requirements()[(dt.scene_region("S_3009"), dt.scene_region("S_3010"))]
    assert dt.character_req(req, "hugo") == ["Hammer"]
    assert dt.character_req(req, "yunica") == ["Silver Harmonica", "Roda Fruit#3"]
    assert dt.character_req(req, "toal") == []
    assert "Silver Harmonica" in dt.GATE_ITEMS and "Roda Fruit" in dt.GATE_ITEMS


def test_rado_annex_door_needs_ring_and_necklace():
    """S_4021/LOOK_DOOR: the Evil Ring held is lethal without the Blue Necklace,
    and the ring must bring its drained twin (0x5E) or the door never opens."""
    req = dt.edge_requirements()[(dt.scene_region("S_4021"), dt.scene_region("S_4017"))]
    assert dt.character_req(req, "yunica") == ["Evil Ring", "Blue Necklace"]
    assert dt.character_req(req, "toal") == ["Bronze Key"]
    assert dt.skill_grants()["Evil Ring"] == 0x5E


def test_element_gates():
    """Engine gates no script tests: a SOB flag word makes the crumbling walls
    thunder-only and the torches fire-only, and the S_2008 ward's Menoak seeds
    shrug off melee and wind (CleriaCore ROUTE_F13_TOAL D1/D4)."""
    edges = dt.edge_requirements()
    r = dt.scene_region
    thunder, fire = "Levinstrike Warhammer", "Crimson Lotusblade"
    assert edges[(r("S_2008"), r("S_2015"))] == [[thunder, fire]]
    assert edges[(r("S_2015"), r("S_2010"))] == [thunder]
    assert thunder in edges[(r("S_4006"), r("S_4009"))]
    assert fire in edges[(r("S_3004"), r("S_3005"))]
    assert dt.LOCATION_REQUIRES["Flooded Prison: 8F Waterway 1"] == [thunder]
    assert fire in dt.LOCATION_REQUIRES["Flames of Guilt: 11F Path 1 #2"]
    assert dt.LOCATION_REQUIRES["Flames of Guilt: Bridge Room 2"] == [fire]


def test_the_2f_gap_takes_wind_or_the_double_jump():
    """The 2F Path 2 gap: the wind skill or the double jump (Gold Bracelet)."""
    edges = dt.edge_requirements()
    r = dt.scene_region
    assert edges[(r("S_1004"), r("S_1005"))] == [["Cerulean Flabellum", "Gold Bracelet"]]


def test_22f_path_2_is_fire_for_toal():
    """The 22F Path 2 chest: double jump + wind for Yunica and Hugo, double jump
    + fire for Toal (the guide's three walkthroughs)."""
    loc = "Demonic Core: 22F Path 2"
    assert dt.LOCATION_REQUIRES[loc] == ["Gold Bracelet", "Cerulean Flabellum"]
    assert dt.LOCATION_REQUIRES_BY_CHARACTER == {"toal": {loc: ["Gold Bracelet", "Crimson Lotusblade"]}}


def test_undead_wards_need_the_chimes():
    """The revive family only stays dead under the Silver Chimes, and two wards
    are held by it: S_4009's Zarues and S_5080's four Zeruena, whose barrier
    stands before the Dragonbone Key chest and the EVT_5080 story trigger."""
    edges = dt.edge_requirements()
    r = dt.scene_region
    assert edges[(r("S_4009"), r("S_4010"))] == ["Silver Chimes"]
    assert dt.LOCATION_REQUIRES["Corrupted Blood: Boss Room"] == ["Silver Chimes"]
    idol = [k for k in dt.LOCATION_REQUIRES if k.startswith("Wailing Blue: 1F Save")]
    assert "Silver Chimes" in dt.LOCATION_REQUIRES[idol[0]]


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
