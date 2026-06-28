"""Item definitions for the Ys Origin apworld.

The pool is built per-world from the active locations (see ``__init__`` /
``data_tables``); this module just maps names→ids, names→classification, and
exposes the item groups. Names match the client's item registry.
"""

from __future__ import annotations

from enum import IntEnum

from .data_tables import (
    item_classification,
    item_name_to_id as _item_name_to_id,
)


class ItemKind(IntEnum):
    """Mirror of AP's ItemClassification."""
    FILLER = 0
    PROGRESSION = 1
    USEFUL = 2
    TRAP = 4


_CLASS_TO_KIND = {
    "filler": ItemKind.FILLER,
    "progression": ItemKind.PROGRESSION,
    "useful": ItemKind.USEFUL,
    "trap": ItemKind.TRAP,
}


def kind_of(name: str) -> ItemKind:
    return _CLASS_TO_KIND[item_classification(name)]


item_name_to_id: dict[str, int] = dict(_item_name_to_id)

item_name_groups: dict[str, set[str]] = {}
for _name in item_name_to_id:
    item_name_groups.setdefault(kind_of(_name).name.title(), set()).add(_name)
