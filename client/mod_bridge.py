"""Localhost socket bridge to the in-game native mod (mod/src/hook_bridge.cpp).

The mod intercepts every g_flags grant at the real VM store and talks to us over
127.0.0.1 with a tiny line protocol:

    Mod -> us :  "G <idx> <val>"   a g_flags grant happened (idx hex, val dec)
                 "C <flag>"        a registered randomized-location flag fired
    us  -> Mod:  "L <flag>"        register <flag> (hex) as a randomized loc
                 "I <idx>"         suppress vanilla item <idx> (hex)
                 "U"               unregister all
                 "V <idx> <count>" grant a received AP item (give semantics)

This lets the mod do the in-game half of the randomizer (suppress vanilla,
detect checks, grant items) while the Python client keeps doing the Archipelago
network half. Pure stdlib; runs its own reader thread.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable, Optional

HOST = "127.0.0.1"
PORT = 43673


class ModBridge:
    """Client side of the mod bridge. Connects with retry, reads grant/check
    events on a background thread, and sends registration / give commands.

    ``on_check(flag_idx:int)`` fires when the mod reports a registered location
    flag flipped (a check). ``on_grant(idx:int, val:int)`` fires for every grant
    (optional; useful for logging / detection of other state)."""

    def __init__(self, on_check: Optional[Callable[[int], None]] = None,
                 on_grant: Optional[Callable[[int, int], None]] = None):
        self.on_check = on_check
        self.on_grant = on_grant
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.connected = False

    # -- lifecycle ---------------------------------------------------------- #

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="ModBridge",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass

    # -- sending ------------------------------------------------------------ #

    def _send(self, line: str) -> None:
        with self._lock:
            s = self._sock
        if not s:
            return
        try:
            s.sendall((line + "\n").encode("ascii"))
        except OSError:
            pass

    def register_location(self, flag_idx: int) -> None:
        self._send(f"L {flag_idx:X}")

    def suppress_item(self, item_idx: int) -> None:
        self._send(f"I {item_idx:X}")

    def unregister_all(self) -> None:
        self._send("U")

    def give_item(self, item_idx: int, count: int = 1) -> None:
        self._send(f"V {item_idx:X} {count}")

    # -- reader loop -------------------------------------------------------- #

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                s = socket.create_connection((HOST, PORT), timeout=2)
            except OSError:
                time.sleep(1.0)  # mod not up yet; retry
                continue
            s.settimeout(0.5)
            with self._lock:
                self._sock = s
            self.connected = True
            if self.on_connect:
                try:
                    self.on_connect()
                except Exception:  # noqa: BLE001
                    pass
            buf = b""
            while not self._stop.is_set():
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    self._dispatch(raw.decode("ascii", "replace").strip())
            self.connected = False
            with self._lock:
                self._sock = None
            try:
                s.close()
            except OSError:
                pass

    # Optional callback fired each time we (re)connect to the mod.
    on_connect: Optional[Callable[[], None]] = None

    def _dispatch(self, line: str) -> None:
        if not line:
            return
        tag = line[0]
        try:
            if tag == "C":
                flag = int(line[2:].strip(), 16)
                if self.on_check:
                    self.on_check(flag)
            elif tag == "G":
                parts = line[2:].split()
                if len(parts) == 2 and self.on_grant:
                    self.on_grant(int(parts[0], 16), int(parts[1]))
        except ValueError:
            pass


# Quick standalone test: print grants/checks, optionally register a flag/item.
#   python -m client.mod_bridge
if __name__ == "__main__":
    import sys

    def _grant(idx, val):
        print(f"  grant g_flags[0x{idx:X}] = {val}")

    def _check(flag):
        print(f"CHECK location flag 0x{flag:X} fired")

    b = ModBridge(on_check=_check, on_grant=_grant)

    def _hello():
        print("connected to mod bridge")
        # demo: suppress Panacea (0x59), watch the first-2F-chest box flag (0x12E)
        b.suppress_item(0x59)
        b.register_location(0x12E)
        print("registered: suppress item 0x59, watch flag 0x12E")

    b.on_connect = _hello
    b.start()
    print(f"waiting for mod on {HOST}:{PORT} ... (Ctrl-C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        b.stop()
