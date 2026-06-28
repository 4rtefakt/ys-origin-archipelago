"""Offline tests for vanilla item-grant suppression (no game / AP needed).

Run directly::

    python -m tests.test_suppression

Exercises the baseline model in :mod:`client.suppression` against a fake memory
backend, covering the four behaviours that matter for content replacement:

  * a vanilla key-item pickup is reverted to "never obtained";
  * the skill a key item grants is left cosmetic (never reverted — cast-freeze);
  * an AP grant raises the baseline and is never mistaken for a vanilla grant;
  * consumables: vanilla pickups reverted, AP grants kept, use lowers the floor.
"""

from __future__ import annotations

from client.game_state import GameState, apply_item
from client.offsets import ITEM_OFFSETS as _ITEM_OFFSETS
from client.suppression import NEVER, Suppressor


class FakeMemory:
    """Minimal stand-in for ProcessMemory keyed by module-relative offset."""

    def __init__(self, values: dict[int, int] | None = None):
        self.cells: dict[int, int] = dict(values or {})

    def read_offset_int32(self, offset: int) -> int:
        return self.cells.get(offset, NEVER)

    def write_offset_int32(self, offset: int, value: int) -> None:
        self.cells[offset] = value


# Offsets of the entries we touch, by name (mirrors ITEM_OFFSETS).
FLAB = _ITEM_OFFSETS["Cerulean Flabellum"]
BUBBLE = _ITEM_OFFSETS["Ventus Bracelet"]  # wind power (the "bubble" ability)
PANACEA = _ITEM_OFFSETS["Celcetan Panacea"]


def _state(**items: int) -> GameState:
    """A valid snapshot whose item slots default to NEVER (-1)."""
    base = {name: NEVER for name in _ITEM_OFFSETS}
    base.update(items)
    return GameState(valid=True, items=base)


def _mem_from(state: GameState) -> FakeMemory:
    return FakeMemory({_ITEM_OFFSETS[n]: v for n, v in state.items.items()})


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_vanilla_key_item_reverted_skill_kept():
    """Opening the vanilla Flabellum chest: item reverted, skill left cosmetic."""
    sup = Suppressor(enabled=True)
    sup.prime(_state())  # nothing obtained yet -> all baselines NEVER

    # Player opens the vanilla chest: both the key item and its skill get set.
    state = _state(**{"Cerulean Flabellum": 1, "Ventus Bracelet": 1})
    mem = _mem_from(state)

    reverts = sup.suppress(mem, state)

    names = {r[0] for r in reverts}
    assert names == {"Cerulean Flabellum"}, names
    assert mem.cells[FLAB] == NEVER, "key item must be reverted to -1"
    assert mem.cells[BUBBLE] == 1, "skill slot must be left untouched (cosmetic)"
    # state mutated to post-suppression reality
    assert state.items["Cerulean Flabellum"] == NEVER
    assert state.items["Ventus Bracelet"] == 1


def test_ap_grant_not_reverted():
    """An AP grant raises the baseline; the next poll must not revert it."""
    sup = Suppressor(enabled=True)
    sup.prime(_state())

    mem = _mem_from(_state())
    new_val = sup.grant(mem, "Cerulean Flabellum")
    assert new_val == 1
    assert mem.cells[FLAB] == 1

    # Next poll sees the granted value; suppression must leave it alone.
    state = _state(**{"Cerulean Flabellum": 1})
    reverts = sup.suppress(mem, state)
    assert reverts == []
    assert mem.cells[FLAB] == 1


def test_consumable_vanilla_reverted_ap_kept_then_used():
    """Consumable lifecycle: vanilla reverted, AP kept, consumption lowers floor."""
    sup = Suppressor(enabled=True)
    sup.prime(_state(**{"Celcetan Panacea": 1}))  # start with one legit panacea

    # AP grants one panacea -> baseline 1 -> 2, memory written to 2.
    mem = _mem_from(_state(**{"Celcetan Panacea": 1}))
    sup.grant(mem, "Celcetan Panacea")
    assert mem.cells[PANACEA] == 2
    assert sup.baseline["Celcetan Panacea"] == 2

    # Player opens a vanilla chest containing a panacea -> count climbs to 3.
    state = _state(**{"Celcetan Panacea": 3})
    reverts = sup.suppress(mem, state)
    assert ("Celcetan Panacea", 3, 2) in reverts
    assert mem.cells[PANACEA] == 2, "vanilla +1 suppressed back to AP floor"

    # Player drinks a panacea: the game itself writes the lower count to memory,
    # which the next poll reads back. The suppressor must only follow the floor
    # down, never write the item back.
    mem.cells[PANACEA] = 1
    state = _state(**{"Celcetan Panacea": 1})
    reverts = sup.suppress(mem, state)
    assert reverts == []
    assert sup.baseline["Celcetan Panacea"] == 1, "consumption lowers the floor"
    assert mem.cells[PANACEA] == 1, "used item is not given back"


def test_grant_overwrites_vanilla_contamination():
    """If a vanilla grant lands before suppression, the AP grant still nets right.

    Baseline panacea = 1. A vanilla pickup makes the live cell 2 *before* the AP
    grant runs. grant() computes from the baseline (1 -> 2) and writes 2, so the
    net is exactly one AP grant on top of the legit floor — the vanilla +1 is
    not double-counted.
    """
    sup = Suppressor(enabled=True)
    sup.prime(_state(**{"Celcetan Panacea": 1}))

    mem = _mem_from(_state(**{"Celcetan Panacea": 2}))  # vanilla already there
    sup.grant(mem, "Celcetan Panacea")
    assert mem.cells[PANACEA] == 2
    assert sup.baseline["Celcetan Panacea"] == 2

    # The leftover live==baseline, so nothing to revert.
    state = _state(**{"Celcetan Panacea": 2})
    assert sup.suppress(mem, state) == []


def test_disabled_is_noop():
    sup = Suppressor(enabled=False)
    sup.prime(_state())
    state = _state(**{"Cerulean Flabellum": 1})
    mem = _mem_from(state)
    assert sup.suppress(mem, state) == []
    assert mem.cells[FLAB] == 1  # untouched


def test_apply_item_routes_through_suppressor():
    """apply_item with a suppressor uses baseline grant, not the additive path."""
    sup = Suppressor(enabled=True)
    sup.prime(_state(**{"Celcetan Panacea": 1}))
    mem = _mem_from(_state(**{"Celcetan Panacea": 1}))

    ok = apply_item(mem, "Celcetan Panacea", suppressor=sup)
    assert ok
    assert mem.cells[PANACEA] == 2
    assert sup.baseline["Celcetan Panacea"] == 2


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
