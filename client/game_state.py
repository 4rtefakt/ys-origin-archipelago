"""Poll Ys Origin game state and translate AP items into memory writes.

Two responsibilities:

  * :func:`poll` snapshots every *mapped* offset into a :class:`GameState`.
  * :func:`detect_checks` diffs two snapshots to find newly-acquired things,
    which the AP client turns into ``LocationChecks``.
  * :func:`apply_item` writes the effect of a received AP item into the game.

Everything degrades gracefully: if an offset is still ``UNKNOWN`` the relevant
field stays ``None`` and is simply skipped, so the client keeps working as more
offsets get reverse-engineered. Nothing here raises on an unmapped offset —
only explicit writes via :func:`apply_item` will, since silently dropping a
received item would desync the multiworld.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:  # avoid an import cycle (suppression imports this module)
    from .suppression import Suppressor

from .memory import ProcessMemory, MemoryError_
from .offsets import (
    GRANT_SAFE_MIN,
    ITEM_OFFSETS,
    LOCATION_FLAG_OFFSETS,
    OFFSETS,
    Offsets,
    OffsetNotMapped,
    require,
)

log = logging.getLogger("ys_origin.game_state")

POLL_INTERVAL_S = 0.5  # 500 ms, per spec


# --------------------------------------------------------------------------- #
# Snapshot
# --------------------------------------------------------------------------- #


@dataclass
class GameState:
    """A snapshot of the randomization-relevant game state at one instant."""

    valid: bool = False  # False if the process was unreadable this tick

    current_floor: Optional[int] = None

    # progression flags
    double_jump: Optional[int] = None
    dash: Optional[int] = None
    boost_ex_mode: Optional[int] = None

    # spell tiers (0/1/2)
    wind_spell: Optional[int] = None
    thunder_spell: Optional[int] = None
    fire_spell: Optional[int] = None

    # gem counters
    emerald_count: Optional[int] = None
    topaz_count: Optional[int] = None
    ruby_count: Optional[int] = None

    # equipment
    weapon_level: Optional[int] = None
    armor_id: Optional[int] = None
    boots_id: Optional[int] = None
    accessory_id: Optional[int] = None

    # confirmed item/skill array entries (name -> int; -1 = not obtained)
    items: dict[str, int] = field(default_factory=dict)
    # confirmed per-location pickup flags (name -> 0/1)
    location_flags: dict[str, int] = field(default_factory=dict)

    # raw key-item flags (index -> 0/1), only populated if legacy base is mapped
    key_items: dict[int, int] = field(default_factory=dict)


# Fields read as a plain int32 from a single offset.
_INT_FIELDS = (
    "current_floor",
    "double_jump",
    "dash",
    "boost_ex_mode",
    "wind_spell",
    "thunder_spell",
    "fire_spell",
    "emerald_count",
    "topaz_count",
    "ruby_count",
    "weapon_level",
    "armor_id",
    "boots_id",
    "accessory_id",
)

# How many key-item flags to scan if a base is provided.
KEY_ITEMS_COUNT = 64


def poll(memory: ProcessMemory, offsets: Offsets = OFFSETS) -> GameState:
    """Read every mapped offset into a fresh :class:`GameState`.

    Never raises on unmapped offsets — they are skipped. If the whole process
    is gone, returns a ``GameState`` with ``valid=False``.
    """
    state = GameState()
    if not memory.is_alive():
        return state

    for name in _INT_FIELDS:
        off = getattr(offsets, name)
        if off is None:
            continue
        try:
            setattr(state, name, memory.read_offset_int32(off))
        except MemoryError_ as e:
            log.debug("read %s failed: %s", name, e)

    # Confirmed item/skill array entries and per-location pickup flags.
    for name, off in ITEM_OFFSETS.items():
        try:
            state.items[name] = memory.read_offset_int32(off)
        except MemoryError_ as e:
            log.debug("read item %s failed: %s", name, e)
    for name, off in LOCATION_FLAG_OFFSETS.items():
        try:
            state.location_flags[name] = memory.read_offset_int32(off)
        except MemoryError_ as e:
            log.debug("read location flag %s failed: %s", name, e)

    if offsets.key_items_base is not None:
        base = offsets.key_items_base
        stride = offsets.key_items_stride
        for i in range(KEY_ITEMS_COUNT):
            try:
                addr = memory.resolve(base + i * stride)
                state.key_items[i] = memory.read_int8(addr) & 0xFF
            except MemoryError_:
                break

    state.valid = True
    return state


# --------------------------------------------------------------------------- #
# Diff -> location checks
# --------------------------------------------------------------------------- #


def detect_checks(prev: GameState, curr: GameState) -> List[str]:
    """Return human-readable signals for things newly acquired curr vs prev.

    These strings are mapped to AP location IDs by the AP client. The mapping
    is intentionally string-based and loose for now; tighten once locations are
    finalised in the apworld. Returns ``[]`` if either snapshot is invalid.
    """
    if not (prev.valid and curr.valid):
        return []

    checks: List[str] = []

    def newly_set(attr: str) -> bool:
        a, b = getattr(prev, attr), getattr(curr, attr)
        return a is not None and b is not None and a == 0 and b >= 1

    def increased(attr: str) -> bool:
        a, b = getattr(prev, attr), getattr(curr, attr)
        return a is not None and b is not None and b > a

    if newly_set("double_jump"):
        checks.append("Double Jump")
    if newly_set("dash"):
        checks.append("Dash")
    if newly_set("boost_ex_mode"):
        checks.append("Boost/EX Mode")

    for spell in ("wind_spell", "thunder_spell", "fire_spell"):
        if increased(spell):
            tier = getattr(curr, spell)
            checks.append(f"{spell.split('_')[0].title()} Spell Tier {tier}")

    for gem in ("emerald_count", "topaz_count", "ruby_count"):
        if increased(gem):
            count = getattr(curr, gem)
            name = {"emerald_count": "Emerald", "topaz_count": "Topaz",
                    "ruby_count": "Ruby"}[gem]
            checks.append(f"{name} {count}")

    if increased("weapon_level"):
        checks.append(f"Weapon Level {curr.weapon_level}")

    for attr in ("armor_id", "boots_id", "accessory_id"):
        a, b = getattr(prev, attr), getattr(curr, attr)
        if a is not None and b is not None and a != b and b != 0:
            checks.append(f"{attr.replace('_id', '').title()} #{b}")

    # newly-set per-location pickup flags — the primary check signal. The signal
    # string is the raw flag name, which equals the apworld location name (so the
    # AP client maps it via slot_data['location_signals']).
    for loc, val in curr.location_flags.items():
        # Fire when a watched cell crosses into "done": box flags 0->1, item /
        # blessing flags -1/0 -> >=1. (prev has all keys after the first poll.)
        if val >= 1 and prev.location_flags.get(loc, 1) < 1:
            checks.append(loc)

    # newly-obtained items/skills (entry goes from <1 to >=1).
    for item, val in curr.items.items():
        if val >= 1 and prev.items.get(item, -1) < 1:
            checks.append(f"Obtained: {item}")

    # legacy raw key-item flags (only if a legacy base was mapped)
    for idx, val in curr.key_items.items():
        if val and not prev.key_items.get(idx, 0):
            checks.append(f"Key Item #{idx}")

    return checks


# --------------------------------------------------------------------------- #
# AP item -> memory write
# --------------------------------------------------------------------------- #


def _set_flag(memory: ProcessMemory, name: str, off: Optional[int]) -> None:
    memory.write_offset_int32(require(name, off), 1)


def _increment(memory: ProcessMemory, name: str, off: Optional[int],
               cap: Optional[int] = None) -> None:
    resolved = require(name, off)
    cur = memory.read_offset_int32(resolved)
    nxt = cur + 1
    if cap is not None:
        nxt = min(nxt, cap)
    memory.write_offset_int32(resolved, nxt)


def _set_tier(memory: ProcessMemory, name: str, off: Optional[int],
              tier: int) -> None:
    resolved = require(name, off)
    cur = memory.read_offset_int32(resolved)
    if tier > cur:  # never downgrade
        memory.write_offset_int32(resolved, tier)


def _grant_item(memory: ProcessMemory, name: str, count: int = 1) -> None:
    """Grant an entry in the confirmed item/skill array.

    Adds ``count`` (key items become 1, consumables increment). NEVER writes
    below :data:`GRANT_SAFE_MIN` — clearing a key-item/skill entry leaves a
    dangling skill-object pointer and freezes the game (learned the hard way).
    """
    off = ITEM_OFFSETS[name]
    cur = memory.read_offset_int32(off)
    base = cur if cur >= 1 else 0          # treat -1 ("never obtained") as 0
    target = max(base + count, GRANT_SAFE_MIN)
    memory.write_offset_int32(off, target)
    log.info("granted %r: %d -> %d (+0x%X)", name, cur, target, off)


def apply_item(memory: ProcessMemory, item_name: str,
               offsets: Offsets = OFFSETS,
               suppressor: "Optional[Suppressor]" = None) -> bool:
    """Apply the effect of a received AP item to the running game.

    Returns ``True`` if a write was performed. Raises :class:`OffsetNotMapped`
    if the item maps to an offset that has not been reverse-engineered yet —
    callers should surface this rather than silently dropping the item.
    Returns ``False`` for items that have no in-memory effect (pure filler).

    When ``suppressor`` is supplied and enabled, item-array grants go through it
    so the grant raises the suppression baseline (and is therefore never mistaken
    for a vanilla pickup to revert). Without it, the legacy additive grant is
    used — convenient for tests and for running the loop without replacement.
    """
    name = item_name.strip()

    # Confirmed item/skill array entries (key items, granted skills, consumables)
    # — granted by writing the array directly. Checked first so mapped items win.
    if name in ITEM_OFFSETS:
        if suppressor is not None and suppressor.enabled:
            suppressor.grant(memory, name)
        else:
            _grant_item(memory, name)
        return True

    # Simple unlock flags.
    if name == "Double Jump":
        _set_flag(memory, "double_jump", offsets.double_jump)
        return True
    if name == "Dash":
        _set_flag(memory, "dash", offsets.dash)
        return True
    if name in ("Boost", "EX Mode", "Boost/EX Mode"):
        _set_flag(memory, "boost_ex_mode", offsets.boost_ex_mode)
        return True

    # Spells. Accept "Wind Spell", "Wind Spell (Charged)", or explicit tier.
    spell_map = {
        "wind": ("wind_spell", offsets.wind_spell),
        "thunder": ("thunder_spell", offsets.thunder_spell),
        "fire": ("fire_spell", offsets.fire_spell),
    }
    lname = name.lower()
    for key, (field_name, off) in spell_map.items():
        if lname.startswith(key) and "spell" in lname:
            tier = 2 if ("charged" in lname or "tier 2" in lname) else 1
            _set_tier(memory, field_name, off, tier)
            return True

    # Gems (counters, capped at 3).
    gem_map = {
        "emerald": ("emerald_count", offsets.emerald_count),
        "topaz": ("topaz_count", offsets.topaz_count),
        "ruby": ("ruby_count", offsets.ruby_count),
    }
    for key, (field_name, off) in gem_map.items():
        if lname.startswith(key):
            _increment(memory, field_name, off, cap=3)
            return True

    # Weapon upgrade (capped at 10).
    if lname.startswith("weapon"):
        _increment(memory, "weapon_level", offsets.weapon_level, cap=10)
        return True

    # Filler items have no static-offset effect (HP/MP are dynamic; granting
    # them safely needs a pointer chain we deliberately avoid). No-op for now.
    if name in ("HP Up", "MP Up", "Roda Fruit", "Nothing"):
        log.info("filler item %r — no memory write", name)
        return False

    log.warning("apply_item: no mapping for item %r", name)
    return False
