"""Extract item icons from the game archives -> client/icons/<slug>.png.

For each item in ``MISC/INVINFO.DAT`` (icon tag like ``tl_21`` / ``sw_00``),
finds the matching DDS in the archive (``MENU/ICON/<TAG>.DDS`` or, for weapons,
``MENU/ICON2/<TAG>V.DDS``), decodes it (Pillow), and writes a small PNG named by
the item's slug (``celcetan_panacea.png``) — which is exactly what the client
overlay loads via ``tk.PhotoImage``.

Requires Pillow (read-only DDS support) at extraction time only; the client/
overlay needs nothing extra.

Usage (from repo root):
    python -m tools.extract_icons <release/data.ni> <INVINFO.DAT> [out_dir] [size]
    (out_dir defaults to client/icons, size to 28)
"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client.overlay import icon_slug  # noqa: E402
from tools import invinfo  # noqa: E402
from tools.ni_unpack import NIArchive  # noqa: E402


def _entry_index(ar: NIArchive) -> dict:
    """Normalised 'menu/icon/tl_21.dds' -> Entry (lowercase, forward slashes)."""
    idx = {}
    for e in ar.entries:
        key = e.clean_name.lower()           # already strips .z, '/'-normalised
        idx[key] = e
    return idx


def _candidates(tag: str) -> list[str]:
    t = tag.lower()
    return [f"menu/icon/{t}.dds", f"menu/icon2/{t}v.dds",
            f"menu/icon2/{t}.dds", f"menu/icon/{t}v.dds"]


def main(argv) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    from PIL import Image

    ni = Path(argv[1])
    inv = Path(argv[2])
    out = Path(argv[3]) if len(argv) > 3 else \
        Path(__file__).resolve().parent.parent / "client" / "icons"
    size = int(argv[4]) if len(argv) > 4 else 28
    out.mkdir(parents=True, exist_ok=True)

    ar = NIArchive(ni)
    index = _entry_index(ar)
    names = invinfo.names(inv)
    tags = invinfo.icon_tags(inv)

    ok = miss = 0
    with open(ar.na_path, "rb") as fh:
        for item_id, tag in tags.items():
            name = names.get(item_id, "")
            if not name or name.startswith("Reserved"):
                continue
            entry = next((index[c] for c in _candidates(tag) if c in index), None)
            if entry is None:
                miss += 1
                continue
            try:
                dds = ar.read(entry, fh)
                im = Image.open(BytesIO(dds)).convert("RGBA")
                im = im.resize((size, size), Image.LANCZOS)
                im.save(out / f"{icon_slug(name)}.png")
                ok += 1
            except Exception as e:  # noqa: BLE001
                miss += 1
                print(f"  ! {name} ({tag}): {type(e).__name__}: {e}")
    print(f"  wrote {ok} icon PNG(s) to {out} ({miss} missing/failed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
