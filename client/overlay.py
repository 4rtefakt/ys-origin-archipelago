"""A small always-on-top overlay showing the last few received AP items.

No game injection — it's a separate borderless Tkinter window (pure stdlib) you
park over the game. The AP client pushes each applied item; the overlay shows
the most recent ``max_rows`` with name, source, and location (and an icon if a
matching PNG exists under ``icon_dir``).

Threading: Tk runs on its own thread with its own mainloop; the client pushes
via a thread-safe queue that the window drains with ``after()``. All public
methods are safe to call from the asyncio client thread. If Tk is unavailable
(headless), everything degrades to no-ops so the client still runs.

Icons are loaded as PNG via ``tk.PhotoImage`` (Tk 8.6+), so no image library is
needed at runtime — convert the game's DDS icons to ``<icon_dir>/<item_id>.png``
offline with ``tools/extract_icons.py``.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def icon_slug(name: str) -> str:
    """Filename-safe key for an item name: 'Celcetan Panacea' -> 'celcetan_panacea'."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")

log = logging.getLogger("ys_origin.overlay")

# soft palette (works on the dark default we give the window)
_BG = "#16181d"
_FG = "#f2f2f2"
_DIM = "#9aa0aa"
_ACCENT = "#e0a458"


@dataclass
class Entry:
    item: str
    source: str = ""
    location: str = ""


def tk_available() -> bool:
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


class Overlay:
    """Thread-backed overlay. Construct, :meth:`start`, then :meth:`push`."""

    def __init__(self, *, title: str = "Ys Origin — Archipelago",
                 max_rows: int = 5, icon_dir: Optional[Path] = None,
                 enabled: bool = True):
        self.max_rows = max_rows
        self.icon_dir = Path(icon_dir) if icon_dir else None
        self.enabled = enabled and tk_available()
        self._q: "queue.Queue[Entry]" = queue.Queue()
        self._title = title
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # Tk objects (created on the Tk thread)
        self._root = None
        self._rows = []          # list of (icon_label, text_label)
        self._icons = {}         # slug -> PhotoImage cache (keep refs alive)
        self._recent: list[Entry] = []

    # -- public (call from any thread) ------------------------------------- #

    def start(self) -> None:
        if not self.enabled:
            log.info("overlay disabled (no Tk or turned off)")
            return
        self._thread = threading.Thread(target=self._run, name="Overlay",
                                        daemon=True)
        self._thread.start()

    def push(self, item: str, source: str = "", location: str = "") -> None:
        if self.enabled:
            self._q.put(Entry(item, source, location))

    def stop(self) -> None:
        self._stop.set()

    # -- Tk thread --------------------------------------------------------- #

    def _run(self) -> None:
        try:
            import tkinter as tk
        except Exception as e:  # noqa: BLE001
            log.warning("overlay: Tk unavailable: %s", e)
            return
        root = tk.Tk()
        self._root = root
        root.title(self._title)
        root.configure(bg=_BG)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", 0.92)
        except Exception:  # noqa: BLE001
            pass
        root.overrideredirect(True)            # borderless
        root.geometry(self._corner_geometry(root, 360, 26 + 44 * self.max_rows))

        # header (drag handle + close)
        header = tk.Frame(root, bg=_BG)
        header.pack(fill="x")
        tk.Label(header, text="◆ Archipelago — recent items", bg=_BG,
                 fg=_ACCENT, font=("Segoe UI", 9, "bold")).pack(side="left",
                                                                padx=8, pady=3)
        tk.Label(header, text="✕", bg=_BG, fg=_DIM,
                 font=("Segoe UI", 9, "bold")).pack(side="right", padx=8)
        self._bind_drag(root, header)
        header.bind("<Button-3>", lambda e: self._hide())
        root.bind("<Escape>", lambda e: self._hide())

        for _ in range(self.max_rows):
            row = tk.Frame(root, bg=_BG)
            row.pack(fill="x", padx=6, pady=1)
            icon = tk.Label(row, bg=_BG, width=2)
            icon.pack(side="left", padx=(2, 6))
            text = tk.Label(row, bg=_BG, fg=_FG, justify="left", anchor="w",
                            font=("Segoe UI", 10))
            text.pack(side="left", fill="x", expand=True)
            self._rows.append((icon, text))

        self._render()
        self._poll(root)
        root.mainloop()

    # -- internals --------------------------------------------------------- #

    def _corner_geometry(self, root, w: int, h: int) -> str:
        sw = root.winfo_screenwidth()
        return f"{w}x{h}+{sw - w - 24}+24"

    def _bind_drag(self, root, handle) -> None:
        state = {"x": 0, "y": 0}

        def press(e):
            state["x"], state["y"] = e.x, e.y

        def drag(e):
            root.geometry(f"+{root.winfo_x() + e.x - state['x']}"
                          f"+{root.winfo_y() + e.y - state['y']}")
        handle.bind("<Button-1>", press)
        handle.bind("<B1-Motion>", drag)

    def _hide(self) -> None:
        if self._root:
            self._root.withdraw()

    def _poll(self, root) -> None:
        drained = False
        try:
            while True:
                self._recent.insert(0, self._q.get_nowait())
                drained = True
        except queue.Empty:
            pass
        if drained:
            self._recent = self._recent[: self.max_rows]
            self._render()
        if self._stop.is_set():
            root.destroy()
            return
        root.after(200, lambda: self._poll(root))

    def _load_icon(self, item_name: str):
        if not self.icon_dir:
            return None
        slug = icon_slug(item_name)
        if slug in self._icons:
            return self._icons[slug]
        png = self.icon_dir / f"{slug}.png"
        if not png.exists():
            self._icons[slug] = None
            return None
        try:
            import tkinter as tk
            img = tk.PhotoImage(file=str(png))
            self._icons[slug] = img
            return img
        except Exception:  # noqa: BLE001
            self._icons[slug] = None
            return None

    def _render(self) -> None:
        for i, (icon_lbl, text_lbl) in enumerate(self._rows):
            if i < len(self._recent):
                e = self._recent[i]
                img = self._load_icon(e.item)
                icon_lbl.configure(image=img if img else "")
                icon_lbl.image = img
                sub = " · ".join(p for p in (e.source, e.location) if p)
                text_lbl.configure(
                    text=e.item + (f"\n{sub}" if sub else ""),
                    fg=_FG if i == 0 else _DIM,
                )
            else:
                icon_lbl.configure(image="")
                text_lbl.configure(text="")


# --------------------------------------------------------------------------- #
# Manual demo:  python -m client.overlay
# --------------------------------------------------------------------------- #

def _demo() -> None:
    import time
    ov = Overlay(max_rows=5)
    ov.start()
    demo = [
        ("Celcetan Panacea", "Hugo", "Wailing Blue: 2F Path 1"),
        ("Beast Medallion", "Hugo", "Wailing Blue: 4F Lower (Medal)"),
        ("Ventus Bracelet", "Alice", "Hollow Knight: Greenpath"),
        ("Bronze Key", "Hugo", "Wailing Blue: 2F Path 1"),
        ("Roda Fruit", "Hugo", "Statue: 6F Save"),
        ("Devil Medallion", "Bob", "SM: Crateria"),
    ]
    for name, src, loc in demo:
        ov.push(name, src, loc)
        time.sleep(1.2)
    time.sleep(4)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _demo()
