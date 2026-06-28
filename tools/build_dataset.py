"""Join all extracted sources into one authoritative Ys Origin dataset.

Combines:
  * chest catalog       (tools/xso_catalog) — box flag + granted item ids
  * scene names         (tools/scenelist)   — floor + room name per scene
  * item table          (tools/invinfo)     — id → English name
  * (item gate logic is produced separately by tools/xso_logic)

Output:
  * ``<out>/chests.json`` — machine-readable: one record per chest with scene,
    zone, floor, room, box-flag, and items (id+name+classification).
  * ``<out>/master_chests.csv`` — the same, human-reviewable.

This is the bridge from raw RE to the apworld tables (regions/locations/items).

Usage (from repo root):
    python -m tools.build_dataset <xso_root> <INVINFO.DAT> <SCENELIST.SL> <out_dir>
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import invinfo, scenelist, xso_catalog  # noqa: E402

# Item id ranges -> AP-ish classification (from the INVINFO id-space map).
def classify_item(item_id: int) -> str:
    if 0x4E <= item_id <= 0x53:            # boss medallions
        return "progression"
    if item_id in (0x5C, 0x6F, 0x6B):      # crests, flabellum
        return "progression"
    if 0x63 <= item_id <= 0x6E:            # keys / idols / black pearl
        return "progression"
    if 0x72 <= item_id <= 0x76:            # seeds, elemental bracelets
        return "progression"
    if item_id <= 0x35:                    # weapons / armor / boots / shields
        return "useful"
    if 0x36 <= item_id <= 0x41:            # accessories
        return "useful"
    if 0x42 <= item_id <= 0x4D:            # stat drops
        return "filler"
    if 0x57 <= item_id <= 0x5B:            # consumables / materials
        return "filler"
    if 0x78 <= item_id <= 0x7F:            # gold
        return "filler"
    return "filler"


_FLOOR = re.compile(r"\b(\d{1,2}F|B\d)\b")


def _floor(room: str) -> str:
    m = _FLOOR.search(room)
    return m.group(1) if m else ""


def _subscene(rel: str) -> str:
    """'MAP\\S_10\\S_1001\\S_BOX01.XSO' -> 'S_1001'."""
    parts = rel.replace("\\", "/").split("/")
    return parts[2].upper() if len(parts) > 2 else ""


def build(xso_root: Path, inv: Path, scl: Path) -> List[dict]:
    names = invinfo.names(inv)
    scenes = scenelist.parse(scl)
    xso_catalog.ITEM_NAME.update(names)   # so catalog uses real names too
    infos = xso_catalog.walk(xso_root)

    records: List[dict] = []
    for i in infos:
        if i.kind != "chest" or not i.gives:
            continue
        sub = _subscene(i.rel)
        room = scenes.get(sub, scenes.get(f"{i.scene}/{sub}", ""))
        rec = {
            "id": f"{sub}/{Path(i.rel).stem}",        # e.g. S_1001/S_BOX01
            "scene": i.scene,
            "zone": xso_catalog.ZONE.get(i.scene, ""),
            "floor": _floor(room),
            "room": room,
            "box_flag": (f"0x{i.box_flags[0]:X}" if i.box_flags else None),
            "script": i.rel.replace("\\", "/"),
            "items": [
                {"id": f"0x{g:X}", "name": names.get(g, f"0x{g:X}"),
                 "class": classify_item(g)}
                for g in i.gives
            ],
        }
        records.append(rec)
    records.sort(key=lambda r: (r["scene"], r["id"]))
    return records


def write(records: List[dict], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "chests.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    with (out / "master_chests.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "zone", "floor", "room", "box_flag",
                    "item_ids", "item_names", "item_classes", "script"])
        for r in records:
            w.writerow([
                r["id"], r["zone"], r["floor"], r["room"], r["box_flag"] or "?",
                " ".join(it["id"] for it in r["items"]),
                " | ".join(it["name"] for it in r["items"]),
                " ".join(it["class"] for it in r["items"]),
                r["script"],
            ])
    print(f"  wrote {len(records)} chest records to {out}\\chests.json + master_chests.csv")


def main(argv) -> int:
    if len(argv) < 5:
        print(__doc__)
        return 2
    recs = build(Path(argv[1]), Path(argv[2]), Path(argv[3]))
    write(recs, Path(argv[4]))
    # quick summary
    from collections import Counter
    byzone = Counter(r["zone"] for r in recs)
    print("  chests per zone:", dict(byzone))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
