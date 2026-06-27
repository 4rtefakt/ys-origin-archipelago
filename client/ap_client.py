"""Archipelago client for Ys Origin.

Subclasses Archipelago's :class:`CommonContext` (the standard base used by every
memory-driven AP client, e.g. ``worlds/sm``). It does **not** reimplement the
WebSocket layer — that is entirely handled by ``CommonClient``.

Two async loops run alongside the AP network loop:

  * :meth:`game_watcher` polls game memory every 500 ms, diffs the snapshot,
    and queues new ``LocationChecks`` for the server.
  * received items are applied in :meth:`on_package` (``ReceivedItems``) via
    :func:`client.game_state.apply_item`.

Run:  python -m client.ap_client
(requires the Archipelago source on PYTHONPATH; see README "Running the client".)
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Dict, Optional

from .game_state import (
    GameState,
    POLL_INTERVAL_S,
    apply_item,
    detect_checks,
    poll,
)
from .memory import ProcessMemory, MemoryError_
from .offsets import MODULE_NAME, OffsetNotMapped

log = logging.getLogger("ys_origin.client")

GAME_NAME = "Ys Origin"
ITEMS_HANDLING = 0b111  # full remote items (receive own + others' items)


# --------------------------------------------------------------------------- #
# Archipelago import shim
# --------------------------------------------------------------------------- #
#
# Archipelago is distributed as a source tree, not a pip package. We import its
# CommonClient symbols lazily so that the rest of this repo (memory layer,
# scanner, apworld stubs) can be imported and tested without Archipelago
# present. Point AP_ROOT at your Archipelago checkout, or run this module from
# within the Archipelago tree.

def _load_archipelago():
    try:
        from CommonClient import CommonContext, server_loop, gui_enabled  # type: ignore
        from NetUtils import ClientStatus  # type: ignore
        import Utils  # type: ignore
        return CommonContext, server_loop, gui_enabled, ClientStatus, Utils
    except ImportError as e:  # pragma: no cover - depends on external tree
        raise SystemExit(
            "Could not import Archipelago's CommonClient.\n"
            "Set the AP_ROOT environment variable to your Archipelago checkout, "
            "or run this from inside that tree. See README.\n"
            f"(import error: {e})"
        )


# --------------------------------------------------------------------------- #
# Context
# --------------------------------------------------------------------------- #


def build_context_class():
    """Return a YsOriginContext class bound to the loaded AP base classes.

    Done as a factory so importing this module never hard-fails when AP is
    absent — only :func:`main` triggers the import.
    """
    CommonContext, server_loop, gui_enabled, ClientStatus, Utils = _load_archipelago()

    class YsOriginContext(CommonContext):
        game = GAME_NAME
        items_handling = ITEMS_HANDLING
        command_processor = CommonContext.command_processor

        def __init__(self, server_address: Optional[str], password: Optional[str]):
            super().__init__(server_address, password)
            self.mem: Optional[ProcessMemory] = None
            self.prev_state: Optional[GameState] = None
            self.checked_signals: set[str] = set()
            self.applied_item_index: int = 0  # how many received items processed
            # signal-string -> AP location id; filled from slot data / datapackage
            self.location_signal_to_id: Dict[str, int] = {}

        # -- AP auth ------------------------------------------------------- #

        async def server_auth(self, password_requested: bool = False):
            if password_requested and not self.password:
                await super().server_auth(password_requested)
            await self.get_username()
            await self.send_connect()

        # -- AP package handling ------------------------------------------ #

        def on_package(self, cmd: str, args: dict):
            if cmd == "Connected":
                slot_data = args.get("slot_data", {}) or {}
                mapping = slot_data.get("location_signals", {})
                if isinstance(mapping, dict):
                    self.location_signal_to_id.update(
                        {str(k): int(v) for k, v in mapping.items()}
                    )
                log.info("connected to slot %s", args.get("slot"))
            elif cmd == "ReceivedItems":
                asyncio.create_task(self._apply_received_items())

        async def _apply_received_items(self):
            """Apply any received items we have not applied yet (idempotent)."""
            if self.mem is None:
                return
            items = list(self.items_received)
            while self.applied_item_index < len(items):
                net_item = items[self.applied_item_index]
                item_name = self.item_names.lookup_in_game(net_item.item) \
                    if hasattr(self, "item_names") else str(net_item.item)
                try:
                    did = apply_item(self.mem, item_name)
                    log.info("applied item #%d %r (write=%s)",
                             self.applied_item_index, item_name, did)
                except OffsetNotMapped as e:
                    log.error("cannot apply %r yet: %s", item_name, e)
                    # Stop; retry once the offset is mapped & client restarted.
                    return
                except MemoryError_ as e:
                    log.error("memory write failed for %r: %s", item_name, e)
                    return
                self.applied_item_index += 1

        # -- game connection ----------------------------------------------- #

        def ensure_attached(self) -> bool:
            if self.mem is not None and self.mem.is_alive():
                return True
            try:
                self.mem = ProcessMemory.attach(MODULE_NAME)
                log.info("attached to %s pid=%d base=0x%X",
                         MODULE_NAME, self.mem.pid, self.mem.base_address)
                self.prev_state = None
                return True
            except MemoryError_:
                self.mem = None
                return False

        async def send_location_signal(self, signal: str):
            """Map a game signal string to an AP location id and send it."""
            loc_id = self.location_signal_to_id.get(signal)
            if loc_id is None:
                log.debug("signal %r has no location id yet", signal)
                return
            if loc_id in self.locations_checked:
                return
            await self.send_msgs(
                [{"cmd": "LocationChecks", "locations": [loc_id]}]
            )
            log.info("sent LocationCheck %r -> %d", signal, loc_id)

    return YsOriginContext, server_loop, gui_enabled, ClientStatus


# --------------------------------------------------------------------------- #
# Game-watcher loop
# --------------------------------------------------------------------------- #


async def game_watcher(ctx) -> None:
    """Poll memory, diff, and forward new checks. Runs until ctx exits."""
    while not ctx.exit_event.is_set():
        await asyncio.sleep(POLL_INTERVAL_S)
        if not ctx.ensure_attached():
            continue
        try:
            state = poll(ctx.mem)
        except MemoryError_ as e:
            log.debug("poll failed: %s", e)
            continue
        if not state.valid:
            continue
        if ctx.prev_state is not None:
            for signal in detect_checks(ctx.prev_state, state):
                if signal not in ctx.checked_signals:
                    ctx.checked_signals.add(signal)
                    await ctx.send_location_signal(signal)
        ctx.prev_state = state
        # Re-apply received items in case we just (re)attached after a restart.
        await ctx._apply_received_items()


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


async def _amain(server: Optional[str], password: Optional[str],
                 slot: Optional[str] = None) -> None:
    YsOriginContext, server_loop, gui_enabled, _ = build_context_class()
    ctx = YsOriginContext(server, password)
    if slot:
        # Pre-set the slot name so get_username() auths headless (no prompt).
        ctx.username = slot
        ctx.auth = slot
    ctx.server_task = asyncio.create_task(server_loop(ctx), name="ServerLoop")

    watcher = asyncio.create_task(game_watcher(ctx), name="GameWatcher")
    try:
        await ctx.exit_event.wait()
    finally:
        watcher.cancel()
        if ctx.mem is not None:
            ctx.mem.close()
        await ctx.shutdown()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Allow `AP_ROOT` to point at the Archipelago checkout.
    import os
    ap_root = os.environ.get("AP_ROOT")
    if ap_root:
        sys.path.insert(0, ap_root)

    # usage: python -m client.ap_client <host:port> <slot> [password]
    server = sys.argv[1] if len(sys.argv) > 1 else None
    slot = sys.argv[2] if len(sys.argv) > 2 else None
    password = sys.argv[3] if len(sys.argv) > 3 else None
    try:
        asyncio.run(_amain(server, password, slot))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
