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
    # the three elemental powers are the known bracelet cells, plus the two
    # MOBILITY pairings (Gold -> 0xA3 double-jump, Silver -> 0xB5 dash), whose
    # companion cell is a bare g_flags index rather than an item of its own,
    # and the Evil Ring's drained twin (the one Rado's Annex door wants held).
    assert sorted(grants.values()) == [0x5E, 0x74, 0x75, 0x76, 0xA3, 0xB5,
                                       0xB6, 0xB7, 0xB8], sorted(grants.values())
    assert grants["Gold Bracelet"] == 0xA3
    assert grants["Silver Bracelet"] == 0xB5
    assert grants["Evil Ring"] == 0x5E
    # all distinct: a shared cell would make one item silently light up another
    assert len(set(grants.values())) == len(grants)
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
        # each character's ladders span their whole 6-slot gear band minus the
        # piece they start wearing: 5 armor tiers, 6 boots tiers.
        assert len(armor) == 5, (char, armor)
        assert len(boots) == 6, (char, boots)
        for idx in armor + boots:
            assert isinstance(idx, int) and 0 <= idx < 0x200, (char, idx)
        # tiers must be distinct cells
        assert len(set(armor)) == 5 and len(set(boots)) == 6


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
        # 4 armor + 5 boots come from chests, and one of each from a Roo trade
        # (S_5100 armor, S_4004 boots) — those pieces are real ladder tiers, so
        # they substitute like any other rather than being handed over raw.
        assert prog.count(dt.PROGRESSIVE_ARMOR) == 5, char
        assert prog.count(dt.PROGRESSIVE_BOOTS) == 6, char
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
    # the chests whose only content was dead gold now have no vanilla item ->
    # the pool pads them with filler instead.
    #
    # NOTE: two chests were removed from this list. 0x80/0x81/0x82 looked like
    # hex placeholders because INVINFO has no name for them, but they are the
    # real elemental upgrade gems (Emerald/Ruby/Topaz) and cleaning them left
    # eight chests with no vanilla item to suppress — so the chest handed out
    # the skill upgrade on top of the AP item. Seen live on the 4F Emerald.
    for loc in ("Corrupted Blood: Toal's Room",):
        assert dt.location_vanilla_item(loc) == "", loc
    for loc, gem in (("Wailing Blue: 4F Forward Passage 3", "Emerald"),
                     ("Flames of Guilt: Lava Rods", "Ruby")):
        assert dt.location_vanilla_item(loc) == gem, loc


def test_gems_raise_their_own_element():
    """A gem's level cell is the one its progressive chain names: the Ruby/Topaz
    rename swapped the names everywhere but ABILITY_GRANTS, which went unseen
    because progressive skills keep the bare gems out of the pool."""
    for prog, d in dt.PROGRESSIVE_SKILLS.items():
        assert dt.ABILITY_GRANTS[d["gem"]] == d["level_cell"], prog
    assert dt.GEM_GIVE_IDS == {"Emerald": 0x80, "Topaz": 0x81, "Ruby": 0x82}


def test_suppressed_items_include_the_skill_power_cells():
    """The elemental power cells must be suppressed, not just the artifacts.

    The altar scripts set the artifact cell AND its bracelet cell in one go, but
    only the artifact is a location's vanilla item. When the bracelet cells were
    absent from suppress_items the mod let those stores through and the player
    kept the vanilla element on top of whatever AP had placed there — the
    "randomization of the elements is not working" playtest report.
    """
    all_locs = [l["name"] for l in dt._LOCS]
    powers = set(dt.skill_grants().values())
    assert powers, "artifacts must publish their skill grants"
    for char in ALL_CHARS:
        supp = set(dt.suppress_item_indices(all_locs, char))
        missing = powers - supp
        assert not missing, (
            f"{char}: power cells not suppressed: "
            f"{sorted(hex(m) for m in missing)}")
        # and the artifacts themselves are still there — except the gems, whose
        # pool id is SYNTHETIC on purpose: their real ids (0x80-0x82) are
        # give-item ids, not g_flags cells, and 0x82 is the boss-battle flag 130.
        # Putting that in the g_flags suppress set would have blocked every real
        # boss battle from setting it.
        for art in dt.skill_grants():
            idx = dt.item_index[art]
            if idx >= 0x200:
                assert art in dt.GEM_GIVE_IDS, art
                assert idx not in supp, (char, art, "synthetic id must not be suppressed")
                continue
            assert idx in supp, (char, art)
        # The give-item path carries the ids that must NOT reach the g_flags
        # suppress set: the gems' real ids, plus any degraded VARIANT a pickup
        # writes instead of the cell our item table names (the drained Evil
        # Ring, 0x5E). Both are 0x116 operands rather than g_flags cells.
        gives = set(dt.suppress_give_ids(all_locs, char))
        expected = set(dt.GEM_GIVE_IDS.values()) | set(dt.VARIANT_GIVE_IDS.values())
        assert gives == expected, (char, gives, expected)


def test_suppressed_items_track_active_locations():
    # an empty world suppresses nothing (no vanilla grant to neutralize)
    assert dt.suppress_item_indices([], "hugo") == []


def test_pool_holds_only_items_the_character_can_use():
    """A character's pool never carries another character's item.

    Such an item is inert (the Hammer in Yunica's pool — she cures the S_3103
    noise with the Harmonica; Discord, Aug 2026), and a gate item lands in a pool
    whose logic never asks for it (Toal's Blue Necklace: S_EVT1013 lets him pass
    while boosting, never by the necklace). Those slots must pad with filler."""
    enabled = set(dt.CATEGORIES)
    for char in ALL_CHARS:
        bad = sorted({n for n in dt.vanilla_items(enabled, char)
                      if not dt.item_allowed(n, char)})
        assert not bad, (char, bad)


def test_suppression_still_covers_items_written_to_other_characters():
    """The pool drops another character's item, but suppression must not: the
    S_4003 chest writes the drained Evil Ring (0x5E) for every character, Toal
    included, so it stays in Toal's give-item suppress set."""
    all_locs = [l["name"] for l in dt._LOCS]
    assert 0x5E in dt.suppress_give_ids(all_locs, "toal")


def test_the_usable_dreaming_idol_is_never_suppressed():
    """0x69 (the UNCHARGED idol Dino gives) is the one Yunica can Use: her item
    page and Use list carry 0x69, not 0x68 (CleriaCore inventory.cpp,
    pausebook.cpp), and S_COMMON/USESEKIZOU needs it. Sinking Dino's write left
    no usable idol in the game at all, so the petrified party could never be
    cured. 0x68 is the pool's (charged) idol and stays suppressed."""
    all_locs = [l["name"] for l in dt._LOCS]
    supp = set(dt.suppress_item_indices(all_locs, "yunica"))
    assert 0x69 not in supp
    assert 0x68 in supp


def test_no_location_detects_on_a_skill_level_cell():
    """g_flags[0xB6..0xB8] are the element skill LEVELS. Detecting a check on one
    (the Fire Altar read 0xB8) exempts that cell from suppression in the mod, so
    the vanilla fire-gem chests' +1 stacked on the AP levels to 4 — and level 4
    reads past MP regen's table (Aug 2026, "MP at -650%"). The altars have their
    own done flags: wind 304, thunder 335, fire 350."""
    level_cells = {hex(0x36B91C + 4 * i) for i in (0xB6, 0xB7, 0xB8)}
    bad = [l["name"] for l in dt._LOCS
           if l["detect"].get("offset", "").lower() in level_cells]
    assert not bad, bad


def test_pool_follows_the_character_location_filter():
    """A location that does not exist for a character seeds no item for them
    (the Toal-only S_2005 box_02 Cleria Ore; the per-character blessing rows),
    so the pool never outgrows the locations; and the Cleria Ore that weapon
    requirements count still covers the deepest zone's need."""
    enabled = set(dt.CATEGORIES)
    need = max(dt.zone_ore_requirements(True).values())
    for char in ALL_CHARS:
        locs = sum(len(v) for v in dt.locations_by_region(enabled, char).values())
        items = dt.vanilla_items(enabled, char, blessing_items=True)
        assert len(items) <= locs, (char, len(items), locs)
        assert items.count("Cleria Ore") >= need, (char, items.count("Cleria Ore"), need)


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


def test_suppress_set_never_contains_a_story_flag():
    """g_flags suppression must stay inside cells that are actually items.

    A gem's real id 0x82 is flag 130 — the boss-battle state every BATTLE*.XSO
    sets on entry. It reached the suppress set once, which both put the game into
    a boss fight when the item was granted and would have stopped real bosses
    from setting the flag at all. Anything outside the inventory band plus the
    known companion ability cells is a story flag and must never be suppressed.
    """
    # The inventory band runs 0x00..0x76 (gear from 0x06, consumables/keys from
    # 0x40). Everything above that is story/progress state — 0x82 is flag 130,
    # the boss-battle marker — except the companion ability cells we grant
    # deliberately.
    ALLOWED_EXTRA = {0xA3, 0xB5, 0xB6, 0xB7, 0xB8}   # bracelet + skill-level cells
    all_locs = [l["name"] for l in dt._LOCS]
    for char in ALL_CHARS:
        for idx in dt.suppress_item_indices(all_locs, char):
            ok = (0x00 <= idx <= 0x76) or idx in ALLOWED_EXTRA
            assert ok, (char, hex(idx), "not an item cell — would suppress a story flag")
