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

from BaseClasses import Item, ItemClassification, Location, Region, Tutorial
from worlds.AutoWorld import WebWorld, World

from .items import (
    FILLER_ITEM_NAME,
    ItemKind,
    item_name_groups,
    item_name_to_id,
    item_table,
)
from .locations import location_name_to_id, locations_by_region
from .options import YsOriginOptions
from .regions import ALL_REGIONS, CONNECTIONS
from .rules import set_completion_condition, set_rules

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

    # -- items --------------------------------------------------------------- #

    def create_item(self, name: str) -> YsOriginItem:
        kind = item_table[name].kind if name in item_table else ItemKind.FILLER
        return YsOriginItem(
            name, _KIND_TO_AP[kind], self.item_name_to_id[name], self.player
        )

    def get_filler_item_name(self) -> str:
        return FILLER_ITEM_NAME

    def create_items(self) -> None:
        pool: list[YsOriginItem] = []
        for name, d in item_table.items():
            for _ in range(d.count):
                pool.append(self.create_item(name))

        # Size the pool to exactly fill the (non-event) locations.
        gap = len(self.location_name_to_id) - len(pool)
        if gap > 0:
            for _ in range(gap):
                pool.append(self.create_item(self.get_filler_item_name()))
        elif gap < 0:
            pool = pool[:len(self.location_name_to_id)]

        self.multiworld.itempool += pool

    # -- regions / locations ------------------------------------------------- #

    def create_regions(self) -> None:
        regions: dict[str, Region] = {}
        for name in ALL_REGIONS:
            region = Region(name, self.player, self.multiworld)
            regions[name] = region
            self.multiworld.regions.append(region)

        for region_name, loc_names in locations_by_region.items():
            region = regions[region_name]
            for loc_name in loc_names:
                region.locations.append(
                    YsOriginLocation(
                        self.player, loc_name,
                        self.location_name_to_id[loc_name], region,
                    )
                )

        for src, dst in CONNECTIONS:
            regions[src].connect(regions[dst], f"{src} -> {dst}")

    # -- rules + slot data --------------------------------------------------- #

    def set_rules(self) -> None:
        set_rules(self)
        set_completion_condition(self)

    def fill_slot_data(self) -> dict[str, Any]:
        # The client uses location_signals to turn detected flag flips into
        # LocationChecks. Names here == client LOCATION_FLAG_OFFSETS keys.
        return {
            "character": int(self.options.character.value),
            "goal": int(self.options.goal.value),
            "location_signals": dict(self.location_name_to_id),
        }
