"""Generate N random seeds against a real Archipelago checkout and assert each
one is generatable and beatable.

The hand-written yamls in ``tools/ci_apworld_check.py`` cover the configurations
we happened to think of, which is exactly why the live betas kept surfacing
option combinations nobody had run:

  * every playtest used ``blessing_costs: shuffled`` over a wide range, so a
    seed where several blessings priced IDENTICALLY (a narrow range, or
    ``min == max``) was never generated -- the statue list collapsing to one
    repeated row shipped in two betas before a player hit it;
  * ``weapon_requirements`` was off in every test yaml, so nothing noticed that
    the fill then had no reason to keep Cleria Ore reachable.

Random option combinations find that class of thing. This is a fuzzer, not a
proof: a pass means N seeds happened to work, and failures are the point.

Usage (from the repo root):

    python -m tools.fuzz_seeds <archipelago-root> [-n 40] [--seed 1234]

Exits non-zero on the first failure, printing the offending yaml so it can be
replayed. Each run prints the option combination it used, so a CI failure is
reproducible from the log alone.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# Option space. Values are drawn independently, so combinations that no
# hand-written yaml would pair up (open mode + weapon requirements + a flat
# blessing price, say) come out regularly.
CHOICES: dict[str, list] = {
    "character": ["yunica", "hugo", "toal"],
    "goal": ["defeat_darm"],
    "statue_checks": [True, False],
    "blessing_checks": [True, False],
    "boss_checks": [True, False],
    "floor_checks": [True, False],
    "room_checks": [True, False],
    "shop_hints": [True, False],
    "blessing_costs": ["vanilla", "shuffled"],
    "blessing_shop_unlock": ["all", "one_per_floor"],
    "blessing_items": [True, False],
    "progressive_armor": [True, False],
    "progressive_skills": [True, False],
    "progressive_blessings": [True, False],
    "statue_warp_locks": [True, False],
    "random_start": [True, False],
    "weapon_requirements": [True, False],
    "level_scaling": ["off", "level_floor", "exp_multiplier", "both"],
    "accessibility": ["full", "minimal"],
    "trap_count": [0, 5, 20],
    "starting_level": [1, 10],
}


def make_yaml(rng: random.Random, name: str) -> tuple[str, dict]:
    opts = {k: rng.choice(v) for k, v in CHOICES.items()}
    # Blessing prices: deliberately include degenerate ranges. A flat band makes
    # every blessing cost the same, which is what broke the statue relabel.
    lo, hi = rng.choice([(100, 8000), (500, 500), (100, 200), (10, 500000)])
    opts["blessing_cost_min"], opts["blessing_cost_max"] = lo, hi
    body = "\n".join(f"  {k}: {json.dumps(v)}" for k, v in opts.items())
    text = (f"name: {name}\n"
            f"description: fuzz\n"
            f"game: Ys Origin\n"
            f"requires:\n  version: 0.4.0\n\n"
            f"Ys Origin:\n"
            f"  progression_balancing: 0\n"
            f"{body}\n")
    return text, opts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ap_root", type=Path)
    ap.add_argument("-n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    ap_root: Path = args.ap_root
    if not (ap_root / "Generate.py").exists():
        print(f"FAIL  {ap_root} does not look like an Archipelago checkout")
        return 2

    rng = random.Random(args.seed)
    work = Path(tempfile.mkdtemp(prefix="ysofuzz_"))
    players = work / "Players"
    players.mkdir()
    try:
        for i in range(args.n):
            text, opts = make_yaml(rng, f"Fuzz{i}")
            (players / f"Fuzz{i}.yaml").write_text(text, encoding="utf-8")
            out = work / f"out{i}"
            env = {"SKIP_REQUIREMENTS_UPDATE": "1"}
            proc = subprocess.run(
                [sys.executable, "Generate.py",
                 "--player_files_path", str(players),
                 "--outputpath", str(out), "--seed", str(1000 + i)],
                cwd=ap_root, capture_output=True, text=True,
                env={**dict(__import__("os").environ), **env},
            )
            (players / f"Fuzz{i}.yaml").unlink()   # one player per run
            if proc.returncode != 0:
                print(f"FAIL  seed {i} did not generate\n")
                print(text)
                print(proc.stdout[-3000:])
                print(proc.stderr[-3000:])
                return 1
            print(f"ok  {i:3}  {opts['character']:6} "
                  f"cost={opts['blessing_cost_min']}-{opts['blessing_cost_max']} "
                  f"weapon={opts['weapon_requirements']} "
                  f"warps={opts['statue_warp_locks']} "
                  f"acc={opts['accessibility']}")
        print(f"\nall {args.n} fuzzed seeds generated and passed AP's own "
              f"beatability check")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
