"""Log scene/room transitions + key-item acquisitions during a (vanilla) run.

Now that the current-scene global is known (`current_scene` = +0x36C100, the
decimal scene leaf, living at g_flags[0x1F9]), we can capture the room ADJACENCY
GRAPH and "what's obtained before each room" with NO mod rebuild — just poll
memory while you play. This bootstraps the Part-3 room access rules.

Run it (game running) and play through a zone; ideally a VANILLA run (rando off
or a vanilla install) so skills/keys come at their natural spots and you observe
true connectivity:

    python -m tools.scenelog                 # log to tools/../scenelog.jsonl

It polls at 10 Hz and records, on every room change, an edge `from -> to` with
both room names + floor, plus any g_flags item/key/skill that became obtained
since the previous room (so each edge carries the inventory delta that unlocked
it). Ctrl-C to stop; it also prints a de-duplicated adjacency summary.

Watched g_flags: the low item/skill/key range (<0x80) by name (from items.json)
— wind/earth/fire bracelets (0x74/0x75/0x76), medallions (0x4E-0x53), keys, etc.
Output JSONL is append-only so multiple sessions accumulate.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client.memory import ProcessMemory, MemoryError_  # noqa: E402
from client.offsets import MODULE_NAME  # noqa: E402

SCENE_OFF = 0x36C100   # current scene leaf number (g_flags[0x1F9])
FLOOR_OFF = 0x36BC58   # current floor
GFLAGS = 0x36B91C      # g_flags base
WATCH_HI = 0x80        # watch item/skill/key indices below this

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "scenelog.jsonl"


def _scene_names() -> Dict[int, str]:
    """scene number -> 'floor / room' from locations.json (no external file)."""
    locs = json.loads((REPO / "ys_origin" / "data" / "locations.json").read_text("utf-8"))
    out: Dict[int, str] = {}
    for l in locs:
        s = l.get("detect", {}).get("scene")
        if not s:
            continue
        num = int(s.split("/")[0][2:])  # 'S_1014/S_BOX01' -> 1014
        nm = f"{l.get('floor','')} {l.get('room','')}".strip()
        out.setdefault(num, nm or s)
    return out


def _item_names() -> Dict[int, str]:
    items = json.loads((REPO / "ys_origin" / "data" / "items.json").read_text("utf-8"))
    return {v: k for k, v in items.items()}


def main(argv) -> int:
    try:
        mem = ProcessMemory.attach(MODULE_NAME)
    except MemoryError_ as e:
        print(f"  attach failed: {e} (is the game running?)")
        return 1

    scenes = _scene_names()
    inames = _item_names()

    def name(num: int) -> str:
        return scenes.get(num, f"S_{num}?")

    base = mem.resolve(GFLAGS)
    prev_scene = mem.read_int32(mem.resolve(SCENE_OFF))
    # snapshot watched item slots
    prev_items = [mem.read_int32(base + i * 4) for i in range(WATCH_HI)]

    edges = {}  # (from,to) -> count
    out_f = OUT.open("a", encoding="utf-8")
    print(f"  logging -> {OUT}")
    print(f"  start scene = {prev_scene} ({name(prev_scene)}). Play; Ctrl-C to stop.\n")
    try:
        while True:
            time.sleep(0.1)
            try:
                scene = mem.read_int32(mem.resolve(SCENE_OFF))
            except MemoryError_:
                continue
            # detect newly-obtained items each tick (cheap, low range)
            gained = []
            for i in range(WATCH_HI):
                try:
                    v = mem.read_int32(base + i * 4)
                except MemoryError_:
                    continue
                if prev_items[i] < 1 <= v:
                    gained.append({"idx": i, "name": inames.get(i, f"0x{i:X}"), "val": v})
                prev_items[i] = v
            if gained:
                for g in gained:
                    print(f"   + obtained {g['name']} (0x{g['idx']:X}) in {name(scene)}")
                    out_f.write(json.dumps({"t": "item", "scene": scene,
                                            "scene_name": name(scene), **g}) + "\n")
                out_f.flush()
            if scene != prev_scene:
                floor = mem.read_int32(mem.resolve(FLOOR_OFF))
                rec = {"t": "move", "from": prev_scene, "from_name": name(prev_scene),
                       "to": scene, "to_name": name(scene), "floor": floor,
                       "ts": round(time.time(), 1)}
                out_f.write(json.dumps(rec) + "\n")
                out_f.flush()
                key = (prev_scene, scene)
                edges[key] = edges.get(key, 0) + 1
                arrow = "->" if edges[key] == 1 else f"-> (x{edges[key]})"
                print(f"  {prev_scene} {name(prev_scene)!r}  {arrow}  "
                      f"{scene} {name(scene)!r}  [floor {floor}]")
                prev_scene = scene
    except KeyboardInterrupt:
        pass
    finally:
        out_f.close()

    print(f"\n  {len(edges)} distinct edges this session:")
    for (a, b), c in sorted(edges.items()):
        print(f"    {a} {name(a)!r} -> {b} {name(b)!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
