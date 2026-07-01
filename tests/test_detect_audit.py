"""Offline audit of location detect signatures (duplicate/collision hygiene).

Run directly::

    python -m tests.test_detect_audit

Guards the two bug classes found in playtesting:

  * TRUE DUPLICATES — several locations with the same detect signature AND the
    same item (one physical pickup captured once per character/variant script).
    Each one can only ever fire a single check; the rest are dead locations.
    (Red Moon Crest x3, the Fire Altar TALKSAUL_THOR pair, Water Dragon #3.)
  * SHARED-SIGNAL PAIRS — one event flag legitimately mapping to TWO locations
    with DIFFERENT items (the elemental altars grant weapon + bracelet on one
    script flag). These are allowed — the clients fire every location mapped to
    a flag — but are asserted here explicitly so new ones are conscious choices.

Also pins the blessing detect layout (the "aren't blessings broken?" audit):
23 bitfield entries with distinct bits + the armor cell as a value flag.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
LOCS = json.loads((_ROOT / "ys_origin" / "data" / "locations.json").read_text("utf-8"))

# (offset, frozenset(items)) pairs that intentionally share one detect flag:
# a single altar interaction grants two items -> two checks fire together.
ALLOWED_SHARED = {
    "0x36BDDC",  # Wind altar: Cerulean Flabellum + Ventus Bracelet
    "0x36BE58",  # Thunder skill room: Levinstrike Warhammer + Terra Bracelet
    "0x36BBFC",  # Fire altar: Crimson Lotusblade + Ignis Bracelet
}


def _sig(l: dict):
    d = l["detect"]
    return (d["method"], d.get("offset"), d.get("bit"), d.get("floor"),
            d.get("scene"))


def test_no_true_duplicate_locations():
    # scene-method sharing across TYPES is fine (a boss arena is both a "Boss:"
    # and an "Explore:" check; scene detection fires every location mapped to a
    # scene). A duplicate within the same type/signature/item is a dead location.
    seen = defaultdict(list)
    for l in LOCS:
        items = frozenset(i["name"] for i in l.get("items", []))
        key = (_sig(l), items)
        if l["detect"]["method"] == "scene":
            key = (_sig(l), items, l["type"])
        seen[key].append(l["name"])
    dupes = {k: v for k, v in seen.items() if len(v) > 1 and k[0][0] != "floor"}
    assert not dupes, f"locations duplicating the same signal+item: {dupes}"


def test_shared_flags_are_known_multi_item_events():
    by_flag = defaultdict(list)
    for l in LOCS:
        d = l["detect"]
        if d["method"] == "flag":
            by_flag[d["offset"]].append(l)
    for off, ls in by_flag.items():
        if len(ls) == 1:
            continue
        items = [frozenset(i["name"] for i in l.get("items", [])) for l in ls]
        assert off in ALLOWED_SHARED, (
            f"flag {off} shared by {[l['name'] for l in ls]} — either a true "
            "duplicate (delete it) or a new multi-item event (add to "
            "ALLOWED_SHARED after confirming both items really fire together)")
        assert len(set(items)) == len(items), (
            f"flag {off}: shared entries must carry different items")


def test_red_moon_crest_deduped():
    # exactly ONE pool copy (the chest); the companion event location remains a
    # check (box flag vs event flag = two real signals from one chest) but holds
    # no item, so it pads with filler.
    rmc = [l["name"] for l in LOCS
           if any(i["name"] == "Red Moon Crest" for i in l.get("items", []))]
    assert rmc == ["Flames of Guilt: Mark Treasure"], rmc
    names = {l["name"] for l in LOCS}
    assert "Flames of Guilt: Mark Treasure — Red Moon Crest" in names


def test_blessing_detect_layout():
    bless = [l for l in LOCS if l["type"] == "blessing"]
    bits = [l for l in bless if l["detect"]["method"] == "bit"]
    flags = [l for l in bless if l["detect"]["method"] == "flag"]
    assert len(bits) == 23 and len(flags) == 1, (len(bits), len(flags))
    # all bit entries watch the same bitfield cell, each a distinct bit
    assert {l["detect"]["offset"] for l in bits} == {"0x36BC80"}
    bitnums = [l["detect"]["bit"] for l in bits]
    assert len(set(bitnums)) == 23, "duplicate blessing bits"
    # the armor blessing lives OUTSIDE g_flags (0x36A684 < base 0x36B91C): the
    # in-game mod must poll it (the VM store hook can never see it).
    assert flags[0]["detect"]["offset"] == "0x36A684"


def test_detect_methods_are_known():
    # every method here must be implemented by BOTH clients (mod + python);
    # adding a new one means adding detection code, not just data.
    methods = {l["detect"]["method"] for l in LOCS}
    assert methods <= {"flag", "bit", "floor", "scene"}, methods


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
