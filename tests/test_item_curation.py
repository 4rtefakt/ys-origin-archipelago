"""Offline tests for the item-pool curation: hex/gold removal, SP fillers, and
progressive armor/boots.

Run directly::

    python -m tests.test_item_curation

Loads ``ys_origin.data_tables`` in isolation (the package ``__init__`` needs
Archipelago's ``BaseClasses``, absent offline) and checks:

  * the raw-hex placeholder items (0x80/0x81/0x82) and the dead gold items
    (no money exists in Ys Origin) are gone from the item universe;
  * the filler pool is Panacea + SP grants, and every SP filler has an amount;
  * progressive gear: ladders resolve to g_flags indices for every character,
    and ``vanilla_items(progressive_gear=True)`` seeds Progressive Armor/Boots
    in place of every raw gear piece (and only then).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ys_origin_data_tables_cur", _ROOT / "ys_origin" / "data_tables.py"
)
dt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dt)

ALL_CHARS = ("yunica", "hugo", "toal")


def test_artifacts_grant_their_skill():
    """Each sacred artifact must map to the power cell that lets you cast it.

    The bracelets (0x74/0x75/0x76) are the power the game actually checks, and
    they are deliberately NOT separate pickups (the vanilla chest sets both cells,
    so two checks would fire from one chest). That makes this mapping the only
    thing lighting up a skill — without it you own an uncastable artifact.
    """
    grants = dt.skill_grants()
    assert grants, "artifacts must publish their skill grants"
    # every artifact resolves to its bracelet's g_flags index
    for art, skill in dt.SKILL_GRANTS.items():
        assert art in grants, f"{art} grants no skill"
        assert grants[art] == dt.item_index[skill], f"{art} -> wrong cell"
    # the three powers are the known bracelet cells, and are distinct
    assert sorted(grants.values()) == [0x74, 0x75, 0x76], sorted(grants.values())
    # the artifacts themselves stay real items; the bracelets stay out of the pool
    for art, skill in dt.SKILL_GRANTS.items():
        assert art in dt.item_name_to_id, f"{art} must remain a real item"
        assert skill not in dt.item_name_to_id, (
            f"{skill} must NOT be its own item (double-fires the artifact's chest)"
        )


def test_no_hex_placeholder_items():
    bad = [n for n in dt.item_name_to_id if re.fullmatch(r"0x[0-9A-Fa-f]+", n)]
    assert not bad, f"raw-hex placeholder items remain: {bad}"


def test_no_gold_items():
    gold = [n for n in dt.item_name_to_id if re.fullmatch(r"\d+G", n)]
    assert not gold, f"dead gold items remain: {gold}"
    assert not any(re.fullmatch(r"\d+G", n) for n in dt.FILLER_POOL)


def test_filler_pool_is_panacea_and_sp():
    assert "Celcetan Panacea" in dt.FILLER_POOL
    sp = [n for n in dt.FILLER_POOL if n.startswith("SP:")]
    assert sp, "expected SP fillers in the pool"
    for n in sp:
        assert n in dt.SP_FILLER and dt.SP_FILLER[n] > 0, n
    # every filler is either Panacea or a mapped SP grant
    assert set(dt.FILLER_POOL) <= {"Celcetan Panacea"} | set(dt.SP_FILLER)
    assert dt.SP_FLAG_IDX == 0xD8


def test_sp_and_progressive_in_universe_as_right_class():
    for n in dt.SP_FILLER:
        assert n in dt.item_name_to_id
        assert dt.item_classification(n) == "filler", n
    for n in (dt.PROGRESSIVE_ARMOR, dt.PROGRESSIVE_BOOTS):
        assert n in dt.item_name_to_id
        assert dt.item_classification(n) == "useful", n


def test_gear_ladders_resolve_for_all_characters():
    for char in ALL_CHARS:
        sd = dt.progressive_gear_slot_data(char)
        armor = sd[dt.PROGRESSIVE_ARMOR]
        boots = sd[dt.PROGRESSIVE_BOOTS]
        assert len(armor) == 4, (char, armor)
        assert len(boots) == 5, (char, boots)
        for idx in armor + boots:
            assert isinstance(idx, int) and 0 <= idx < 0x200, (char, idx)
        # tiers must be distinct cells
        assert len(set(armor)) == 4 and len(set(boots)) == 5


def test_vanilla_items_progressive_substitution():
    enabled = {"chest", "event"}
    for char in ALL_CHARS:
        raw = dt.vanilla_items(enabled, char, progressive_gear=False)
        prog = dt.vanilla_items(enabled, char, progressive_gear=True)
        assert len(raw) == len(prog)
        ladder = set(dt.GEAR_LADDERS[char][dt.PROGRESSIVE_ARMOR]) \
            | set(dt.GEAR_LADDERS[char][dt.PROGRESSIVE_BOOTS])
        # every raw gear piece became a progressive item, count preserved
        n_gear = sum(1 for n in raw if n in ladder)
        n_prog = sum(1 for n in prog
                     if n in (dt.PROGRESSIVE_ARMOR, dt.PROGRESSIVE_BOOTS))
        assert n_gear == n_prog > 0, (char, n_gear, n_prog)
        assert not any(n in ladder for n in prog), char
        # armor chests -> 4 armor + 5 boots (each character has one per tier)
        assert prog.count(dt.PROGRESSIVE_ARMOR) == 4, char
        assert prog.count(dt.PROGRESSIVE_BOOTS) == 5, char
        # non-gear items untouched
        assert [n for n in raw if n not in ladder] == \
               [n for n in prog
                if n not in (dt.PROGRESSIVE_ARMOR, dt.PROGRESSIVE_BOOTS)]


def test_class_overrides_valid_entries_kept():
    real = next(iter(dt.item_name_to_id))
    out = dt.parse_class_overrides({real: "useful", dt.GOAL_ITEM: "filler"})
    # a real item -> valid tier is kept, canonicalised to lowercase
    assert out[real] == "useful"
    # DOWNGRADING the goal item is allowed on purpose (player's call / skips)
    assert out[dt.GOAL_ITEM] == "filler"


def test_class_overrides_case_and_whitespace_insensitive():
    real = next(iter(dt.item_name_to_id))
    out = dt.parse_class_overrides({real: "  PROGRESSION  "})
    assert out[real] == "progression"


def test_class_overrides_drop_unknown_and_invalid():
    warnings = []
    raw = {
        "Definitely Not An Item": "useful",   # unknown name -> dropped
        dt.GOAL_ITEM: "legendary",            # invalid tier -> dropped
    }
    out = dt.parse_class_overrides(raw, warn=warnings.append)
    assert out == {}, out
    assert len(warnings) == 2, warnings  # both reported, neither fatal


def test_class_overrides_empty_and_none():
    assert dt.parse_class_overrides(None) == {}
    assert dt.parse_class_overrides({}) == {}
    # every accepted tier is one AP knows how to map
    assert set(dt.VALID_TIERS) == {"filler", "useful", "progression", "trap"}


def test_cleaned_chests_seed_filler():
    # the chests whose only content was a hex placeholder / gold now have no
    # vanilla item -> the pool pads them with filler instead.
    for loc in ("Wailing Blue: 4F Forward Passage 3",
                "Flames of Guilt: Lava Rods",
                "Corrupted Blood: Toal's Room"):
        assert dt.location_vanilla_item(loc) == "", loc


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
