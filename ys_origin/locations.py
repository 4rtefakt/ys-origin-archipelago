"""Location (check) definitions for the Ys Origin apworld — Hugo slice.

Each location name **must exactly match a key in the client's
``LOCATION_FLAG_OFFSETS``** (``client/offsets.py``): the client reads the
corresponding ``+0x36BDxx`` event flag, and when it flips 0->1 it emits that
exact name as the check signal. The world publishes ``location_name_to_id`` to
the client via ``slot_data['location_signals']`` so the name maps to an AP id.

Keep this list and the client registry in lock-step. Add locations as they are
confirmed with ``tools/snapdiff.py``.
"""

from __future__ import annotations

from .regions import HUGO_REGION

BASE_ID = 0x59_6000

# Confirmed Hugo-route locations (== client LOCATION_FLAG_OFFSETS keys).
HUGO_LOCATIONS: list[str] = [
    "Chest 1 - Panacea",
    "Flabellum Altar",
    "Pressure Plate (4F)",
    "Chest 2 - Roda Fruit (4F)",
    "Pressure Plate 2 - Door East (4F)",
]

location_name_to_id: dict[str, int] = {
    name: BASE_ID + i for i, name in enumerate(HUGO_LOCATIONS)
}

# region -> [location names]
locations_by_region: dict[str, list[str]] = {HUGO_REGION: list(HUGO_LOCATIONS)}
