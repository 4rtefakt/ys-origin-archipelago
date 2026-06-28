"""YAML-configurable options for the Ys Origin apworld (Hugo slice).

Imports AP's options framework (only importable inside an Archipelago tree).
"""

from __future__ import annotations

from dataclasses import dataclass

from Options import Choice, DefaultOnToggle, PerGameCommonOptions, Toggle


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


class StatueChecks(DefaultOnToggle):
    """Add a check for activating each goddess statue / save point (~22)."""
    display_name = "Statue checks"


class BossChecks(DefaultOnToggle):
    """Add a check for defeating each boss / mid-boss (~12)."""
    display_name = "Boss checks"


class FloorChecks(DefaultOnToggle):
    """Add a check for reaching each tower floor (~21)."""
    display_name = "Floor checks"


class RoomChecks(Toggle):
    """Add a check for entering each tower room (~145). Big, filler-heavy."""
    display_name = "Room checks (sanity)"


@dataclass
class YsOriginOptions(PerGameCommonOptions):
    character: Character
    goal: Goal
    statue_checks: StatueChecks
    boss_checks: BossChecks
    floor_checks: FloorChecks
    room_checks: RoomChecks
