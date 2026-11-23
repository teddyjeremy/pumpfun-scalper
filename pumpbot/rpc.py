"""Minimal async Solana JSON-RPC client. URLs come from env; they are never logged unredacted."""
from __future__ import annotations

import base64
import itertools

import aiohttp

from .config import redact


class RPC:
    def __init__(self, url: str, session: aiohttp.ClientSession | None = None):
        self.url = url
        self.s = session
        self._id = itertools.count(1)

    async def _sess(self):
        if self.s is None:
            self.s = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        return self.s

    async def call(self, method: str, params: list):
        s = await self._sess()
        async with s.post(self.url, json={"jsonrpc": "2.0", "id": next(self._id), "method": method, "params": params}) as r:
            j = await r.json(content_type=None)
        if "error" in j:
            raise RuntimeError(f"rpc {method} error {j['error'].get('code')}: {j['error'].get('message')} @ {redact(self.url)}")
        return j.get("result")

    async def account_data(self, addr: str) -> tuple[bytes | None, str | None]:
        r = await self.call("getAccountInfo", [addr, {"encoding": "base64", "commitment": "processed"}])
        v = (r or {}).get("value")
        if not v:
            return None, None
        return base64.b64decode(v["data"][0]), v["owner"]

    async def latest_blockhash(self) -> str:
        r = await self.call("getLatestBlockhash", [{"commitment": "processed"}])
        return r["value"]["blockhash"]

    async def balance(self, addr: str) -> int:
        r = await self.call("getBalance", [addr, {"commitment": "processed"}])
        return int(r["value"])

    async def token_balance(self, ata_addr: str) -> int:
        try:
            r = await self.call("getTokenAccountBalance", [ata_addr, {"commitment": "processed"}])
            return int(r["value"]["amount"])
        except RuntimeError:
            return 0

    async def send_raw(self, raw_tx: bytes) -> str:
        return await self.call("sendTransaction", [base64.b64encode(raw_tx).decode(),
                                                   {"encoding": "base64", "skipPreflight": True, "maxRetries": 0}])

    async def simulate_unsigned(self, raw_tx: bytes) -> dict:
        return await self.call("simulateTransaction", [base64.b64encode(raw_tx).decode(),
                                                       {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True,
                                                        "commitment": "processed"}])

    async def sig_status(self, sig: str) -> dict | None:
        r = await self.call("getSignatureStatuses", [[sig], {"searchTransactionHistory": False}])
        return (r or {}).get("value", [None])[0]

    async def get_tx(self, sig: str) -> dict | None:
        return await self.call("getTransaction", [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}])

    async def close(self):
        if self.s:
            await self.s.close()
