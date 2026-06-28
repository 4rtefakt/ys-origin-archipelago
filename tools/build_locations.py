"""Build the unified Ys Origin location set -> ys_origin/data/locations.json.

Five location categories, all derived from the game's own data:

  * chest  — S_BOX scripts; detect via box-flag flip (works live today).
  * event  — guarded key-item grants outside chests (altars/talks: Flabellum,
             the Ventus/Terra/Ignis bracelets, Mantid Medallion, crests…);
             detect via the item flag flip (works live today).
  * boss   — boss / mid-boss rooms (Sxx99 / Sxx80); detect deferred (needs a
             current-scene memory offset).
  * floor  — one per tower floor; detect deferred.
  * room   — one per tower room; detect deferred.

chest/event locations carry their vanilla item(s); boss/floor/room hold filler.

Usage:
    python -m tools.build_locations <xso_root> <INVINFO.DAT> <SCENELIST.SL> <out_dir>
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import build_dataset, scenelist, xso_catalog  # noqa: E402
from tools.invinfo import names as invinfo_names  # noqa: E402
from tools.xso_dis import XSO  # noqa: E402

TOWER_SCENE = re.compile(r"^S_[1-6]\d/")
TOWER_ZONE = re.compile(r"^S_[1-6]\d$")
NOISE = re.compile(r"FLAG_INV|SETSCENFLAG|RESETSCENFLAG|FLAG_MAIN", re.I)
FLOOR_RE = re.compile(r"\b(\d{1,2}F|B\d)\b")

# Key/progression item ids worth treating as event locations.
KEYISH = (set(range(0x4E, 0x54)) | {0x5C, 0x6F, 0x6B}
          | set(range(0x63, 0x71)) | {0x72, 0x73, 0x74, 0x75, 0x76})

ZONE_BY_DECADE = {
    "1": "Wailing Blue", "2": "Flooded Prison", "3": "Flames of Guilt",
    "4": "Silent Sands", "5": "Corrupted Blood", "6": "Demonic Core",
    "7": "Demonic Core",   # 26F roof / summit attaches to the top zone
}


def zone_of(scene_leaf: str) -> str:
    m = re.match(r"S_([1-6])", scene_leaf.upper())
    return ZONE_BY_DECADE.get(m.group(1), "") if m else ""


def _floor(room: str) -> str:
    m = FLOOR_RE.search(room)
    return m.group(1) if m else ""


class Builder:
    def __init__(self, xso_root: Path, inv: Path, scl: Path):
        self.root = xso_root
        self.names = invinfo_names(inv)
        self.scenes = scenelist.parse(scl)
        self.locs: List[dict] = []
        self._used: Counter = Counter()

    def _name(self, base: str) -> str:
        self._used[base] += 1
        return base if self._used[base] == 1 else f"{base} #{self._used[base]}"

    # -- categories -------------------------------------------------------- #

    def chests(self) -> None:
        for c in build_dataset.build(self.root, _INV, _SCL):
            z = c["zone"].split(" (")[0] or zone_of(c["id"])
            self.locs.append({
                "id": c["id"], "type": "chest", "zone": z,
                "floor": c["floor"], "room": c["room"],
                "name": self._name(f"{z}: {c['room']}"),
                "detect": {"method": "box_flag", "flag": c["box_flag"]},
                "items": c["items"],
            })

    def events(self) -> None:
        seen = set()
        for f in self.root.rglob("*"):
            if not (f.is_file() and f.suffix.lower() == ".xso"):
                continue
            k = str(f).lower()
            if k in seen:
                continue
            seen.add(k)
            rel = str(f.relative_to(self.root)).replace("\\", "/")
            parts = rel.split("/")
            zone = parts[1].upper() if len(parts) > 1 else ""
            sub = parts[2].upper() if len(parts) > 2 else ""
            base = parts[-1]
            if not TOWER_ZONE.match(zone) or NOISE.search(base):
                continue
            if "S_BOX" in base.upper():
                continue
            try:
                xso = XSO(f.read_bytes(), base)
            except Exception:  # noqa: BLE001
                continue
            gives, guard = set(), False
            for ins in xso.disasm():
                if ins.cls != 2 or not ins.operands:
                    continue
                idx = ins.operands[0]
                if ins.sub == 0x116 and 0x07 <= idx <= 0x7F:
                    gives.add(idx)
                elif (ins.sub == 0x64 and 0x07 <= idx <= 0x7F
                      and len(ins.operands) > 1 and ins.operands[1] >= 1):
                    gives.add(idx)
                elif ins.sub == 0x5F:
                    guard = True
            keyish = sorted(gives & KEYISH)
            if not (keyish and guard):
                continue
            room = self.scenes.get(sub, sub)
            z = zone_of(sub)
            # one location per granted key item (so each stays in the pool).
            for i in keyish:
                nm = self.names.get(i, f"0x{i:X}")
                self.locs.append({
                    "id": f"{sub}/{Path(base).stem}/0x{i:X}", "type": "event",
                    "zone": z, "floor": _floor(room), "room": room,
                    "name": self._name(f"{z}: {room} — {nm}"),
                    "detect": {"method": "item_flag", "item": f"0x{i:X}"},
                    "items": [{"id": f"0x{i:X}", "name": nm,
                               "class": build_dataset.classify_item(i)}],
                })

    def scene_based(self) -> None:
        tower = {k: v for k, v in self.scenes.items() if TOWER_SCENE.match(k)}
        floors_done = set()
        for key, room in sorted(tower.items()):
            leaf = key.rsplit("/", 1)[-1]
            z = zone_of(leaf)
            fl = _floor(room)
            is_boss = leaf.endswith("99") or leaf.endswith("80") \
                or "Velagunder" in room
            # room check
            self.locs.append({
                "id": f"{leaf}/room", "type": "room", "zone": z, "floor": fl,
                "room": room, "name": self._name(f"Explore: {room} ({leaf})"),
                "detect": {"method": "scene", "scene": leaf}, "items": [],
            })
            # boss check
            if is_boss:
                self.locs.append({
                    "id": f"{leaf}/boss", "type": "boss", "zone": z, "floor": fl,
                    "room": room, "name": self._name(f"Boss: {room} ({leaf})"),
                    "detect": {"method": "scene", "scene": leaf}, "items": [],
                })
            # floor check (one per floor, first time we see it)
            if fl and fl not in floors_done:
                floors_done.add(fl)
                self.locs.append({
                    "id": f"floor/{fl}", "type": "floor", "zone": z, "floor": fl,
                    "room": room, "name": self._name(f"Reach {fl}"),
                    "detect": {"method": "scene_floor", "floor": fl}, "items": [],
                })

    def statues(self) -> None:
        """Goddess statues (save/warp points): S_SAVEOBJECTCHANGE checks a
        per-statue state flag that flips when the statue is activated."""
        seen = set()
        for f in sorted(self.root.rglob("*.XSO")):
            if f.name.upper() != "S_SAVEOBJECTCHANGE.XSO":
                continue
            parts = str(f.relative_to(self.root)).replace("\\", "/").split("/")
            sub = parts[2].upper() if len(parts) > 2 else ""
            if sub in seen:
                continue
            seen.add(sub)
            try:
                xso = XSO(f.read_bytes(), f.name)
            except Exception:  # noqa: BLE001
                continue
            flag = next((ins.operands[0] for ins in xso.disasm()
                         if ins.cls == 2 and ins.sub == 0x5F and ins.operands), None)
            z = zone_of(sub)
            if flag is None or not z:
                continue
            room = self.scenes.get(sub, sub)
            self.locs.append({
                "id": f"{sub}/statue", "type": "statue", "zone": z,
                "floor": _floor(room), "room": room,
                "name": self._name(f"Statue: {room} ({sub})"),
                "detect": {"method": "box_flag", "flag": f"0x{flag:X}"},
                "items": [],
            })

    def build(self) -> List[dict]:
        self.chests()
        self.events()
        self.statues()
        self.scene_based()
        return self.locs


# build_dataset.build wants the same INVINFO/SCENELIST paths; stash globally.
_INV: Path
_SCL: Path


def main(argv) -> int:
    global _INV, _SCL
    if len(argv) < 5:
        print(__doc__)
        return 2
    xso_root, _INV, _SCL, out = (Path(argv[1]), Path(argv[2]),
                                 Path(argv[3]), Path(argv[4]))
    locs = Builder(xso_root, _INV, _SCL).build()
    out.mkdir(parents=True, exist_ok=True)
    (out / "locations.json").write_text(
        json.dumps(locs, indent=1, ensure_ascii=False), encoding="utf-8")
    by_type = Counter(l["type"] for l in locs)
    print(f"  wrote {len(locs)} locations to {out}\\locations.json")
    print("  by type:", dict(by_type))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
