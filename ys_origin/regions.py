"""Region definitions for the Ys Origin apworld.

A linear chain of Darm Tower zone regions (Wailing Blue → … → Demonic Core)
from the ``Menu`` origin, derived from the extracted dataset. Boss-medallion
gates between zones are attached in ``rules.py``.
"""

from __future__ import annotations

from .data_tables import ALL_REGIONS, CONNECTIONS, MENU

__all__ = ["ALL_REGIONS", "CONNECTIONS", "MENU"]
