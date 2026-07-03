"""Ys Origin — Archipelago world definition (Hugo vertical slice).

Zip this directory to ``ys_origin.apworld`` and drop it in Archipelago's
``custom_worlds/`` folder to make "Ys Origin" available to the generator.

This first pass is a minimal, *real* slice of the Hugo route: the locations and
items that have been confirmed in memory (see ``RE_FINDINGS.md``). Location and
grantable-item names are kept identical to the client's ``LOCATION_FLAG_OFFSETS``
/ ``ITEM_OFFSETS`` so the live client can map checks and item grants directly.
"""

from __future__ import annotations

from typing import Any

from BaseClasses import (
    Item,
    ItemClassification,
    Location,
    LocationProgressType,
    Region,
    Tutorial,
)
from worlds.AutoWorld import WebWorld, World

from .items import ItemKind, item_name_groups, item_name_to_id, kind_of
from .locations import LOC_META, location_name_to_id
from .options import YsOriginOptions
from .regions import ALL_REGIONS, CONNECTIONS
from .rules import set_completion_condition, set_rules
from . import data_tables as dt

_KIND_TO_AP = {
    ItemKind.FILLER: ItemClassification.filler,
    ItemKind.PROGRESSION: ItemClassification.progression,
    ItemKind.USEFUL: ItemClassification.useful,
    ItemKind.TRAP: ItemClassification.trap,
}


class YsOriginItem(Item):
    game = "Ys Origin"


class YsOriginLocation(Location):
    game = "Ys Origin"


class YsOriginWeb(WebWorld):
    theme = "dirt"
    tutorials = [
        Tutorial(
            "Multiworld Setup Guide",
            "A guide to setting up the Ys Origin client and apworld.",
            "English",
            "setup_en.md",
            "setup/en",
            ["YsOrigin AP contributors"],
        )
    ]


class YsOriginWorld(World):
    """Ys Origin randomizer world (Hugo slice)."""

    game = "Ys Origin"
    web = YsOriginWeb()

    options_dataclass = YsOriginOptions
    options: YsOriginOptions

    item_name_to_id = item_name_to_id
    location_name_to_id = location_name_to_id
    item_name_groups = item_name_groups

    # -- early setup --------------------------------------------------------- #

    def generate_early(self) -> None:
        """Pick the spawn / start-unlocked statue and decide the graph mode.

        ``open_mode`` (random spawn) swaps the linear zone backbone for the warp
        hub + bidirectional room graph. RNG is consumed here ONLY when
        ``random_start`` is on, so normal (non-random) seeds are byte-identical
        to before — the draw simply moved out of ``fill_slot_data``."""
        o = self.options
        self.open_mode = bool(o.random_start.value)
        if o.random_start.value:
            # Cap the spawn to a survivable floor (max_starting_floor); a 25F start
            # drops the player into brutal rooms with no gear behind them.
            candidates = dt.start_statue_candidates(int(o.max_starting_floor.value))
            self.start_statue_scene = self.random.choice(candidates)
        elif o.statue_warp_locks.value:
            self.start_statue_scene = 1000          # 1F starting statue
        else:
            self.start_statue_scene = 0             # locks off: no forced start

    # -- items --------------------------------------------------------------- #

    def create_item(self, name: str) -> YsOriginItem:
        cls = _KIND_TO_AP[kind_of(name)]
        # Cleria Ore becomes progression when weapon gating is on (it's the
        # weapon-upgrade currency the zone gates require).
        if name == dt.CLERIA_ORE and self.options.weapon_requirements.value:
            cls = ItemClassification.progression
        # In open mode the warp network is part of the reachability spine, so a
        # statue's unlock item gates real access -> promote it to progression
        # (it's merely "useful" convenience in normal, on-foot seeds).
        elif getattr(self, "open_mode", False) and name in dt.STATUE_UNLOCKS:
            cls = ItemClassification.progression
        return YsOriginItem(name, cls, self.item_name_to_id[name], self.player)

    def get_filler_item_name(self) -> str:
        return self.random.choice(dt.FILLER_POOL)

    def _active_locations(self) -> dict[str, list[str]]:
        return dt.locations_by_region(dt.enabled_categories(self.options))

    def create_items(self) -> None:
        enabled = dt.enabled_categories(self.options)
        n_locations = sum(len(v) for v in dt.locations_by_region(enabled).values())

        # One real (vanilla) item per enabled chest/event location; pad the rest
        # (boss/floor/room sanity checks) with varied filler.
        char = dt.char_name(self.options)
        pool = [self.create_item(n) for n in dt.vanilla_items(
            enabled, char, bool(self.options.progressive_armor.value))]
        # statue warp-unlock items (one per statue) when the option is on; they
        # take real-item slots, displacing that many filler.
        if self.options.statue_warp_locks.value:
            pool += [self.create_item(n) for n in dt.statue_unlock_items()]
        for _ in range(n_locations - len(pool)):
            pool.append(self.create_item(self.get_filler_item_name()))

        self.multiworld.itempool += pool

    # -- regions / locations ------------------------------------------------- #

    def create_regions(self) -> None:
        open_mode = getattr(self, "open_mode", False)
        region_names = dt.open_regions() if open_mode else ALL_REGIONS
        connections = (dt.warp_connections() + dt.open_scene_connections()
                       if open_mode else CONNECTIONS)

        regions: dict[str, Region] = {}
        for name in region_names:
            region = Region(name, self.player, self.multiworld)
            regions[name] = region
            self.multiworld.regions.append(region)

        for region_name, loc_names in self._active_locations().items():
            region = regions[region_name]
            for loc_name in loc_names:
                loc = YsOriginLocation(
                    self.player, loc_name,
                    self.location_name_to_id[loc_name], region,
                )
                # Provisional checks (not yet confirmed live-detectable) hold
                # filler only, so a played seed stays beatable via confirmed ones.
                if dt.is_excluded(loc_name):
                    loc.progress_type = LocationProgressType.EXCLUDED
                region.locations.append(loc)

        for src, dst in connections:
            regions[src].connect(regions[dst], f"{src} -> {dst}")

    # -- rules + slot data --------------------------------------------------- #

    def set_rules(self) -> None:
        set_rules(self)
        set_completion_condition(self)

    def _blessing_costs(self, active: set) -> dict[str, int]:
        """Overlay-shop SP prices (loc id -> price), weighted by the tower depth of
        the item placed at each slot so early/filler is cheap and late medallions
        and skills are expensive. Deterministic (uses self.random)."""
        cmin = int(self.options.blessing_cost_min.value)
        cmax = max(cmin, int(self.options.blessing_cost_max.value))
        id_to_name = {i: n for n, i in self.location_name_to_id.items()}
        costs: dict[str, int] = {}
        for loc_id in dt.blessing_bit_location_ids(active, self.location_name_to_id):
            item = self.get_location(id_to_name[loc_id]).item
            costs[str(loc_id)] = dt.weighted_blessing_cost(
                item.name, item.player == self.player, bool(item.advancement),
                self.random, cmin, cmax)
        return costs

    def fill_slot_data(self) -> dict[str, Any]:
        # The client builds its detection map from slot data:
        #   location_signals : active location name -> AP location id
        #   location_detect  : active location name -> {method, flag/item/scene}
        # method is "box_flag" / "item_flag" (detectable live today) or
        # "scene"/"scene_floor" (needs a current-scene memory offset — pending).
        active = {n for names in self._active_locations().values() for n in names}
        locks = bool(self.options.statue_warp_locks.value)
        # With warp-locks on, the mod suppresses each locked statue's
        # purification (its activation flag write) so it stays dark. That flag is
        # also the statue CHECK, so detection is switched to scene-method (firing
        # on room entry) to keep checks reachable on foot regardless of locks.
        statue_scenes = dt.statue_location_scenes() if locks else {}
        location_detect = {}
        for n in active:
            if n in statue_scenes:
                location_detect[n] = {"method": "scene", "scene": statue_scenes[n]}
            else:
                location_detect[n] = LOC_META[n]["detect"]
        # Which statue starts unlocked (always-usable so the player can save).
        # Default: the 1F starting statue (S_1000); Random start picks any statue.
        # Chosen in generate_early so create_regions/set_rules use the same value.
        start_statue = self.start_statue_scene if locks else 0
        return {
            "character": int(self.options.character.value),
            "goal": int(self.options.goal.value),
            "death_link": bool(self.options.death_link.value),
            # statue warp locks: on/off + item name -> {scene, flag} so the mod
            # knows which statue each received unlock item enables, plus which
            # statue starts unlocked (start_statue_scene; 0 when locks are off).
            "statue_warp_locks": locks,
            "statue_unlocks": (dt.statue_unlock_slot_data() if locks else {}),
            "random_start": bool(self.options.random_start.value),
            "start_statue_scene": start_statue,
            # Spawn loadout weapon record value (g_flags[0x94]), applied by the mod
            # as a floor at New Game. The higher of: the vanilla weapon for a
            # random-start spawn floor and the player's configured
            # starting_weapon_level. 0 = starter. The floor-appropriate weapon is
            # part of the INSTANT catch-up bundle (with the level bump), so it only
            # applies when level_scaling includes the bump (level_floor=1 / both=3);
            # under the default exp_multiplier(2)/off(0) you keep the starter weapon
            # and earn it back through Cleria Ore, paced by a low max_starting_floor.
            "start_weapon": max(
                (dt.floor_weapon_value(dt.scene_floor(start_statue) or 1)
                 if (self.options.random_start.value
                     and start_statue not in (0, 1000)
                     and self.options.level_scaling.value in (1, 3))
                 else 0),
                dt.weapon_value_for_level(int(self.options.starting_weapon_level.value)),
            ),
            # New-Game starting loadout (applied as a floor by the mod/client):
            #   start_level : minimum character level (1 = vanilla, only raises).
            #   start_items : g_flags indices to mark owned (default = warp Crystals
            #                 the skipped intro would grant). Unknown names dropped.
            "start_level": int(self.options.starting_level.value),
            "start_items": dt.start_item_indices(
                list(self.options.starting_items.value)
            ),
            # SP filler grants: item name -> amount added to the SP currency cell
            # (g_flags[0xD8]); SP is a stat, not an inventory item, so the mod
            # needs the explicit map rather than a g_flags item index.
            "sp_items": dict(dt.SP_FILLER),
            "sp_flag_idx": dt.SP_FLAG_IDX,
            # Progressive gear: item name -> the selected character's tier ladder
            # as g_flags indices; receiving one grants the first unowned tier.
            "progressive_gear": (
                dt.progressive_gear_slot_data(dt.char_name(self.options))
                if self.options.progressive_armor.value else {}
            ),
            # catch-up level scaling: mode + tuning + the floor->expected-level
            # curve, so the mod can bump under-leveled players / boost their EXP
            # when the warp network drops them somewhere too high.
            "level_scaling": int(self.options.level_scaling.value),
            "level_margin": int(self.options.level_margin.value),
            # EXP scaling: flat base multiplier everywhere; the catch-up
            # multiplier while level <= (deepest visited floor's expected level
            # + margin). The mod tracks the deepest-floor high-water mark.
            "exp_base_mult": int(self.options.exp_multiplier_base.value),
            "exp_catchup_mult": int(self.options.exp_multiplier_catchup.value),
            "exp_catchup_margin": int(self.options.exp_catchup_margin.value),
            # weapon gating: Cleria Ore = weapon level (for the eventual mod hook
            # that upgrades the weapon on Cleria Ore receipt instead of granting it).
            "weapon_requirements": int(self.options.weapon_requirements.value),
            # expected level keyed by SCENE (current_floor/0xCF is unreliable for
            # warp destinations; the mod reads current_scene/0x1F9 reliably).
            "scene_levels": dt.scene_levels(),
            # scene leaf number -> tower floor, for reliable "distinct floors
            # visited" (blessing-shop one-per-floor pacing) off current_scene.
            "scene_floors": dt.scene_floors(),
            "location_signals": {
                n: i for n, i in self.location_name_to_id.items() if n in active
            },
            "location_detect": location_detect,
            # item name -> g_flags item index, so the client can grant anything.
            "item_index": dt.item_index,
            # scene leaf number -> room name, for the in-game overlay's current
            # room line (and scene-method check display).
            "scene_names": dt.scene_names(),
            # Overlay trackers: per-scene / per-floor active location ids (the
            # "checks here" line + the per-floor remaining list at statues), the
            # blessing shop-hint names, and whether to show shop hints at all.
            "scene_locations": dt.scene_locations_map(active, self.location_name_to_id),
            "floor_locations": dt.floor_locations_map(active, self.location_name_to_id),
            "blessing_names": dt.blessing_location_names(active, self.location_name_to_id),
            "shop_hints": bool(self.options.shop_hints.value),
            # Overlay blessing shop (blessing_costs: random): loc id -> SP price,
            # rolled seed-deterministically in [min, max] (rounded to 10s); empty
            # in vanilla mode (RNG untouched -> vanilla seeds stay identical).
            # blessing_shop_unlock paces the shop inventory (0 all, 1 per-floor).
            "blessing_costs": (
                self._blessing_costs(active)
                if self.options.blessing_costs.value else {}
            ),
            "blessing_shop_unlock": int(self.options.blessing_shop_unlock.value),
            # All statue scenes (panel trigger; statue_unlocks only ships with
            # warp locks on).
            "statue_scenes": dt.statue_scenes(),
            # g_flags indices of the vanilla content of active chest/event
            # locations — the in-game mod suppresses these (player gets the AP
            # item over the network instead).
            "suppress_items": sorted({
                dt.item_index[dt.location_vanilla_item(n, dt.char_name(self.options))]
                for n in active
                if dt.location_vanilla_item(n, dt.char_name(self.options))
                and dt.location_vanilla_item(n, dt.char_name(self.options)) in dt.item_index
            }),
        }
