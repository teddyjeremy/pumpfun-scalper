"""Websocket feed: logsSubscribe(mentions=[pump program]) on every FEED_WS_URLS endpoint at once, de-duplicated.

Running 2+ endpoints is the cheapest way to cut lag and survive one provider dropping. URLs may contain API keys;
they are only ever logged redacted.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict

import websockets

from .config import redact
from .events import events_from_logs

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


class Feed:
    def __init__(self, urls: list[str], on_events, log=print):
        self.urls, self.on_events, self.log = urls, on_events, log
        self.seen: OrderedDict = OrderedDict()
        self.last_msg: dict[str, float] = {}
        self.msgs: dict[str, int] = {}

    def _dedupe(self, sig: str) -> bool:
        if sig in self.seen:
            return False
        self.seen[sig] = 1
        if len(self.seen) > 200_000:
            for _ in range(20_000):
                self.seen.popitem(last=False)
        return True

    async def _one(self, url: str):
        name = redact(url)
        backoff = 1
        while True:
            try:
                async with websockets.connect(url, max_size=2 ** 24, ping_interval=20, ping_timeout=20, close_timeout=5) as ws:
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                                              "params": [{"mentions": [PUMP_PROGRAM]}, {"commitment": "processed"}]}))
                    self.log(f"feed connected: {name}")
                    backoff = 1
                    async for raw in ws:
                        recv = time.time()
                        self.last_msg[name] = recv
                        self.msgs[name] = self.msgs.get(name, 0) + 1
                        m = json.loads(raw)
                        res = (m.get("params") or {}).get("result")
                        if not res:
                            continue
                        v = res.get("value") or {}
                        if v.get("err") is not None:
                            continue
                        sig = v.get("signature")
                        if not sig or not self._dedupe(sig):
                            continue
                        evs = events_from_logs(v.get("logs") or [])
                        if evs:
                            self.on_events(evs, sig, (res.get("context") or {}).get("slot"), recv)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log(f"feed {name} dropped: {type(e).__name__}; reconnect in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def run(self):
        await asyncio.gather(*(self._one(u) for u in self.urls))
