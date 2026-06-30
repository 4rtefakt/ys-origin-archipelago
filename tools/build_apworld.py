"""Build a distributable ``ys_origin.apworld`` from the ``ys_origin/`` package.

Produces a zip whose top-level folder is ``ys_origin/`` (the AP world id) plus a
root ``archipelago.json`` manifest (required from Archipelago 0.7.0; a harmless
deprecation warning before that). Excludes ``__pycache__`` / ``.pyc``.

    python -m tools.build_apworld [output_path]
        default output: <repo>/dist/ys_origin.apworld
"""

from __future__ import annotations

import json
import os
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "ys_origin"

WORLD_VERSION = "1.4.0"
MIN_AP_VERSION = "0.6.0"

MANIFEST = {
    # container-format version this file targets (AP 0.6.x uses 7)
    "compatible_version": 7,
    "version": 7,
    "game": "Ys Origin",
    "world_version": WORLD_VERSION,
    "minimum_ap_version": MIN_AP_VERSION,
}


def build(out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        # the world package, top folder = ys_origin/
        for root, dirs, files in os.walk(PKG):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if f.endswith(".pyc"):
                    continue
                full = Path(root) / f
                z.write(full, full.relative_to(REPO).as_posix())
        # manifest at the archive root
        z.writestr("archipelago.json", json.dumps(MANIFEST, indent=1))
    return out


def main(argv) -> int:
    out = Path(argv[1]) if len(argv) > 1 else REPO / "dist" / "ys_origin.apworld"
    build(out)
    names = zipfile.ZipFile(out).namelist()
    print(f"built {out} ({out.stat().st_size} bytes, {len(names)} entries)")
    assert "archipelago.json" in names, "manifest missing"
    assert any(n == "ys_origin/__init__.py" for n in names), "package missing"
    assert any(n.endswith("data/room_logic.json") for n in names), "room_logic missing"
    print("  manifest + package + data present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
