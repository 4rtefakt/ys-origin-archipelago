"""Location (check) definitions for the Ys Origin apworld.

One location per chest, derived from the extracted dataset (``data_tables``).
Each location name is unique and carries its zone + room (e.g.
``"Wailing Blue: 2F Path 1"``). The client maps a detected box-flag flip back to
the location via ``slot_data`` (name → id, and name → g_flags box flag).
"""

from __future__ import annotations

from .data_tables import (
    location_box_flag,
    location_name_to_id,
    location_vanilla_item,
    locations_by_region,
)

__all__ = [
    "location_name_to_id",
    "locations_by_region",
    "location_box_flag",
    "location_vanilla_item",
]
