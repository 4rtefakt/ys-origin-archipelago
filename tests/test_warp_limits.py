"""Offline tests for the random-start floor cap and warp-skip limit (no AP tree).

Run directly::

    python -m tests.test_warp_limits

``ys_origin.data_tables`` is pure data (json + re) but the package ``__init__``
pulls in Archipelago's ``BaseClasses``, so we load the module in isolation via
importlib — exactly how the generator's data layer runs, minus the AP import.

Covers:

  * ``start_statue_candidates`` caps the Random-start spawn to floor <= max and
    never returns empty (falls back to the lowest statue);
  * ``warp_skip_anchor`` yields an anchor exactly when the destination is more
    than N floors above the base, and nothing when the limit is unlimited/near;
  * ``warp_edge_rules`` keeps the spawn free, gates far statues behind an anchor,
    and leaves near/unlimited statues anchor-free.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ys_origin_data_tables", _ROOT / "ys_origin" / "data_tables.py"
)
dt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dt)


def _floors(scenes):
    return sorted({dt.scene_floor(s) for s in scenes})


def test_start_cap_limits_floor():
    # a low cap keeps you in the opening zone; every candidate is at/under it.
    for cap in (1, 5, 10, 25):
        cands = dt.start_statue_candidates(cap)
        assert cands, f"cap={cap} produced no spawn candidates"
        assert all((dt.scene_floor(s) or 1) <= cap for s in cands), cap


def test_start_cap_excludes_deep_statues():
    # a 25F spawn must be impossible under a gentle cap, possible when raised.
    deep = [s for s in dt.statue_scenes() if (dt.scene_floor(s) or 0) >= 22]
    assert deep, "expected some deep (>=22F) statues in the data"
    assert not (set(deep) & set(dt.start_statue_candidates(10)))
    assert set(deep) & set(dt.start_statue_candidates(25))


def test_start_cap_never_empty_below_lowest():
    # a cap under the lowest statue floor still yields the single lowest statue.
    lowest_floor = min(dt.scene_floor(s) or 1 for s in dt.statue_scenes())
    cands = dt.start_statue_candidates(max(1, lowest_floor - 1) if lowest_floor > 1 else 1)
    assert len(cands) >= 1


def test_skip_anchor_binds_only_far_targets():
    # within N floors of the base -> no anchor; further up -> an anchor region.
    assert dt.warp_skip_anchor(1, 5) is None
    assert dt.warp_skip_anchor(6, 5) is None            # 6-5 = 1, base-reachable
    assert dt.warp_skip_anchor(25, 5) is not None
    # the anchor sits below the destination, within the skip window.
    for target in (10, 18, 22, 25):
        anchor = dt.warp_skip_anchor(target, 5)
        assert anchor is not None
        leaf = int(re.match(r"S_(\d+)", anchor).group(1))
        af = dt.scene_floor(leaf)
        assert target - 5 <= af < target, (target, af)


def test_skip_anchor_unlimited_is_noop():
    for target in (10, 18, 25):
        assert dt.warp_skip_anchor(target, 0) is None


def test_warp_edge_rules_shape_and_gating():
    rules = dt.warp_edge_rules(1000, True, 1, 5)   # spawn 1F, locks, weapon, skip 5
    assert rules, "expected warp edges"
    spawn_free = anchored_far = unanchored_near = 0
    for (src, dst), val in rules.items():
        assert len(val) == 3, "warp_edge_rules must return (unlock, ore, anchor)"
        unlock, ore, anchor = val
        floor = dt.scene_floor(int(re.match(r"S_(\d+)", dst).group(1)))
        if unlock is None and ore == 0 and anchor is None:
            spawn_free += 1                          # the 1F spawn edge
        elif floor and floor - 5 > 1:
            assert anchor is not None, f"far floor {floor} should be anchored"
            anchored_far += 1
        elif floor and floor <= 6:
            assert anchor is None, f"near floor {floor} should be anchor-free"
            unanchored_near += 1
    assert spawn_free == 1 and anchored_far and unanchored_near


def test_warp_edge_rules_unlimited_drops_anchors():
    rules = dt.warp_edge_rules(1000, True, 1, 0)   # skip 0 = unlimited
    assert all(anchor is None for (_u, _o, anchor) in rules.values())


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
