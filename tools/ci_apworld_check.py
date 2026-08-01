"""Validate the built apworld against a real Archipelago checkout.

The offline tests in ``tests/`` never import Archipelago, which is what makes
them fast — but it also means they cannot see the whole class of bug that only
appears once AP itself handles the world. The Launcher outage in #20 was exactly
that: a plain ``visibility = 0`` that AP's ``visibility_level in
option.visibility`` could not iterate, which aborted template generation for
*every installed game* and produced no traceback anywhere the player could see.

This script closes that gap. Point it at an Archipelago checkout with the
apworld already installed in ``custom_worlds/``:

    python -m tools.ci_apworld_check <path-to-archipelago>

Checks, in order of how much they would have caught:

  1. **template generation** — the #20 regression. Renders every world's YAML
     template and asserts ours is among them.
  2. **AP's own world test suite** — the 90-odd generic tests AP runs against a
     world (ids, names, groups, item/location sanity, reachability, rules).
  3. **fill + beatability** across the option combinations that change the graph
     shape (open mode, the blessing shop, each character).
  4. **shop economy invariants** — no sphere-1 progression parked behind an
     expensive SP wall, the thing the playtest reported.

Exits non-zero on the first failure, with the real traceback.
"""

from __future__ import annotations

import os
import sys
import unittest
import warnings
from pathlib import Path

GAME = "Ys Origin"
# Kept alongside ours because a few of AP's generic tests reference the built-in
# "Archipelago" world (item links, datapackage checks).
KEEP_WORLDS = (GAME, "Archipelago")

AP_TEST_MODULES = [
    "test_names", "test_ids", "test_groups", "test_items", "test_locations",
    "test_reachability", "test_rule_builder", "test_entrances", "test_state",
]

# Option sets that change the region graph or the economy. Each must fill and be
# beatable; a bare default-only run would miss the open-mode warp network.
FILL_CASES = [
    ("defaults", {}),
    ("blessing shop", {"blessing_costs": 1}),
    ("shop at cap", {"blessing_costs": 1, "blessing_cost_max": 100000}),
    ("open mode + shop", {"random_start": 1, "statue_warp_locks": 1,
                          "blessing_costs": 1, "blessing_cost_max": 100000}),
    ("yunica open", {"character": 0, "random_start": 1, "statue_warp_locks": 1,
                     "blessing_costs": 1}),
    ("toal + rooms", {"character": 2, "room_checks": 1, "blessing_costs": 1}),
]

# A shop slot at or above this price must never hold a sphere-1 progression item.
EXPENSIVE_SP = 10_000


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    sys.exit(1)


def check_templates() -> None:
    import Options
    out = Path("_ci_templates")
    Options.generate_yaml_templates(str(out), False)
    names = {p.name for p in out.iterdir()}
    if f"{GAME}.yaml" not in names:
        _fail(f"{GAME}.yaml not generated (got {len(names)} templates)")
    if len(names) < 2:
        _fail("only our own template rendered — other worlds failed")
    print(f"PASS  template generation ({len(names)} templates, {GAME}.yaml present)")


def check_ap_suite() -> None:
    from worlds.AutoWorld import AutoWorldRegister
    keep = {g: AutoWorldRegister.world_types[g]
            for g in KEEP_WORLDS if g in AutoWorldRegister.world_types}
    if GAME not in keep:
        _fail(f"{GAME} is not registered — is the apworld in custom_worlds/?")
    AutoWorldRegister.world_types.clear()
    AutoWorldRegister.world_types.update(keep)

    loader, suite = unittest.TestLoader(), unittest.TestSuite()
    for mod in AP_TEST_MODULES:
        suite.addTests(loader.loadTestsFromName(f"test.general.{mod}"))
    with open(os.devnull, "w") as devnull:
        res = unittest.TextTestRunner(verbosity=0, stream=devnull).run(suite)
    if not res.wasSuccessful():
        for case, tb in res.failures + res.errors:
            print(f"  {case}\n{tb}")
        _fail(f"AP world suite: {len(res.failures)} failures, {len(res.errors)} errors")
    print(f"PASS  AP world suite ({res.testsRun} tests)")


def check_fill_and_economy() -> None:
    from test.general import setup_multiworld, gen_steps
    from worlds.AutoWorld import AutoWorldRegister
    from Fill import distribute_items_restrictive

    world = AutoWorldRegister.world_types[GAME]
    for label, opts in FILL_CASES:
        mw = setup_multiworld(world, gen_steps, seed=1234, options=opts)
        distribute_items_restrictive(mw)
        mw.state = mw.get_all_state()
        if not mw.can_beat_game():
            _fail(f"fill '{label}' produced an unbeatable seed")
        print(f"PASS  fill + beatable — {label}")

        prices = getattr(mw.worlds[1], "blessing_prices", {})
        if not prices:
            continue
        mw.spoiler.create_playthrough(create_paths=False)
        sphere_of = {}
        for sn, sphere in mw.spoiler.playthrough.items():
            if str(sn).isdigit():
                for loc in sphere:
                    sphere_of[str(loc)] = int(sn)
        for name, price in prices.items():
            loc = mw.get_location(name, 1)
            if (price >= EXPENSIVE_SP and loc.item.advancement
                    and sphere_of.get(str(loc)) == 1):
                _fail(f"'{label}': sphere-1 progression {loc.item.name!r} behind a "
                      f"{price} SP slot ({name})")
        print(f"PASS  no sphere-1 progression behind a >={EXPENSIVE_SP} SP slot"
              f" — {label}")


def main(argv: list[str]) -> int:
    warnings.filterwarnings("ignore")
    if len(argv) < 2:
        print(__doc__)
        return 2
    ap_root = Path(argv[1]).resolve()
    if not (ap_root / "Options.py").is_file():
        _fail(f"{ap_root} does not look like an Archipelago checkout")
    os.chdir(ap_root)
    sys.path.insert(0, str(ap_root))

    check_templates()
    check_ap_suite()
    check_fill_and_economy()
    print("\nall apworld integration checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
