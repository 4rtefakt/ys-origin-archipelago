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
    CONNECTIONS,
    GOAL_ITEM,
    active_gates,
    char_name,
    character_req,
    edge_requirements,
    req_satisfied,
)

if TYPE_CHECKING:
    from . import YsOriginWorld


def set_rules(world: "YsOriginWorld") -> None:
    mw = world.multiworld
    player = world.player

    # Coarse: boss-medallion gate on each zone entrance.
    for zone, item in active_gates().items():
        srcs = [s for s, d in CONNECTIONS if d == zone]
        if not srcs:
            continue
        entrance = mw.get_entrance(f"{srcs[0]} -> {zone}", player)
        entrance.access_rule = lambda state, i=item: state.has(i, player)

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


def set_completion_condition(world: "YsOriginWorld") -> None:
    player = world.player
    world.multiworld.completion_condition[player] = (
        lambda state: state.has(GOAL_ITEM, player)
    )
