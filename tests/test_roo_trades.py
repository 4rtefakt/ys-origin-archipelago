"""Offline guards for the Roo trades (issue #9).

The Roos are the sacred animals that take one Roda Fruit and give something
back. They were missing from ``locations.json`` entirely — the chest catalog
types scripts by filename and the trade scripts are ``AGERU.XSO`` /
``TALKRUU_*.XSO``, neither of which matches the ``S_BOX*`` / ``EVT_*`` / ``TALK*``
patterns it looks for — so the trade ran vanilla and no check ever fired
("The Roo checks are not randomized").

Loads ``ys_origin.data_tables`` in isolation (the package ``__init__`` needs
Archipelago's ``BaseClasses``, absent offline) and checks the properties that
would break a seed if they drifted:

  * all six trades exist, with LIVE (flag) detection and no offset collision;
  * their detect offsets are the flags the scripts actually set;
  * the fruit supply covers the demand exactly (six fruits, six Roos), which is
    what makes the count-based access rule satisfiable.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ys_origin_data_tables_roo", _ROOT / "ys_origin" / "data_tables.py"
)
dt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dt)

_LOCS = json.loads((_ROOT / "ys_origin" / "data" / "locations.json")
                   .read_text(encoding="utf-8"))

# g_flags is at 0x76B91C absolute; locations.json stores module-relative offsets
# (image base 0x400000, no ASLR).
_GFLAGS_REL = 0x36B91C

# scene -> the flag its AGERU/TALKRUU script sets when the fruit is handed over,
# read out of the extracted XSO corpus.
_ROO_FLAGS = {
    "S_1014": 0x138,
    "S_2011": 0x153,
    "S_3104": 0x16A,
    "S_4004": 0x184,
    "S_5100": 0x19E,
    "S_6009": 0x1CA,
}


def _by_name(name):
    for l in _LOCS:
        if l["name"] == name:
            return l
    raise AssertionError(f"location missing: {name}")


def test_all_six_roo_trades_exist():
    assert len(dt.ROO_LOCATIONS) == 6, "there are six Roos in the tower"
    assert len(set(dt.ROO_LOCATIONS)) == 6, "duplicate Roo location name"
    for name in dt.ROO_LOCATIONS:
        _by_name(name)


def test_roo_detection_is_live_and_matches_the_scripts():
    """Detection must be flag-method, and the flag must be the one the trade
    script actually sets — a scene-method Roo would be excluded from holding
    progression, and a wrong offset would simply never fire."""
    seen = set()
    for name in dt.ROO_LOCATIONS:
        loc = _by_name(name)
        det = loc["detect"]
        assert det["method"] == "flag", f"{name}: detection must be live"
        scene = loc["id"].split("/")[0]
        want = _GFLAGS_REL + _ROO_FLAGS[scene] * 4
        assert int(det["offset"], 16) == want, (
            f"{name}: detect {det['offset']} != flag 0x{_ROO_FLAGS[scene]:X} "
            f"(expected 0x{want:X})")
        assert det["offset"] not in seen, f"{name}: duplicate detect offset"
        seen.add(det["offset"])


def test_roo_detect_offsets_do_not_collide_with_other_locations():
    others = [l for l in _LOCS if l["name"] not in set(dt.ROO_LOCATIONS)]
    taken = {l["detect"].get("offset") for l in others
             if l["detect"].get("method") == "flag"}
    for name in dt.ROO_LOCATIONS:
        off = _by_name(name)["detect"]["offset"]
        assert off not in taken, f"{name}: detect offset {off} already in use"


def test_roo_trades_are_not_excluded():
    """They must be able to hold progression — otherwise adding them is pointless
    and the Roda Fruit promotion in create_item would be dead weight."""
    for name in dt.ROO_LOCATIONS:
        assert not dt.is_excluded(name), f"{name} must be able to hold progression"


def test_fruit_supply_matches_roo_demand():
    """Six Roos each consume one fruit, and vanilla stocks exactly six fruits.

    The access rule is by COUNT (the k-th Roo needs k fruits), so if the supply
    ever dropped below six the last Roo would be unreachable and generation
    could fail. Guards both directions.
    """
    fruits = sum(1 for l in _LOCS for i in l.get("items", [])
                 if i["name"] == dt.RODA_FRUIT)
    assert fruits == len(dt.ROO_LOCATIONS) == 6, (
        f"{fruits} Roda Fruit in the pool for {len(dt.ROO_LOCATIONS)} Roos — the "
        "count-based Roo rule needs one fruit per trade")


def test_roo_vanilla_items_are_real_or_absent():
    """Five Roos hand out a conversation Topic (0x93 SetTopicKnownIndex), which
    is not a poolable item, so they carry no vanilla item and create_items pads
    the slot with filler. The S_3104 trade is the only one granting a real item
    (Hammer 0x60 on the generic/Hugo path)."""
    with_items = {}
    for name in dt.ROO_LOCATIONS:
        items = _by_name(name).get("items", [])
        assert len(items) <= 1, f"{name}: unexpected multi-item Roo"
        if items:
            with_items[name] = items[0]["name"]
    assert list(with_items.values()) == ["Hammer"], with_items
    # and it must be a real pool item, or the pool would be short one slot
    assert "Hammer" in dt.item_index


def _run_all():
    for k, v in sorted(globals().items()):
        if k.startswith("test_") and callable(v):
            v()
            print(f"ok  {k}")


if __name__ == "__main__":
    _run_all()
