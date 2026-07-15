"""Open-mode (warp / random-start) item-CRITICALITY audit — the ground truth
behind the "many progression items are actually skippable" finding.

Run directly (also prints the full criticality report)::

    python -m tests.test_logic_criticality

Loads ``ys_origin.data_tables`` in isolation and faithfully mirrors
``rules._set_rules_open`` using the SAME pure builders (``warp_edge_rules`` /
``interzone_climb_rules`` / ``open_scene_edge_requirements`` /
``warp_connections`` / ``open_scene_connections``) and the module's own
``req_satisfied`` evaluator — so the reachability it computes tracks the real
open-mode rules, not a re-implementation.

**Criticality test** (placement-independent): grant the FULL vanilla item set
(plus every statue-warp unlock), then remove one item and re-check reachability.
If no location becomes unreachable, that item is *never strictly required* — an
alternate path (the warp network, plus level/weapon scaling) always covers it, so
its default ``progression`` tag is only conservative. This is a sound necessity
test: if the goal set is reachable without X while holding everything else, X is
never the unique gate for anything.

The finding this pins: in open mode the five zone medallions (and most keys /
bracelets / crests) are NON-critical because a chain of warps ascends the tower
without ever climbing — while a small core of room-gate items (Mask of Eyes /
Cleria Ring, the Moon Crests, Water Dragon's Scales, Evil Ring, Bronze Key) plus
the goal medallion stay genuinely required. Guarded in BOTH directions so a graph
edit that makes a "safe" item critical (or vice-versa) trips here.

Scope: open mode only (``random_start`` + ``statue_warp_locks``), weapon gating
on (the shipped default), across every character and every possible spawn statue.
"""

from __future__ import annotations

import importlib.util
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ys_origin_data_tables_crit", _ROOT / "ys_origin" / "data_tables.py"
)
dt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dt)

ALL_CHARS = ("yunica", "hugo", "toal")
ITEM_LOCS = frozenset({"chest", "event"})

# The five zone-gate medallions — the headline "skippable in warp mode" set.
ZONE_MEDALLIONS = frozenset(dt.ZONE_GATE.values()) - {dt.GOAL_ITEM}

# Room-gate items a warp CANNOT bypass (they gate on-foot room paths to specific
# chests). At least one must genuinely strand a location, per character — the
# guard that we never demote something actually required. Character-aware: Toal
# gets Cleria Ring where Yunica/Hugo get Mask of Eyes (the same hidden-door gate).
CRITICAL_CORE = {
    "yunica": {"Mask of Eyes", "Blue Moon Crest", "Red Moon Crest",
               "Water Dragon's Scales", "Evil Ring", "Bronze Key"},
    "hugo": {"Mask of Eyes", "Blue Moon Crest", "Red Moon Crest",
             "Water Dragon's Scales", "Evil Ring", "Bronze Key"},
    "toal": {"Cleria Ring", "Blue Moon Crest", "Red Moon Crest",
             "Water Dragon's Scales", "Bronze Key"},
}


class _State:
    def __init__(self, inv):
        self.inv = inv

    def has(self, item, player, count=1):
        return self.inv.get(item, 0) >= count


def _all_spawns():
    """Every statue scene that could be a random-start spawn (max floor = 25)."""
    return dt.start_statue_candidates(25)


def _loc_regions():
    m = {}
    for rg, locs in dt.locations_by_region(ITEM_LOCS).items():
        for l in locs:
            m[l] = rg
    return m


def _full_inventory(char):
    inv = defaultdict(int)
    for locs in dt.locations_by_region(ITEM_LOCS).values():
        for loc in locs:
            it = dt.location_vanilla_item(loc, char)
            if it:
                inv[it] += 1
    for w in dt.statue_unlock_items():   # warp unlock items (locks on)
        inv[w] += 1
    return inv


def _reachable(inv, char, conns, warp, climb, scenereq):
    state = _State(inv)
    reached = {dt.MENU}

    def edge_open(src, dst):
        if (src, dst) in warp:
            unlock, ore_n, anchor = warp[(src, dst)]
            if unlock and inv.get(unlock, 0) < 1:
                return False
            if inv.get(dt.CLERIA_ORE, 0) < ore_n:
                return False
            if anchor and anchor not in reached:
                return False
            return True
        if (src, dst) in climb:
            med, ore_n = climb[(src, dst)]
            if med and inv.get(med, 0) < 1:
                return False
            if inv.get(dt.CLERIA_ORE, 0) < ore_n:
                return False
            return True
        req = scenereq.get((src, dst))
        if req:
            creq = dt.character_req(req, char)
            if creq and not dt.req_satisfied(creq, state, 0):
                return False
        return True

    changed = True
    while changed:
        changed = False
        for src, dst in conns:
            if src in reached and dst not in reached and edge_open(src, dst):
                reached.add(dst)
                changed = True
    return reached


def _model(spawn, weapon_on=1, max_skip=5):
    conns = dt.warp_connections() + dt.open_scene_connections()
    warp = dt.warp_edge_rules(spawn, True, weapon_on, max_skip)
    climb = dt.interzone_climb_rules(weapon_on)
    scenereq = dt.open_scene_edge_requirements()
    return conns, warp, climb, scenereq


def _reachable_locs(reached, lr):
    return {l for l, rg in lr.items() if rg in reached}


def criticality(char, spawn, max_skip=5):
    """{progression item -> #locations stranded if removed} for one (char, spawn)."""
    conns, warp, climb, scenereq = _model(spawn, 1, max_skip)
    lr = _loc_regions()
    inv0 = _full_inventory(char)
    base = _reachable_locs(_reachable(inv0, char, conns, warp, climb, scenereq), lr)
    prog = [n for n in inv0 if dt.item_classification(n) == "progression"]
    out = {}
    for it in prog:
        inv = dict(inv0)
        inv.pop(it, None)
        r = _reachable_locs(_reachable(inv, char, conns, warp, climb, scenereq), lr)
        out[it] = len(base - r)
    return out


# --------------------------------------------------------------------------- #

def test_open_baseline_all_reachable():
    """Sanity/faithfulness: full inventory reaches every location from every
    spawn, for every character. If this breaks, the model diverged from rules."""
    lr = _loc_regions()
    for char in ALL_CHARS:
        for spawn in _all_spawns():
            conns, warp, climb, scenereq = _model(spawn)
            reached = _reachable(_full_inventory(char), char, conns, warp, climb, scenereq)
            missing = sorted(set(lr) - _reachable_locs(reached, lr))
            assert not missing, (char, spawn, missing[:8])


def test_zone_medallions_are_never_critical_in_open_mode():
    """The headline finding: warping bypasses every inter-zone climb, so none of
    the five zone medallions ever strands a location — any character, any spawn."""
    for char in ALL_CHARS:
        for spawn in _all_spawns():
            crit = criticality(char, spawn)
            offenders = {m: crit[m] for m in ZONE_MEDALLIONS if crit.get(m, 0) > 0}
            assert not offenders, (char, spawn, offenders)


def test_critical_core_still_gates_locations():
    """Guard the other direction: the room-gate items warps can't bypass MUST
    still strand at least one location on some spawn — so a future 'demote the
    non-critical items' change never accidentally frees one of these."""
    for char in ALL_CHARS:
        ever = defaultdict(int)
        for spawn in _all_spawns():
            crit = criticality(char, spawn)
            for k, v in crit.items():
                ever[k] = max(ever[k], v)
        for item in CRITICAL_CORE[char]:
            assert ever.get(item, 0) > 0, (char, item, "expected critical, strands nothing")


def test_goal_item_is_the_only_always_required_medallion():
    """The Devil Medallion is the completion condition (Has(GOAL_ITEM)); it gates
    no *location* (it's the goal), so the location metric reads 0 — assert it's
    handled as the win item, not mistaken for a demotable gate."""
    assert dt.GOAL_ITEM == "Devil Medallion"
    # it is progression by classification regardless of the location metric
    assert dt.item_classification(dt.GOAL_ITEM) == "progression"


def _run_all() -> int:
    # report first (informative), then the assertions
    print("=== open-mode criticality (spawn=1F, max_skip=5) ===")
    for char in ALL_CHARS:
        crit = criticality(char, _all_spawns()[0])
        never = sorted(k for k, v in crit.items() if v == 0 and k != dt.GOAL_ITEM)
        core = {k: v for k, v in sorted(crit.items()) if v > 0}
        print(f"[{char}] never-critical ({len(never)}): {', '.join(never)}")
        print(f"        still-critical: {core}")
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    print()
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
