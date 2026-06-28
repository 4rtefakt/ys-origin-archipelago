"""Extract access-logic hints from Ys Origin event scripts.

The apworld needs *rules*: what gates each location/region. A lot of that is
encoded in the scripts as g_flags checks on key items — e.g. a door script does
``0x5F [key_id, 1]`` ("do you have this key?") then branches. This tool mines,
per progression item, where it is **found** (give-item / chest), where it is
**required** (checked in a non-giver script — a gate), and whether it is
**consumed** (set to 0 / decremented at the gate).

What this DOES cover: key→door, crest→altar, medallion→arena, idol→event — the
item-dependency backbone. What it does NOT cover: physical/ability reachability
(double-jump, dash, elemental-platform traversal) — that lives in level geometry,
not scripts, and must be authored by hand.

Hub bookkeeping in ``S_01`` (``SETSCENFLAG``/``RESETSCENFLAG``/``FLAG_MAIN``/
``S10xx`` — new-game/debug flag dumps that touch every flag) is filtered out so
the gates that remain are real.

Usage (after extracting scripts + INVINFO.DAT):
    python -m tools.xso_logic <xso_root> --names <INVINFO.DAT> [--csv out.csv]
"""

from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import invinfo  # noqa: E402
from tools.xso_dis import XSO  # noqa: E402

OP_GIVE = 0x116
OP_SET = 0x64       # g_flags[op0] = op1
OP_SUB = 0x69       # g_flags[op0] -= op1
OP_CMP = 0x5F       # reg = (g_flags[op0] == op1)   <- the gate check
OP_LOAD = 0x41      # reg = g_flags[op0]

# Progression-ish item ids (keys, crests, medallions, idols, flabellum,
# bracelets, seeds, black pearl). Consumables/gear/gold are not gates.
PROGRESSION: Set[int] = (
    set(range(0x4E, 0x54))                       # boss medallions
    | {0x5C, 0x6F}                               # Red/Blue Moon Crest
    | {0x63, 0x64, 0x65, 0x66, 0x67, 0x6E}       # keys
    | {0x68, 0x69, 0x6A, 0x6B, 0x70}             # idols, black pearl, flabellum, falcon idol
    | {0x72, 0x73}                               # seeds
    | {0x74, 0x75, 0x76}                         # elemental bracelets
)

# Script-name patterns that are hub/debug flag dumps, not real gates.
_NOISE = re.compile(r"SETSCENFLAG|RESETSCENFLAG|FLAG_MAIN|^S1\d{3}", re.I)


def _basename(rel: str) -> str:
    return rel.replace("\\", "/").rsplit("/", 1)[-1]


def _scene(rel: str) -> str:
    p = rel.replace("\\", "/").split("/")
    return p[1] if len(p) > 1 and p[0].upper() == "MAP" else ""


@dataclass
class ItemLogic:
    item_id: int
    found_in: Set[str] = field(default_factory=set)             # scenes (chests)
    gates: List[Tuple[str, str]] = field(default_factory=list)  # (scene, script)
    consumed: bool = False


def _xso_files(root: Path) -> List[Path]:
    seen: Dict[str, Path] = {}
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() == ".xso":
            seen[str(p).lower()] = p
    return sorted(seen.values())


def extract(root: Path) -> Dict[int, ItemLogic]:
    out: Dict[int, ItemLogic] = {i: ItemLogic(i) for i in PROGRESSION}
    for f in _xso_files(root):
        try:
            xso = XSO(f.read_bytes(), f.name)
        except Exception:  # noqa: BLE001
            continue
        rel = str(f.relative_to(root))
        scene, base = _scene(rel), _basename(rel)
        noise = bool(_NOISE.search(base))
        gives: Set[int] = set()
        checks: Set[int] = set()
        consumes: Set[int] = set()
        for ins in xso.disasm():
            if ins.cls != 2 or not ins.operands:
                continue
            idx = ins.operands[0]
            if idx not in PROGRESSION:
                continue
            if ins.sub == OP_GIVE:
                gives.add(idx)
            elif ins.sub in (OP_CMP, OP_LOAD):
                checks.add(idx)
            elif ins.sub == OP_SUB:
                consumes.add(idx)
            elif ins.sub == OP_SET and len(ins.operands) > 1 and ins.operands[1] <= 0:
                consumes.add(idx)
        for i in gives:
            out[i].found_in.add(scene)
        if not noise:
            # a gate = a check in a script that doesn't itself grant the item
            for i in checks - gives:
                out[i].gates.append((scene, base))
            for i in consumes - gives:
                out[i].consumed = True
    return out


def report(table: Dict[int, ItemLogic], nm: Dict[int, str],
           out_csv: Optional[Path]) -> None:
    rows = []
    print("  item                      found-in     -> required-at (gate)   consumed")
    for i in sorted(table):
        lg = table[i]
        if not lg.found_in and not lg.gates:
            continue
        name = nm.get(i, f"0x{i:X}")
        found = ",".join(sorted(s for s in lg.found_in if s)) or "?"
        gate_scenes = sorted({s for s, _ in lg.gates if s})
        gate_str = ",".join(gate_scenes) if gate_scenes else "-"
        print(f"  0x{i:02X} {name:22} {found:12} -> {gate_str:18} "
              f"{'consumed' if lg.consumed else ''}")
        rows.append((f"0x{i:X}", name, found, gate_str,
                     "; ".join(f"{s}/{b}" for s, b in lg.gates),
                     "yes" if lg.consumed else ""))
    if out_csv:
        with out_csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["item_id", "name", "found_in_scenes",
                        "gate_scenes", "gate_scripts", "consumed"])
            w.writerows(rows)
        print(f"\n  wrote {len(rows)} item-logic rows to {out_csv}")


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    root = Path(argv[1])
    nm: Dict[int, str] = {}
    if "--names" in argv:
        nm = invinfo.names(Path(argv[argv.index("--names") + 1]))
    out_csv = Path(argv[argv.index("--csv") + 1]) if "--csv" in argv else None
    report(extract(root), nm, out_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
