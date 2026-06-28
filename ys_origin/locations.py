"""Location definitions for the Ys Origin apworld.

All location metadata lives in ``data_tables`` (loaded from ``data/locations.json``).
``location_name_to_id`` is the stable full map AP needs; the active subset and
the per-region grouping are selected per-world from the YAML options.
"""

from __future__ import annotations

from .data_tables import LOC_META, location_name_to_id

__all__ = ["location_name_to_id", "LOC_META"]
