"""Access logic for the Ys Origin apworld — Hugo slice.

The slice has no gating yet (all locations reachable from the single region), so
``set_rules`` is a no-op. Completion = obtaining the Cerulean Flabellum (the
slice's progression item). Real floor/spell/movement gating comes as the route
and per-character offsets are mapped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import YsOriginWorld

GOAL_ITEM = "Cerulean Flabellum"


def set_rules(world: "YsOriginWorld") -> None:
    # No entrance/location gating in the vertical slice.
    return


def set_completion_condition(world: "YsOriginWorld") -> None:
    player = world.player
    world.multiworld.completion_condition[player] = (
        lambda state: state.has(GOAL_ITEM, player)
    )
