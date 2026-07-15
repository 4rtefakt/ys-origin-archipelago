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
