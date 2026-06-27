"""Region definitions for the Ys Origin apworld.

Vertical slice: a single Darm Tower region for the Hugo route, reached from the
mandatory ``Menu`` origin. Floor-by-floor regions and per-character gating come
later as more of the route is mapped.
"""

from __future__ import annotations

MENU = "Menu"
HUGO_REGION = "Darm Tower (Hugo)"

ALL_REGIONS: list[str] = [MENU, HUGO_REGION]

# (from_region, to_region) — rules attached in rules.py (none for the slice).
CONNECTIONS: list[tuple[str, str]] = [(MENU, HUGO_REGION)]
