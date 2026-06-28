"""Catalog Ys Origin event scripts: chests, cutscenes, item-grants, flags.

Static analysis over the disassembled ``.XSO`` scripts (extract them first with
``tools/ni_unpack.py``). Differentiates script *types* and, for each, extracts
the structured facts you can verify in-game:

  * **give-item** calls  — class-2 sub-op ``0x116``; operand0 = item id. This is
    the authoritative "this script grants item N" signal (verified: 0x57 Roda,
    0x59 Panacea, 0x6F Blue Moon Crest).
  * **box-open / location flag** — the chest's entry-guard flag: the index that
    is both *tested* at entry (``sub 0x5F [idx,1]`` → return-if-set) and *set*
    (``sub 0x64 [idx,1]``) in the same script. This is the g_flags index that
    flips when you open the chest (what ``tools/flaglog.py`` will show).
  * **other set=1 flags** — remaining booleans the script sets (scene state /
    progress / door flags), listed for manual review.

Script type is inferred from the path + content:
  chest (``S_BOX*``), cutscene (``EVT_*`` / ``*_EVENT*``), item-use
  (``ITEM*`` / ``MENU*ITEM*`` / ``USEITEM*``), scene-main (``S_dddd.XSO``),
  other.

Usage (from repo root, after extracting scripts to e.g. D:\\ghidra-work\\xso):
    python -m tools.xso_catalog <xso_root>                 # summary + chest table
    python -m tools.xso_catalog <xso_root> --csv <dir>     # write CSVs
"""

from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client.offsets import ITEM_OFFSETS, LOCATION_FLAG_OFFSETS  # noqa: E402
from tools.xso_dis import XSO  # noqa: E402

GFLAGS_OFFSET = 0x36B91C

# class-2 sub-ops we care about
OP_GIVE = 0x116     # give item: operand0 = item id
OP_CMP = 0x5F       # reg = (g_flags[op0] == op1)
OP_SET = 0x64       # g_flags[op0] = op1

# A "flag" (vs a scene-state counter) is only ever set to these values.
FLAG_VALUES = frozenset({-1, 0, 1})

# Tower zones by scene prefix (see ys-origin-tower-map).
ZONE = {
    "S_00": "Entrance/Prologue", "S_01": "Town/Hub",
    "S_10": "Wailing Blue (2-5F)", "S_20": "Flooded Prison (6-9F)",
    "S_30": "Flames of Guilt (10-13F)", "S_40": "Silent Sands (14-17F)",
    "S_50": "Corrupted Blood (18-21F)", "S_60": "Demonic Core (22-25F)",
    "S_91": "Rado's Annex", "S_COMMON": "Common",
}


def _idx_to_item_name() -> Dict[int, str]:
    """g_flags index -> item name (from the client registry)."""
    return {(off - GFLAGS_OFFSET) // 4: nm for nm, off in ITEM_OFFSETS.items()}


def _idx_to_flag_name() -> Dict[int, str]:
    return {(off - GFLAGS_OFFSET) // 4: nm
            for nm, off in LOCATION_FLAG_OFFSETS.items()}


IDX_ITEM = _idx_to_item_name()
IDX_FLAG = _idx_to_flag_name()


def classify(rel: str) -> str:
    name = rel.replace("\\", "/").rsplit("/", 1)[-1].upper()
    if name.startswith("S_BOX") or "OPENBOX" in name or name.startswith("BOX"):
        return "chest"
    if name.startswith("EVT_") or "_EVENT" in name or "EVENT_" in name \
            or "PROLOGUE" in name or "CLEAR_EVENT" in name:
        return "cutscene"
    if "ITEM" in name and ("MENU" in name or "USE" in name or
                           name.startswith("ITEM")):
        return "item-use"
    if re.fullmatch(r"S_\d{4}\.XSO", name):
        return "scene-main"
    if name.startswith("MOVE_"):
        return "move"
    return "other"


@dataclass
class ScriptInfo:
    rel: str
    scene: str
    kind: str
    gives: List[int] = field(default_factory=list)        # item ids (0x116)
    box_flags: List[int] = field(default_factory=list)     # guarded-and-set
    set_flags: List[int] = field(default_factory=list)     # other set=1


def analyze(xso: XSO, rel: str) -> ScriptInfo:
    parts = rel.replace("\\", "/").split("/")
    scene = parts[1] if len(parts) > 1 and parts[0].upper() == "MAP" else ""
    info = ScriptInfo(rel=rel, scene=scene, kind=classify(rel))

    compared: set[int] = set()          # idx tested via 0x5F (either idiom)
    set_one: set[int] = set()           # idx set to 1
    set_values: Dict[int, set] = {}     # idx -> all values it's `set` to

    for ins in xso.disasm():
        if ins.cls == 2 and ins.sub == OP_GIVE and ins.operands:
            info.gives.append(ins.operands[0])
        elif ins.cls == 2 and ins.sub == OP_CMP and ins.operands:
            compared.add(ins.operands[0])
        elif ins.cls == 2 and ins.sub == OP_SET and len(ins.operands) >= 2:
            idx, val = ins.operands[0], ins.operands[1]
            set_values.setdefault(idx, set()).add(val)
            if val == 1:
                set_one.add(idx)

    give_set = set(info.gives)
    # Box/location flag: tested as a guard AND set to 1 in the same script, set
    # only to flag-ish values (excludes scene-state counters set to 2,3,...),
    # and not itself a granted item. Handles both guard idioms (==1 / ==0).
    info.box_flags = sorted(
        idx for idx in (compared & set_one)
        if idx not in give_set and set_values.get(idx, set()) <= FLAG_VALUES
    )
    info.set_flags = sorted(set_one - set(info.box_flags) - give_set)
    return info


def walk(root: Path) -> List[ScriptInfo]:
    out: List[ScriptInfo] = []
    seen: dict[str, Path] = {}
    for p in root.rglob("*"):           # dedupe: Windows glob is case-insensitive
        if p.is_file() and p.suffix.lower() == ".xso":
            seen[str(p).lower()] = p
    for f in sorted(seen.values()):
        try:
            xso = XSO(f.read_bytes(), f.name)
        except Exception:  # noqa: BLE001
            continue
        out.append(analyze(xso, str(f.relative_to(root))))
    return out


def _items_str(ids: List[int]) -> str:
    return " ".join(
        f"0x{i:X}({IDX_ITEM[i]})" if i in IDX_ITEM else f"0x{i:X}"
        for i in ids
    )


def _flags_str(ids: List[int]) -> str:
    return " ".join(
        f"0x{i:X}({IDX_FLAG[i]})" if i in IDX_FLAG else f"0x{i:X}"
        for i in ids
    )


def report(infos: List[ScriptInfo], csv_dir: Optional[Path]) -> None:
    by_kind = Counter(i.kind for i in infos)
    print(f"  {len(infos)} scripts: " +
          ", ".join(f"{k}={c}" for k, c in by_kind.most_common()))

    chests = [i for i in infos if i.kind == "chest"]
    givers = [i for i in infos if i.gives]
    print(f"  {len(chests)} chest scripts; {len(givers)} scripts grant items "
          f"({sum(len(i.gives) for i in givers)} give-item calls).")

    # Headline: chest catalog
    print("\n=== CHEST CATALOG (scene / box-flag / items) ===")
    print("  legend: box=g_flags index that flips on open (flaglog will show it);"
          " box=? = not auto-detected, trigger to find.")
    print("  multiple items = parallel grants across difficulty/character"
          " variants — confirm which fires in-game.\n")
    for i in sorted(chests, key=lambda x: x.scene):
        zone = ZONE.get(i.scene, "")
        box = _flags_str(i.box_flags) or "?"
        items = _items_str(i.gives) or "(none via 0x116)"
        tag = "  [variants?]" if len(i.gives) > 1 else ""
        print(f"  {i.scene:7} {zone:24} box={box:18} -> {items}{tag}   {i.rel}")

    if csv_dir:
        csv_dir.mkdir(parents=True, exist_ok=True)
        # 1) chests
        with (csv_dir / "chests.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["scene", "zone", "script", "box_flag_idx",
                        "box_flag_name", "item_ids", "item_names",
                        "other_set_flags"])
            for i in sorted(chests, key=lambda x: x.scene):
                w.writerow([
                    i.scene, ZONE.get(i.scene, ""), i.rel,
                    " ".join(f"0x{b:X}" for b in i.box_flags),
                    " ".join(IDX_FLAG.get(b, "") for b in i.box_flags).strip(),
                    " ".join(f"0x{g:X}" for g in i.gives),
                    " ".join(IDX_ITEM.get(g, "") for g in i.gives).strip(),
                    " ".join(f"0x{s:X}" for s in i.set_flags),
                ])
        # 2) all give-item calls (any script type)
        with (csv_dir / "gives.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["scene", "kind", "script", "item_id", "item_name"])
            for i in infos:
                for g in i.gives:
                    w.writerow([i.scene, i.kind, i.rel, f"0x{g:X}",
                                IDX_ITEM.get(g, "")])
        print(f"\n  wrote chests.csv + gives.csv to {csv_dir}")


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    root = Path(argv[1])
    csv_dir = Path(argv[argv.index("--csv") + 1]) if "--csv" in argv else None
    infos = walk(root)
    report(infos, csv_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
