"""Access logic for the Ys Origin apworld.

Two layers, both applied here:

* **Coarse backstop** — boss-medallion gates on the zone->zone entrances (each
  zone needs the medallion from the zone below, where that medallion is in the
  pool). Guarantees a beatable seed even before room logic is authored.
* **Room logic** — per-edge item/skill requirements from ``room_logic.json``
  (e.g. the wind altar's far door needs the Ventus Bracelet). Authored
  zone-by-zone; un-authored scenes stay on the free, zone-gated default edge.

Completion = obtaining the Devil Medallion (the final boss reward).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .data_tables import (
    CLERIA_ORE,
    CONNECTIONS,
    GOAL_ITEM,
    active_gates,
    char_name,
    character_req,
    edge_requirements,
    interzone_climb_rules,
    open_scene_edge_requirements,
    req_satisfied,
    warp_edge_rules,
    zone_ore_requirements,
)

if TYPE_CHECKING:
    from . import YsOriginWorld


def set_rules(world: "YsOriginWorld") -> None:
    """Forward (linear) rules by default; the bidirectional warp-network rules
    when random spawn is on."""
    if getattr(world, "open_mode", False):
        _set_rules_open(world)
    else:
        _set_rules_forward(world)


def _set_rules_forward(world: "YsOriginWorld") -> None:
    mw = world.multiworld
    player = world.player

    # Coarse: boss-medallion gate on each zone entrance, plus (optional) a Cleria
    # Ore = weapon-level requirement so the warp network can't strand you on a
    # floor your weapon can't dent. The generator then guarantees enough ore is
    # obtainable before each zone is in logic.
    gates = active_gates()
    ore_req = zone_ore_requirements(int(world.options.weapon_requirements.value))
    for zone in set(gates) | set(ore_req):
        srcs = [s for s, d in CONNECTIONS if d == zone]
        if not srcs:
            continue
        entrance = mw.get_entrance(f"{srcs[0]} -> {zone}", player)
        medallion = gates.get(zone)
        ore = ore_req.get(zone, 0)
        entrance.access_rule = lambda state, i=medallion, n=ore: (
            (i is None or state.has(i, player))
            and (n == 0 or state.has(CLERIA_ORE, player, n))
        )

    # Fine: per-edge room-logic requirements (items/skills), transformed for the
    # selected character (substitute/relax items they can't receive — e.g. Toal
    # gets Cleria Ring for Mask of Eyes, and lacks Blue Necklace/Evil Ring so
    # those edges relax to free). These sit on scene->scene (or zone->scene)
    # entrances and never collide with the zone gates (zone->zone entrances).
    char = char_name(world.options)
    for (src, dst), req in edge_requirements().items():
        creq = character_req(req, char)
        if not creq:
            continue  # fully relaxed for this character -> free edge
        entrance = mw.get_entrance(f"{src} -> {dst}", player)
        entrance.access_rule = lambda state, r=creq: req_satisfied(r, state, player)


def _set_rules_open(world: "YsOriginWorld") -> None:
    """Open (random-spawn) rules: per-edge requirements on the bidirectional room
    graph, Cleria-Ore + medallion gates on the inter-zone climbs, and the warp
    hub (spawn statue free; other statues need their unlock item + the warped-to
    zone's Cleria Ore). The coarse zone backbone / active_gates is dropped — the
    medallions live on the boss-door + inter-zone climb edges instead."""
    mw = world.multiworld
    player = world.player
    char = char_name(world.options)
    weapon_on = int(world.options.weapon_requirements.value)
    locks = bool(world.options.statue_warp_locks.value)

    def gate(src, dst, item, ore_n, anchor=None):
        """Attach an (item AND ore-count AND reach-anchor) access rule to an edge."""
        if item is None and ore_n == 0 and anchor is None:
            return                          # free edge
        entrance = mw.get_entrance(f"{src} -> {dst}", player)
        entrance.access_rule = lambda state, i=item, n=ore_n, a=anchor: (
            (i is None or state.has(i, player))
            and (n == 0 or state.has(CLERIA_ORE, player, n))
            and (a is None or state.can_reach(a, "Region", player))
        )
        # An entrance rule that depends on region reachability MUST register the
        # anchor region as an indirect condition, or AP's sweep won't re-evaluate
        # this warp edge when the anchor floor later becomes reachable within the
        # same pass -> stale false-negatives -> nondeterministic fill failures.
        if anchor is not None:
            mw.register_indirect_condition(mw.get_region(anchor, player), entrance)

    # Per-scene room logic (bidirectional graph), character-transformed.
    for (src, dst), req in open_scene_edge_requirements().items():
        creq = character_req(req, char)
        if not creq:
            continue
        entrance = mw.get_entrance(f"{src} -> {dst}", player)
        entrance.access_rule = lambda state, r=creq: req_satisfied(r, state, player)

    # Inter-zone climbs: next zone's medallion + that zone's Cleria-Ore count.
    for (src, dst), (med, ore_n) in interzone_climb_rules(weapon_on).items():
        gate(src, dst, med, ore_n)

    # Warp hub: spawn statue free; others need their unlock item (when locks are
    # on), the warped-to zone's Cleria Ore (when weapon requirements are on), and a
    # reachable floor within max_warp_floors_skip of the destination (so a lone
    # unlock can't leapfrog you across the tower).
    max_skip = int(world.options.max_warp_floors_skip.value)
    for (src, dst), (unlock, ore_n, anchor) in warp_edge_rules(
            world.start_statue_scene, locks, weapon_on, max_skip).items():
        gate(src, dst, unlock, ore_n, anchor)


def set_completion_condition(world: "YsOriginWorld") -> None:
    player = world.player
    world.multiworld.completion_condition[player] = (
        lambda state: state.has(GOAL_ITEM, player)
    )
