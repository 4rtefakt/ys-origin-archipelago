"""Vanilla item-grant suppression ("content replacement").

The bare loop is *additive*: opening a vanilla chest grants its vanilla item
**and** the AP item placed at that location arrives over the network. For a true
randomizer the vanilla grant must be neutralized so the AP item *replaces* it.

We cannot intercept the in-game grant (no DLL/patch/inject — see the project
constraints), and there is no static per-chest contents table to zero out: a
chest grants its item via the event-script VM writing the item's index in the
unified ``g_flags[]`` array (opcode ``0x64``), with the id/value baked into
per-map heap bytecode. So suppression happens *after the fact*, at the array
level — the only level that lives at a fixed module offset we can read/write.

Model — a per-entry **baseline** = the value an item slot *should* have given
**only** AP grants (plus legitimate consumption). Every poll we compare the live
value to the baseline:

  * ``live > baseline`` — a vanilla grant slipped in. Rewrite the slot back to
    the baseline (revert). The player may briefly see the vanilla item (up to one
    poll, ~500 ms) before it vanishes.
  * ``live < baseline`` — the player legitimately *used* a consumable. Lower the
    baseline to match; never "give it back".
  * ``live == baseline`` — nothing to do.

AP grants go through :meth:`Suppressor.grant`, which raises the baseline **and**
writes that exact value to memory — so an AP grant is immune to any vanilla
contamination already sitting in the cell, and is never mistaken for one.

Safety: :data:`~client.offsets.SKILL_ITEMS` entries back a live runtime skill
object. Lowering one to ``-1`` is itself harmless, but the game freezes if it
later *casts* a skill whose entry is ``-1`` (dangling deref), and the equipped-
skill slot needed to safely unequip it is not yet mapped. So skill entries are
**never reverted** — they are absorbed into the baseline as cosmetic. (The key
*item* that grants a skill — e.g. the Cerulean Flabellum — is itself a normal
key item and *is* reverted; only the granted skill slot is left alone.)
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from .game_state import GameState
from .memory import ProcessMemory
from .offsets import GRANT_SAFE_MIN, ITEM_OFFSETS, SKILL_ITEMS

log = logging.getLogger("ys_origin.suppression")

# (item name, value before, value after) for one suppression action.
Revert = Tuple[str, int, int]

# Sentinel baseline for an entry the game has never granted ("never obtained").
NEVER = -1


class Suppressor:
    """Tracks the legitimate floor of each item slot and reverts vanilla grants.

    One instance lives on the AP context for the duration of an attachment.
    Re-prime (:meth:`reset` + :meth:`prime`) whenever the client re-attaches,
    since the game may have reloaded a save.
    """

    def __init__(self, *, enabled: bool = True):
        self.enabled = enabled
        # name -> legitimate floor value (-1 = never obtained).
        self.baseline: dict[str, int] = {}
        self.primed = False
        # Skill entries we have already noted as cosmetic, to log only once each.
        self._noted_cosmetic: set[str] = set()

    # -- lifecycle ---------------------------------------------------------- #

    def reset(self) -> None:
        """Forget all baselines (call on re-attach, before re-priming)."""
        self.baseline.clear()
        self.primed = False
        self._noted_cosmetic.clear()

    def prime(self, state: GameState) -> None:
        """Adopt ``state`` as the legitimate floor for every item slot.

        Everything currently in the save is treated as legitimate — it was
        either started with or previously AP-granted-and-saved. Called once per
        attachment, before any suppression runs, so the player's real inventory
        is never reverted.
        """
        for name in ITEM_OFFSETS:
            self.baseline[name] = state.items.get(name, NEVER)
        self.primed = True
        log.info("suppression primed: %s",
                 {k: v for k, v in self.baseline.items()})

    # -- AP grant ----------------------------------------------------------- #

    def grant(self, memory: ProcessMemory, name: str, count: int = 1) -> int:
        """Apply an AP grant by raising the baseline and writing it to memory.

        Returns the new value. Mirrors ``game_state._grant_item`` semantics
        (key items become 1, consumables add ``count``, never below the floor)
        but computes the target from the *baseline*, not the live cell — so the
        grant overwrites any un-suppressed vanilla value rather than stacking on
        top of it.
        """
        floor = self.baseline.get(name, NEVER)
        base = floor if floor >= 1 else 0          # treat -1 ("never") as 0
        target = max(base + count, GRANT_SAFE_MIN)
        memory.write_offset_int32(ITEM_OFFSETS[name], target)
        self.baseline[name] = target
        log.info("AP-granted %r: %d -> %d (baseline)", name, floor, target)
        return target

    # -- suppression -------------------------------------------------------- #

    def suppress(self, memory: ProcessMemory, state: GameState) -> List[Revert]:
        """Revert vanilla grants visible in ``state``; return what was reverted.

        Mutates ``state.items`` in place so the snapshot reflects the post-
        suppression reality (keeping the caller's ``prev_state`` consistent and
        avoiding a spurious "decrease" on the next diff).
        """
        if not self.enabled:
            return []
        if not self.primed:
            # Be defensive: never suppress before a baseline exists.
            self.prime(state)
            return []

        reverts: List[Revert] = []
        for name in ITEM_OFFSETS:
            live = state.items.get(name)
            if live is None:
                continue  # slot wasn't read this tick
            floor = self.baseline.get(name, NEVER)

            if live > floor:
                # A vanilla grant slipped in above the legitimate floor.
                if name in SKILL_ITEMS:
                    # Cannot safely lower a skill slot (cast-freeze). Absorb it
                    # as cosmetic so we stop trying to revert it.
                    self.baseline[name] = live
                    if name not in self._noted_cosmetic:
                        self._noted_cosmetic.add(name)
                        log.warning(
                            "skill %r vanilla-granted (%d); left as cosmetic "
                            "(revert unsafe until equipped-slot is mapped)",
                            name, live,
                        )
                    continue
                memory.write_offset_int32(ITEM_OFFSETS[name], floor)
                state.items[name] = floor
                reverts.append((name, live, floor))
                log.info("suppressed vanilla %r: %d -> %d", name, live, floor)
            elif live < floor:
                # Legitimate consumption (or external loss): lower the floor.
                self.baseline[name] = live

        return reverts
