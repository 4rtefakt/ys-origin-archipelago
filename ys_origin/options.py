"""YAML-configurable options for the Ys Origin apworld (Hugo slice).

Imports AP's options framework (only importable inside an Archipelago tree).
"""

from __future__ import annotations

from dataclasses import dataclass

from Options import Choice, PerGameCommonOptions


class Character(Choice):
    """Which playable character this run uses.

    Yunica, Hugo, and Toal climb the same tower with different skills/POV. The
    vertical slice targets **Hugo**; Yunica/Toal come once their routes are
    mapped.
    """
    display_name = "Character"
    option_yunica = 0
    option_hugo = 1
    option_toal = 2
    default = 1  # hugo


class Goal(Choice):
    """What finishing the seed requires."""
    display_name = "Goal"
    option_defeat_darm = 0
    option_defeat_all_bosses = 1
    default = 0


@dataclass
class YsOriginOptions(PerGameCommonOptions):
    character: Character
    goal: Goal
