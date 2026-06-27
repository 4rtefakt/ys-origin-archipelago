"""Static memory offsets for ``yso_win.exe`` (Steam, v1.1.1.0).

All values are **module-relative** offsets from the ``yso_win.exe`` image base.
Resolve to an absolute address with ``ProcessMemory.resolve(offset)``.

Offsets that have not yet been reverse-engineered are set to ``UNKNOWN``
(``None``). Reading/writing one must go through :func:`require` so the failure
is a clear :class:`OffsetNotMapped` rather than a silent ``None`` arithmetic
error.

Sources:
  * Community Cheat Engine tables (fearlessrevolution.com viewtopic t=8573)
  * In-house reverse engineering via ``tools/scan.py``

Confirmed offsets are marked ``# confirmed``; the rest are placeholders to be
filled in once ``scan.py`` pins them down. Do not trust a placeholder.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, fields, replace
from typing import Optional

log = logging.getLogger("ys_origin.offsets")

# Sentinel for an offset that has not been reverse-engineered yet.
UNKNOWN: Optional[int] = None

MODULE_NAME = "yso_win.exe"
GAME_VERSION = "1.1.1.0"


class OffsetNotMapped(Exception):
    """Raised when code tries to use an offset that is still ``UNKNOWN``."""


def require(name: str, value: Optional[int]) -> int:
    """Return ``value`` if it is mapped, else raise :class:`OffsetNotMapped`.

    Use at every call site that resolves an offset so that an unmapped offset
    fails loudly and points at exactly which field needs RE work::

        addr = mem.resolve(require("double_jump", OFFSETS.double_jump))
    """
    if value is None:
        raise OffsetNotMapped(
            f"Offset {name!r} is not mapped yet. Discover it with tools/scan.py "
            "and fill it into client/offsets.py."
        )
    return value


@dataclass(frozen=True)
class Offsets:
    """Typed table of every offset the client cares about.

    A value of ``None`` means "not reverse-engineered yet". Keep this in sync
    with whatever ``scan.py`` reports; the field names here are referenced by
    string in :func:`require` calls and in ``game_state.py``.
    """

    # -- stats (read-only anchors, NOT used as randomized state) ------------ #
    # NOTE: the values below came from a community CT for a DIFFERENT build
    # ("Steam Apr 13 2018"); they do NOT apply to v1.1.1.0 (verified via live RE
    # — that build's EXP is a dynamic int32, not a static float here). Left
    # UNKNOWN until rediscovered with tools/. See README "Known offsets".
    exp: Optional[int] = UNKNOWN           # dynamic int32 in this build
    hit_counter: Optional[int] = UNKNOWN

    # Dynamic / pointer-chased values. These move per session and must NOT be
    # used as a static randomization signal. Listed for the scanner & docs.
    current_hp: Optional[int] = UNKNOWN    # float, dynamic (pointer chain)
    current_mp: Optional[int] = UNKNOWN    # float, dynamic
    current_sp: Optional[int] = UNKNOWN    # float, dynamic
    current_level: Optional[int] = UNKNOWN  # int, max 60
    current_oxygen: Optional[int] = UNKNOWN  # int, max 6000

    # -- progression flags (these become AP items) ------------------------- #
    double_jump: Optional[int] = UNKNOWN   # 1 = unlocked
    dash: Optional[int] = UNKNOWN          # 1 = unlocked
    boost_ex_mode: Optional[int] = UNKNOWN  # boost / EX mode flag

    # Spell states: 0 = none, 1 = normal, 2 = fully charged.
    wind_spell: Optional[int] = UNKNOWN
    thunder_spell: Optional[int] = UNKNOWN
    fire_spell: Optional[int] = UNKNOWN

    # -- gems / power-ups (counters, max 3 each) --------------------------- #
    emerald_count: Optional[int] = UNKNOWN  # Wind
    topaz_count: Optional[int] = UNKNOWN    # Thunder
    ruby_count: Optional[int] = UNKNOWN     # Fire

    # -- equipment (current equipped item IDs) ----------------------------- #
    weapon_level: Optional[int] = UNKNOWN   # max 10
    armor_id: Optional[int] = UNKNOWN
    boots_id: Optional[int] = UNKNOWN
    accessory_id: Optional[int] = UNKNOWN

    # -- progress / location signals --------------------------------------- #
    current_floor: Optional[int] = UNKNOWN  # which Darm Tower floor is active

    # Persistent item/skill array (~+0x36BAxx): int32 per entry, -1 = never
    # obtained, >=1 = obtained/count. Items, key items, AND the skills they grant
    # all live here. Base/indexing not fully mapped yet — use ITEM_OFFSETS below
    # for the specific entries confirmed by live RE.
    item_array_base: Optional[int] = UNKNOWN

    # Contiguous per-location pickup-flag array (~+0x36BDxx): int32 per flag,
    # 0 = not collected, 1 = collected. Indices are stable per location (verified
    # by gaps in the set flags). Use LOCATION_FLAG_OFFSETS for confirmed flags.
    location_flags_base: Optional[int] = UNKNOWN

    # legacy fields (kept for compatibility; superseded by the arrays above)
    key_items_base: Optional[int] = UNKNOWN
    key_items_stride: int = 1

    def mapped(self) -> dict[str, int]:
        """Return only the fields that are currently mapped (non-None)."""
        out: dict[str, int] = {}
        for f in fields(self):
            val = getattr(self, f.name)
            if isinstance(val, int) and f.name != "key_items_stride":
                out[f.name] = val
        return out

    def unmapped(self) -> list[str]:
        """Return the names of every offset still awaiting RE."""
        return [
            f.name
            for f in fields(self)
            if getattr(self, f.name) is None
        ]


# Names of every real offset field (excludes the non-offset stride knob).
OFFSET_FIELD_NAMES: frozenset[str] = frozenset(
    f.name for f in fields(Offsets) if f.name != "key_items_stride"
)


def build_offsets() -> "Offsets":
    """Overlay the JSON sidecar (``offsets.json``) onto the hard-coded defaults.

    Scanned discoveries in the sidecar take precedence, so newly-mapped offsets
    take effect on the next client start without editing this file. Unknown or
    unparseable keys are warned about and ignored.
    """
    # Imported here (not at module top) to avoid an import cycle.
    from .offset_store import load_overrides

    base = Offsets()
    applied: dict[str, int] = {}
    for name, value in load_overrides().items():
        if name in OFFSET_FIELD_NAMES:
            applied[name] = value
        else:
            log.warning("offsets.json: unknown field %r ignored", name)
    if applied:
        log.info("loaded %d offset(s) from sidecar: %s",
                 len(applied), ", ".join(sorted(applied)))
    return replace(base, **applied)


# The single shared instance the rest of the client imports. Defaults overlaid
# with anything reverse-engineered into offsets.json.
OFFSETS = build_offsets()


# --------------------------------------------------------------------------- #
# Confirmed array entries (from live reverse engineering, v1.1.1.0)
# --------------------------------------------------------------------------- #
#
# These are module-relative offsets of *individual* entries in the two arrays
# above, confirmed against the running game. Resolve with mem.resolve(offset).

# Item/skill array entries. Granting = write 1 (see GRANT_SAFE_MIN). Consumable
# COUNTS may be written to any positive value. Add entries here as they're mapped.
#
# !!! SAFETY: NEVER write a value < 1 (e.g. -1 to "un-obtain") to a key-item or
# skill entry while the game is running — it leaves a dangling skill-object
# pointer and FREEZES the game. apply_item enforces this. !!!
ITEM_OFFSETS: dict[str, int] = {
    "Roda Fruit": 0x36BA78,              # consumable (count-safe)
    "Celcetan Panacea": 0x36BA80,        # consumable (count-safe)
    "Cerulean Flabellum": 0x36BAC8,      # key item; grants the bubble skill
    "Protective Bubble": 0x36BAEC,        # the skill granted by the Flabellum
}

# Per-location/event flags from the universal progress array at ~+0x36BDxx
# (one int32 per flag, stable indices). Read-only for detection: == 1 means
# collected/triggered. Covers chests, item pickups, pressure plates, events.
LOCATION_FLAG_OFFSETS: dict[str, int] = {
    "Chest 1 - Panacea": 0x36BDD4,
    "Flabellum Altar": 0x36BDDC,
    "Pressure Plate (4F)": 0x36BDE4,
    "Chest 2 - Roda Fruit (4F)": 0x36BDE8,
    "Pressure Plate 2 - Door East (4F)": 0x36BDF0,
}

# Minimum value apply_item is allowed to write to an ITEM_OFFSETS entry. Writing
# below this (clearing a key item/skill) can freeze the game — hard floor.
GRANT_SAFE_MIN = 1

