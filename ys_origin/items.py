"""Item definitions for the Ys Origin apworld — Hugo slice.

Names of *grantable* items **must exactly match a key in the client's
``ITEM_OFFSETS``** (``client/offsets.py``): when the AP server sends the item,
the client's ``apply_item`` writes value 1 (or increments) into the item array.

The slice pool is sized to exactly fill the slice's locations. Grow alongside
``locations.py`` as the route is mapped.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

BASE_ID = 0x59_5000


class ItemKind(IntEnum):
    """Mirror of AP's ItemClassification (import-free for standalone use)."""
    FILLER = 0
    PROGRESSION = 1
    USEFUL = 2
    TRAP = 4


@dataclass(frozen=True)
class ItemDef:
    name: str
    kind: ItemKind
    count: int = 1


# Append-only. Grantable names must match client ITEM_OFFSETS keys.
_ITEM_DEFS: list[ItemDef] = [
    ItemDef("Cerulean Flabellum", ItemKind.PROGRESSION),  # key item; grants bubble
    ItemDef("Celcetan Panacea", ItemKind.FILLER, count=2),
    ItemDef("Roda Fruit", ItemKind.FILLER, count=2),
]

item_table: dict[str, ItemDef] = {d.name: d for d in _ITEM_DEFS}
item_name_to_id: dict[str, int] = {
    d.name: BASE_ID + i for i, d in enumerate(_ITEM_DEFS)
}

item_name_groups: dict[str, set[str]] = {
    "Consumables": {"Celcetan Panacea", "Roda Fruit"},
    "Key Items": {"Cerulean Flabellum"},
}

FILLER_ITEM_NAME = "Roda Fruit"
