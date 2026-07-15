"""YAML-configurable options for the Ys Origin apworld (Hugo slice).

Imports AP's options framework (only importable inside an Archipelago tree).
"""

from __future__ import annotations

from dataclasses import dataclass

from Options import (
    Choice, DeathLink, DefaultOnToggle, OptionList, PerGameCommonOptions, Range,
    Toggle,
)


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
    """Start a New Game at a RANDOM goddess statue anywhere in the tower instead
    of 1F. Requires Statue warp locks. The mod skips the entire intro (movies +
    cutscenes, all characters) and warps you straight to the spawn statue with a
    floor-appropriate level + weapon loadout so you're playable wherever you land;
    reachability uses a bidirectional warp-network logic so the seed stays
    beatable from any spawn. (Normal, non-random seeds are unchanged.)"""
    display_name = "Random start statue"


class MaxStartingFloor(Range):
    """Highest tower floor a Random start may spawn you on. Random start picks a
    goddess statue uniformly at random, and the deep statues (a 25F spawn) drop you
    into brutal rooms with no gear history behind you. This caps the draw to statues
    on floor <= this value, so New Game always begins somewhere survivable while
    still landing you *somewhere* rather than 1F. Only affects ``random_start``;
    lower = gentler openings, raise it (up to 25 = the summit) for more variety.
    Ignored when ``random_start`` is off. If no statue exists at or below the cap,
    the lowest-floor statue is used. Default 5 keeps openings in the early tower
    (1F/4F/5F statues) so a fresh, low-level, starter-weapon run stays fair."""
    display_name = "Random start max floor"
    range_start = 1
    range_end = 25
    default = 5


class MaxWarpFloorsSkip(Range):
    """How many tower floors ahead of your current reach a warp may jump, in the
    random-spawn warp network. With 0 the warp network is unrestricted (a single
    received unlock can send you 15 floors up into rooms you have no business in
    yet). With N > 0 the generator only puts a statue's warp in logic once you can
    already reach a floor within N of it — so progress climbs in steps of at most N
    floors instead of one lucky unlock teleporting you across the tower. Everything
    still stays reachable on foot; this only paces the warp shortcuts. Only affects
    ``random_start``; 0 = unlimited (old behaviour)."""
    display_name = "Max warp floors skipped"
    range_start = 0
    range_end = 25
    default = 5


class StartingItems(OptionList):
    """Items to grant at the start of every New Game, as a floor (they're simply
    marked owned — good for key items). Defaults to the two warp Crystals the
    opening cutscene normally hands you (Crystal, and Toal's Dark Crystal), so a
    Random start — which skips that intro — still begins with them. Add any item
    names here (e.g. ``Mask of Eyes``) to start with them; unknown names are
    ignored. Names must match the game's item names exactly."""
    display_name = "Starting items"
    default = ["Crystal", "Dark Crystal"]


class StartingLevel(Range):
    """Character level to start every New Game at, applied as a floor (only ever
    raises you — never below your real level). Default 1 = vanilla. Raise it for a
    gentler opening, especially with Random start dropping you on a higher floor.
    Stacks with catch-up level scaling: you end up at the higher of this and the
    floor's expected level."""
    display_name = "Starting level"
    range_start = 1
    range_end = 60
    default = 1


class StartingWeaponLevel(Range):
    """Displayed weapon level (1-6) to start every New Game with, applied as a
    floor. Default 1 = the vanilla starter weapon. The in-game mod owns the weapon
    (it upgrades on Cleria Ore and re-applies the floor-appropriate weapon on a
    Random-start spawn), so this raises the *minimum*: you begin with at least this
    level, and Cleria Ore / floor-appropriate gear can take you higher."""
    display_name = "Starting weapon level"
    range_start = 1
    range_end = 6
    default = 1


class LevelScaling(Choice):
    """Catch-up leveling so the statue warp network never means a grind wall.
    Compares your level to the floor you're on (per the game's own level curve):

    - ``off``            : vanilla leveling.
    - ``level_floor``    : entering a floor far above your level bumps you up to
                           (floor level - margin); only ever raises you.
    - ``exp_multiplier`` : boosted EXP only — no instant bump. A flat always-on
                           multiplier (default) so you level naturally but fast;
                           raise the catch-up option for extra EXP while behind.
                           This is the DEFAULT: with a low ``max_starting_floor``
                           you begin near-vanilla and just gain levels quicker,
                           rather than being teleported up in power.
    - ``both``           : the bump gets you most of the way, the EXP boost
                           finishes it through play — for high warp-ahead starts."""
    display_name = "Level scaling"
    option_off = 0
    option_level_floor = 1
    option_exp_multiplier = 2
    option_both = 3
    default = 2  # exp_multiplier — flat always-on boost, no instant level bump


class LevelMargin(Range):
    """How many levels under a floor's expected level you may be before Level
    scaling kicks in (also the gap the level-floor bump leaves for you to earn).
    Default 0: with the corrected floor-level curve and a floor-appropriate
    weapon, the level floor brings you to exactly the floor's expected level
    (live-tuned at 18F: expected 27 + weapon Lv4 = the right difficulty). Raise it
    if you want more challenge (you'll be that many levels under); too high and
    combat clamps to 1 damage on warped-ahead floors."""
    display_name = "Level scaling margin"
    range_start = 0
    range_end = 10
    default = 0


class ExpMultiplierBase(Range):
    """Flat EXP multiplier applied everywhere while EXP scaling is on (the
    ``exp_multiplier`` / ``both`` modes). Default 20x: a rando run zig-zags the
    tower out of order and the mod's own EXP hook only reaches kills once fixed,
    so a strong flat boost keeps leveling brisk. 1 = vanilla rate."""
    display_name = "Base EXP multiplier"
    range_start = 1
    range_end = 100
    default = 20


class ExpMultiplierCatchup(Range):
    """EXP multiplier while you're CATCHING UP: your level is at or under the
    expected level of the deepest floor you've visited, plus the margin below.
    Replaces the base multiplier whenever the condition holds, so falling behind
    your furthest progress levels you back fast wherever you choose to fight.
    Defaults to the base rate (20), i.e. a single FLAT always-on multiplier with
    no catch-up spike (the shipped default). Raise it above the base to add a
    behind-you-level-faster boost."""
    display_name = "Catch-up EXP multiplier"
    range_start = 1
    range_end = 100
    default = 20


class ExpCatchupMargin(Range):
    """How many levels ABOVE the deepest-visited floor's expected level still
    count as catching up (the catch-up multiplier applies while
    your level <= expected + this). Default 5."""
    display_name = "Catch-up margin (levels)"
    range_start = 0
    range_end = 20
    default = 5


class BlessingCosts(Choice):
    """Blessing shop economy. ``vanilla`` = buy from the game's own statue menu
    at its normal prices. ``shuffled`` = the mod's OWN shop overlay (F5) sells
    the blessings at seed-randomized SP prices (between the min/max below) — the
    vanilla menu still works at vanilla prices, but the overlay shop is where
    the deals (and ripoffs) are. Purchases either way grant the blessing effect
    AND the multiworld check. (The value can't be called ``random`` — that's a
    reserved yaml keyword for Choice options in Archipelago.)"""
    display_name = "Blessing costs"
    option_vanilla = 0
    option_shuffled = 1
    default = 0


class BlessingCostMin(Range):
    """Lowest possible randomized blessing price (SP)."""
    display_name = "Blessing cost min"
    range_start = 10
    range_end = 2000
    default = 100


class BlessingCostMax(Range):
    """Highest possible randomized blessing price (SP)."""
    display_name = "Blessing cost max"
    range_start = 10
    range_end = 5000
    default = 800


class BlessingShopUnlock(Choice):
    """How the overlay shop's inventory unlocks. ``all`` = every blessing is
    purchasable from the start. ``one_per_floor`` = one more shop slot (cheapest
    first) unlocks for each distinct tower floor you visit — pace your power to
    your exploration."""
    display_name = "Blessing shop unlock"
    option_all = 0
    option_one_per_floor = 1
    default = 0


class ShopHints(DefaultOnToggle):
    """Show what each Divine Blessing purchase actually gives (the multiworld
    item scouted at that shop slot) on the in-game overlay while you're at a
    goddess statue. Off = blessings are blind buys."""
    display_name = "Blessing shop hints"


class ProgressiveArmor(DefaultOnToggle):
    """Make defensive gear progressive. Ys Origin has two gear slots — Armor and
    Boots — each a strict tier ladder per character. When on, every gear chest
    puts a "Progressive Armor" / "Progressive Boots" in the pool instead of a
    specific piece: receiving one grants your character's NEXT tier, so you can't
    get the 22F armor before the 7F one (pickups never skip ahead). Off = raw
    pieces are shuffled as-is (you can find endgame armor first)."""
    display_name = "Progressive armor & boots"


class WeaponRequirements(DefaultOnToggle):
    """Gate each tower zone behind enough Cleria Ore (= weapon upgrades) to fight
    there, so the warp network can't strand you somewhere your weapon can't dent.
    When on, the generator guarantees the **vanilla weapon level for each floor**
    is obtainable (by you, or friends in a multiworld) before that zone is in
    logic, and Cleria Ore becomes progression that upgrades your weapon on pickup.
    Off = no weapon gating (Cleria Ore is filler)."""
    display_name = "Weapon requirements"


class ExpMultiplierMax(Range):
    """DEPRECATED (v1.6.0): the single EXP cap was replaced by the
    ``exp_multiplier_base`` / ``exp_multiplier_catchup`` pair. Kept as an accepted
    no-op so yamls written for v1.3.0-1.5.0 still generate without error; the
    value is ignored. Set the two replacement options instead."""
    display_name = "EXP multiplier max (deprecated)"
    range_start = 1
    range_end = 20
    default = 1
    visibility = 0  # hidden from templates/webhost — legacy compatibility only


class TrapCount(Range):
    """How many filler slots to replace with TRAP items, shuffled into the
    multiworld — you'll send them to other players and receive them yourself.
    Traps: **EXP Leech** (drops a chunk of your EXP), **Chaos Warp** (yanks you to
    a random unlocked statue), **Butterfingers** (weapon reverts to Lv1 for a few
    seconds), **Blinding Fog** (screen hazes over briefly). 0 = no traps."""
    display_name = "Trap count"
    range_start = 0
    range_end = 50
    default = 6


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
    max_starting_floor: MaxStartingFloor
    max_warp_floors_skip: MaxWarpFloorsSkip
    starting_items: StartingItems
    starting_level: StartingLevel
    starting_weapon_level: StartingWeaponLevel
    level_scaling: LevelScaling
    level_margin: LevelMargin
    exp_multiplier_base: ExpMultiplierBase
    exp_multiplier_catchup: ExpMultiplierCatchup
    exp_catchup_margin: ExpCatchupMargin
    exp_multiplier_max: ExpMultiplierMax  # deprecated no-op (legacy yaml compat)
    progressive_armor: ProgressiveArmor
    shop_hints: ShopHints
    blessing_costs: BlessingCosts
    blessing_cost_min: BlessingCostMin
    blessing_cost_max: BlessingCostMax
    blessing_shop_unlock: BlessingShopUnlock
    weapon_requirements: WeaponRequirements
    trap_count: TrapCount
    death_link: DeathLink
