"""Regenerate ``docs/options.js``, the option list the website's yaml builder is drawn from.

    python -m tools.build_site            # write docs/options.js
    python -m tools.build_site --check    # exit 1 when the file on disk is out of date

Types, defaults, ranges and choices are read from ``ys_origin/options.py`` (the
source of truth, parsed without importing Archipelago); the one-line help of each
option is its "Meaning" cell in the README's options table. An option with no
README row is an error, so the table, the site and the apworld cannot drift apart.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "options.js"

# Accepted by the apworld but not something a player should set.
HIDDEN = {"progressive_skills", "exp_multiplier_max", "start_inventory_from_pool"}

# Section of the form each option goes in, in display order. An option missing
# here still shows, under "Other".
GROUPS = [
    ("You", ["character", "goal"]),
    ("What counts as a check", ["statue_checks", "blessing_checks", "boss_checks", "floor_checks", "room_checks"]),
    ("Blessing shop", ["shop_hints", "blessing_costs", "blessing_cost_min", "blessing_cost_max",
                       "blessing_shop_unlock", "blessing_items", "progressive_blessings"]),
    ("Warps and starting point", ["statue_warp_locks", "random_start", "max_starting_floor", "max_warp_floors_skip"]),
    ("Starting loadout", ["starting_level", "starting_weapon_level", "starting_items"]),
    ("Leveling", ["level_scaling", "level_margin", "exp_multiplier_base", "exp_multiplier_catchup",
                  "exp_catchup_margin"]),
    ("Gear and logic", ["progressive_armor", "weapon_requirements", "fire_skill_crossing"]),
    ("Multiworld", ["trap_count", "death_link"]),
    ("Advanced", ["item_classification_overrides"]),
]

# An option only matters when another has a given value: the form greys it out otherwise.
NEEDS = {
    "blessing_cost_min": ["blessing_costs", "shuffled"],
    "blessing_cost_max": ["blessing_costs", "shuffled"],
    "blessing_shop_unlock": ["blessing_costs", "shuffled"],
    "progressive_blessings": ["blessing_items", True],
    "random_start": ["statue_warp_locks", True],
    "max_starting_floor": ["random_start", True],
    "max_warp_floors_skip": ["random_start", True],
    "fire_skill_crossing": ["character", "toal"],
}


def _literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def read_options() -> list[dict]:
    tree = ast.parse((ROOT / "ys_origin" / "options.py").read_text(encoding="utf-8"))
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    fields = [(s.target.id, s.annotation.id) for s in classes["YsOriginOptions"].body
              if isinstance(s, ast.AnnAssign) and isinstance(s.annotation, ast.Name)]
    out = []
    for name, cls in fields:
        if name in HIDDEN:
            continue
        opt: dict = {"name": name}
        node = classes.get(cls)
        base = node.bases[0].id if node and node.bases and isinstance(node.bases[0], ast.Name) else cls
        attrs = {}
        for s in (node.body if node else []):
            if isinstance(s, ast.Assign) and isinstance(s.targets[0], ast.Name):
                attrs[s.targets[0].id] = _literal(s.value)
        opt["label"] = attrs.get("display_name") or name.replace("_", " ").capitalize()
        if base in ("Toggle", "DefaultOnToggle", "DeathLink"):
            opt.update(type="toggle", default=base == "DefaultOnToggle")
        elif base == "Choice":
            choices = sorted(((v, k[len("option_"):]) for k, v in attrs.items() if k.startswith("option_")))
            opt.update(type="choice", choices=[c for _, c in choices],
                       default=dict(choices)[attrs.get("default", 0)])
        elif base == "Range":
            opt.update(type="range", min=attrs["range_start"], max=attrs["range_end"], default=attrs["default"])
        elif base == "OptionList":
            opt.update(type="list", default=attrs.get("default") or [])
        elif base == "OptionDict":
            opt.update(type="dict", default={})
        else:
            raise SystemExit(f"{name}: option base {base!r} is not handled by tools/build_site.py")
        out.append(opt)
    return out


def read_help() -> dict[str, str]:
    """option name -> the README table's Meaning cell, as plain text."""
    out = {}
    for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\| `(\w+)` \|(.*)\|\s*$", line)
        if m:
            text = m.group(2).rsplit("|", 1)[-1].strip()
            out[m.group(1)] = re.sub(r"\*\*(.+?)\*\*", r"\1", text).replace("`", "")
    return out


def build() -> str:
    options, helps = read_options(), read_help()
    missing = [o["name"] for o in options if o["name"] not in helps]
    if missing:
        raise SystemExit("no row in the README options table for: " + ", ".join(missing))
    where = {name: title for title, names in GROUPS for name in names}
    for o in options:
        o["help"] = helps[o["name"]]
        o["group"] = where.get(o["name"], "Other")
        if o["name"] in NEEDS:
            o["needs"] = NEEDS[o["name"]]
    order = [title for title, _ in GROUPS] + ["Other"]
    rank = {name: i for _, names in GROUPS for i, name in enumerate(names)}
    options.sort(key=lambda o: (order.index(o["group"]), rank.get(o["name"], 99)))
    template = (ROOT / "Ys-Origin.yaml").read_text(encoding="utf-8")
    requires = re.search(r"^\s+version:\s*(\S+)", template, re.M).group(1)
    data = {"game": "Ys Origin", "requires": requires, "groups": [g for g in order if any(o["group"] == g for o in options)],
            "options": options}
    return ("// Generated by tools/build_site.py from ys_origin/options.py and the README table. Do not edit.\n"
            "window.YSO = " + json.dumps(data, indent=1, ensure_ascii=False) + ";\n")


def main(argv: list[str]) -> int:
    text = build()
    if "--check" in argv:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            print("docs/options.js is out of date: run python -m tools.build_site")
            return 1
        return 0
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(json.loads(text.split('=', 1)[1].rstrip().rstrip(';'))['options'])} options)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
