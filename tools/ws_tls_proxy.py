"""Local ws:// -> wss:// bridge for the Ys Origin mod.

The in-game mod's embedded AP client is built without TLS (WSWRAP_NO_SSL), so it
can only reach plain ws:// servers. Public hosting (archipelago.gg) is wss:// only.
This proxy listens on a local ws:// port and forwards every frame, both ways, to
the real wss:// server (terminating TLS here). Point the mod at the proxy:

    yso_ap.cfg:  host=127.0.0.1   port=38281

Run:
    python tools/ws_tls_proxy.py archipelago.gg:62493 [listen_port=38281]

Needs the `websockets` package (present in an Archipelago venv).
"""
import asyncio
import ssl
import sys

import websockets


def parse_upstream(arg: str):
    host, _, port = arg.partition(":")
    return host, int(port)


async def _pump(src, dst):
    async for msg in src:
        await dst.send(msg)


async def make_handler(up_host, up_port):
    uri = f"wss://{up_host}:{up_port}"
    ctx = ssl.create_default_context()

    async def handler(client, *_):
        print("client connected -> dialing", uri)
        try:
            async with websockets.connect(uri, ssl=ctx, max_size=None,
                                          open_timeout=15) as server:
                a = asyncio.create_task(_pump(client, server))
                b = asyncio.create_task(_pump(server, client))
                _, pending = await asyncio.wait({a, b},
                                                return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
        except Exception as e:
            print("session ended:", type(e).__name__, str(e)[:100])
        finally:
            print("client disconnected")

    return handler


async def main():
    if len(sys.argv) < 2:
        print("usage: ws_tls_proxy.py <host:port> [listen_port]")
        return
    up_host, up_port = parse_upstream(sys.argv[1])
    listen_port = int(sys.argv[2]) if len(sys.argv) > 2 else 38281
    handler = await make_handler(up_host, up_port)
    async with websockets.serve(handler, "127.0.0.1", listen_port, max_size=None):
        print(f"proxy listening ws://127.0.0.1:{listen_port} -> "
              f"wss://{up_host}:{up_port}")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
