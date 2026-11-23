"""SOL/USD and quote-token USD prices (no keys). Stables are $1."""
from __future__ import annotations

import asyncio
import time

import aiohttp

STABLES = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": ("USDC", 6),
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": ("USDT", 6),
    "USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB": ("USD1", 6),
    "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo": ("PYUSD", 6),
}
MIN_LIQ_USD = 25_000.0
MAX_AGE_S = 180.0


class Prices:
    def __init__(self, log=print):
        self.sol_usd: float | None = None
        self.sol_ts = 0.0
        self.q: dict[str, dict] = {}
        self.want: dict[str, float] = {}
        self.log = log

    def quote(self, mint: str):
        """(usd, decimals) for a quote mint, or None if no fresh reliable price."""
        if mint == "SOL":
            return (self.sol_usd, 9) if self.sol_usd else None
        if mint in STABLES:
            return 1.0, STABLES[mint][1]
        self.want[mint] = time.time()
        q = self.q.get(mint)
        if q and q.get("usd") and q.get("dec") is not None and time.time() - q["ts"] < MAX_AGE_S and q.get("liq", 0) >= MIN_LIQ_USD:
            return q["usd"], q["dec"]
        return None

    async def run(self):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            while True:
                await self._sol(s)
                await self._quotes(s)
                await asyncio.sleep(10)

    async def _sol(self, s):
        srcs = [("https://api.binance.com/api/v3/ticker/price?symbol=SOLUSDT", lambda j: j["price"]),
                ("https://api.coinbase.com/v2/prices/SOL-USD/spot", lambda j: j["data"]["amount"]),
                ("https://api.kraken.com/0/public/Ticker?pair=SOLUSD", lambda j: list(j["result"].values())[0]["c"][0])]
        for url, get in srcs:
            try:
                async with s.get(url) as r:
                    px = float(get(await r.json()))
                    if 1 < px < 10_000:
                        self.sol_usd, self.sol_ts = px, time.time()
                        return
            except Exception:
                continue
        self.log("prices: all SOL/USD sources failed")

    async def _quotes(self, s):
        mints = [m for m, t in self.want.items() if time.time() - t < 900 and m not in STABLES][:50]
        if not mints:
            return
        try:
            async with s.get("https://lite-api.jup.ag/price/v3?ids=" + ",".join(mints)) as r:
                j = await r.json()
            for m in mints:
                x = (j or {}).get(m) or {}
                if x.get("usdPrice"):
                    self.q[m] = {"usd": float(x["usdPrice"]), "dec": x.get("decimals"), "liq": x.get("liquidity") or 0, "ts": time.time()}
        except Exception as e:
            self.log(f"prices: jupiter error {type(e).__name__}")
