"""YAML-configurable options for the Ys Origin apworld (Hugo slice).

Imports AP's options framework (only importable inside an Archipelago tree).
"""

from __future__ import annotations

from dataclasses import dataclass

from Options import Choice, DeathLink, DefaultOnToggle, PerGameCommonOptions, Toggle


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


class BlessingChecks(DefaultOnToggle):
    """Add a check for buying each Divine Blessing (~24, filler-only for now)."""
    display_name = "Blessing checks"


class BossChecks(DefaultOnToggle):
    """Add a check for defeating each boss / mid-boss (~12)."""
    display_name = "Boss checks"


class FloorChecks(DefaultOnToggle):
    """Add a check for reaching each tower floor (~21)."""
    display_name = "Floor checks"


class RoomChecks(Toggle):
    """Add a check for entering each tower room (~145). Big, filler-heavy."""
    display_name = "Room checks (sanity)"


class StatueWarpLocks(Toggle):
    """Lock the goddess statues. Each statue starts wrapped in darkness and stays
    fully inactive — no warp, healing, saving, or blessings — until you receive
    its unlock item (one per statue, shuffled into the multiworld). One statue is
    unlocked from the start so you can always save (see Random start). Unlock
    items are bonus convenience and don't affect logic (everything stays
    reachable on foot)."""
    display_name = "Statue warp locks"


class RandomStart(Toggle):
    """With Statue warp locks on, pick a random goddess statue as the one that
    starts unlocked, instead of the 1F starting statue. (Physically spawning at
    that statue comes later; for now it only chooses which statue begins
    unlocked.)"""
    display_name = "Random start statue"


@dataclass
class YsOriginOptions(PerGameCommonOptions):
    character: Character
    goal: Goal
    statue_checks: StatueChecks
    blessing_checks: BlessingChecks
    boss_checks: BossChecks
    floor_checks: FloorChecks
    room_checks: RoomChecks
    statue_warp_locks: StatueWarpLocks
    random_start: RandomStart
    death_link: DeathLink
