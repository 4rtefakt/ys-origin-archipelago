"""Access logic for the Ys Origin apworld.

Zone-to-zone gating by boss medallions (each zone requires the medallion from
the zone below, where that medallion exists in the pool). This is the logic the
scripts encode (medallion checks gate progression); physical/ability gating
(double-jump, dash, elemental traversal) is not in the scripts and is left for
hand-authoring as it is mapped.

Completion = obtaining the Devil Medallion (the final boss reward).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .data_tables import CONNECTIONS, GOAL_ITEM, active_gates

if TYPE_CHECKING:
    from . import YsOriginWorld


def set_rules(world: "YsOriginWorld") -> None:
    mw = world.multiworld
    player = world.player
    for zone, item in active_gates().items():
        srcs = [s for s, d in CONNECTIONS if d == zone]
        if not srcs:
            continue
        entrance = mw.get_entrance(f"{srcs[0]} -> {zone}", player)
        entrance.access_rule = lambda state, i=item: state.has(i, player)


def set_completion_condition(world: "YsOriginWorld") -> None:
    player = world.player
    world.multiworld.completion_condition[player] = (
        lambda state: state.has(GOAL_ITEM, player)
    )
