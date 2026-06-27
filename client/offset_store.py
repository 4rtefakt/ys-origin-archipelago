"""Persistence for reverse-engineered offsets.

Confirmed offsets discovered with ``tools/scan.py`` are written to a JSON
sidecar (``client/offsets.json``) instead of being hand-edited into
``offsets.py``. ``offsets.py`` loads this file at import and overlays it onto the
hard-coded defaults, so discoveries survive client restarts and can be committed
to version control as the project's growing offset map.

File schema::

    {
      "module": "yso_win.exe",
      "version": "1.1.1.0",
      "offsets": { "emerald_count": "0x6F1234", ... }
    }

Offset values may be written as hex strings ("0x...") or plain ints; both load.
This module has no dependency on ``offsets.py`` to avoid an import cycle.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("ys_origin.offset_store")

DEFAULT_PATH = Path(__file__).resolve().parent / "offsets.json"


def _coerce_int(value: object) -> Optional[int]:
    """Accept ints or hex/decimal strings; return None if unparseable."""
    if isinstance(value, bool):  # guard: bool is an int subclass
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            return None
    return None


@dataclass
class OffsetStore:
    module: str = "yso_win.exe"
    version: str = "1.1.1.0"
    offsets: dict[str, int] = field(default_factory=dict)
    path: Path = DEFAULT_PATH

    # -- loading ----------------------------------------------------------- #

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "OffsetStore":
        """Read the sidecar; return an empty store if absent or malformed."""
        store = cls(path=path)
        if not path.exists():
            return store
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("could not read %s (%s); ignoring", path, e)
            return store
        if not isinstance(data, dict):
            log.warning("%s is not a JSON object; ignoring", path)
            return store
        store.module = str(data.get("module", store.module))
        store.version = str(data.get("version", store.version))
        raw = data.get("offsets", {})
        if isinstance(raw, dict):
            for name, val in raw.items():
                coerced = _coerce_int(val)
                if coerced is None:
                    log.warning("offset %r has unparseable value %r; skipping",
                                name, val)
                else:
                    store.offsets[str(name)] = coerced
        return store

    # -- saving ------------------------------------------------------------ #

    def save(self) -> None:
        """Write the store back to disk, hex-formatted for readability."""
        payload = {
            "module": self.module,
            "version": self.version,
            "offsets": {k: f"0x{v:X}" for k, v in sorted(self.offsets.items())},
        }
        self.path.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )

    def set(self, name: str, offset: int) -> None:
        self.offsets[name] = offset
        self.save()

    def remove(self, name: str) -> bool:
        if name in self.offsets:
            del self.offsets[name]
            self.save()
            return True
        return False


def load_overrides(path: Path = DEFAULT_PATH) -> dict[str, int]:
    """Convenience: just the ``name -> offset`` map from the sidecar."""
    return OffsetStore.load(path).offsets
