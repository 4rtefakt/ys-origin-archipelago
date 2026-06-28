"""Item definitions for the Ys Origin apworld.

Derived from the extracted game data (``data_tables``): the vanilla item pool is
the canonical item of each chest, classified by the INVINFO id-ranges. Grantable
names match the client's item registry (g_flags item index → name).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .data_tables import (
    FILLER_ITEM_NAME,
    item_classification,
    item_counts,
    item_name_to_id as _item_name_to_id,
)


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


_CLASS_TO_KIND = {
    "filler": ItemKind.FILLER,
    "progression": ItemKind.PROGRESSION,
    "useful": ItemKind.USEFUL,
    "trap": ItemKind.TRAP,
}

item_table: dict[str, ItemDef] = {
    name: ItemDef(name, _CLASS_TO_KIND[item_classification(name)], count)
    for name, count in item_counts.items()
}

item_name_to_id: dict[str, int] = dict(_item_name_to_id)

# Group items by classification for the YAML/UI.
item_name_groups: dict[str, set[str]] = {}
for _name, _d in item_table.items():
    item_name_groups.setdefault(_d.kind.name.title(), set()).add(_name)
