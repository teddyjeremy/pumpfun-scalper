"""Executors: paper (default), simulate (unsigned simulateTransaction), live (signed, real money).

Live/simulate trade SOL-paired coins only by default (TRADEABLE_QUOTES). Cashback coins and completed curves are
skipped (v3 does not support cashback coins). Buys use buy_exact_quote_in_v3 with min_tokens_out from the slippage
limit; sells use sell_v3 for the whole balance plus closeAccount to recover token-account rent, with escalating
slippage on retries (an exit must get out).
"""
from __future__ import annotations

import asyncio
import json
import os
import time

from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from . import pumpix as P
from .events import events_from_logs
from .rpc import RPC

LAMPORTS = 1_000_000_000


def load_keypair_from_env() -> Keypair:
    """WALLET_PRIVATE_KEY: base58 64-byte secret or a JSON array of 64 ints. Never logged."""
    v = os.environ.get("WALLET_PRIVATE_KEY", "").strip()
    if not v:
        raise RuntimeError("WALLET_PRIVATE_KEY not set")
    if v.startswith("["):
        return Keypair.from_bytes(bytes(json.loads(v)))
    return Keypair.from_base58_string(v)


class PaperExecutor:
    """Fills are produced by the engine (next trade >= trigger+1s). Nothing is sent."""
    mode = "paper"

    async def buy(self, coin, stake_usd, sol_usd):
        return {"ok": True, "paper": True}

    async def sell(self, coin, sol_usd):
        return {"ok": True, "paper": True}


class ChainExecutor:
    """Builds real transactions. mode='simulate' -> unsigned simulateTransaction; mode='live' -> sign + send."""

    def __init__(self, cfg, log, mode: str):
        assert mode in ("simulate", "live")
        self.cfg, self.log, self.mode = cfg, log, mode
        self.rpc = RPC(cfg.rpc_http_url)
        self.senders = [RPC(u) for u in cfg.send_rpc_urls]
        self.kp: Keypair | None = None
        if mode == "live":
            self.kp = load_keypair_from_env()
            self.user = self.kp.pubkey()
        else:
            self.user = Pubkey.from_string(cfg.sim_wallet_pubkey)
        self.token_prog_cache: dict[str, Pubkey] = {}
        self.holdings: dict[str, dict] = {}

    async def wallet_usd(self, sol_usd: float) -> float:
        return await self.rpc.balance(str(self.user)) / LAMPORTS * sol_usd

    async def _token_program(self, mint: str) -> Pubkey:
        if mint not in self.token_prog_cache:
            _, owner = await self.rpc.account_data(mint)
            self.token_prog_cache[mint] = Pubkey.from_string(owner) if owner else P.TOKEN_2022
        return self.token_prog_cache[mint]

    async def _curve(self, mint: str) -> dict:
        data, _ = await self.rpc.account_data(str(P.bonding_curve_pda(mint)))
        if not data:
            raise RuntimeError("bonding curve not found")
        return P.parse_bonding_curve(data)

    def _tx(self, ixs, blockhash: str) -> VersionedTransaction:
        msg = MessageV0.try_compile(self.user, ixs, [], Hash.from_string(blockhash))
        if self.mode == "live":
            return VersionedTransaction(msg, [self.kp])
        # unsigned placeholder signature for simulation (sigVerify=false)
        from solders.signature import Signature
        return VersionedTransaction.populate(msg, [Signature.default()])

    def _budget(self):
        ixs = P.compute_budget(self.cfg.cu_limit, self.cfg.priority_micro_lamports)
        return ixs

    def _tip(self):
        if self.cfg.jito_url and self.cfg.jito_tip_lamports > 0:
            return [P.jito_tip(self.user, self.cfg.jito_tip_lamports, int(time.time()) % 8)]
        return []

    async def _submit(self, tx: VersionedTransaction) -> dict:
        raw = bytes(tx)
        if self.mode == "simulate":
            r = await self.rpc.simulate_unsigned(raw)
            v = (r or {}).get("value") or {}
            return {"ok": v.get("err") is None, "simulated": True, "err": v.get("err"), "units": v.get("unitsConsumed"),
                    "logs_tail": (v.get("logs") or [])[-6:]}
        sig = str(tx.signatures[0])
        sends = [self.rpc.send_raw(raw)] + [s.send_raw(raw) for s in self.senders]
        if self.cfg.jito_url:
            sends.append(self._send_jito(raw))
        await asyncio.gather(*sends, return_exceptions=True)
        t0 = time.time()
        while time.time() - t0 < 20:
            st = await self.rpc.sig_status(sig)
            if st and st.get("confirmationStatus") in ("processed", "confirmed", "finalized"):
                if st.get("err"):
                    return {"ok": False, "sig": sig, "err": st["err"]}
                return {"ok": True, "sig": sig}
            await asyncio.sleep(0.4)
        return {"ok": False, "sig": sig, "err": "not_confirmed_20s"}

    async def _send_jito(self, raw: bytes):
        import base64
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
            body = {"jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
                    "params": [base64.b64encode(raw).decode(), {"encoding": "base64"}]}
            async with s.post(self.cfg.jito_url, json=body) as r:
                return await r.text()

    async def _fill_from_tx(self, sig: str) -> dict | None:
        for _ in range(10):
            tx = await self.rpc.get_tx(sig)
            if tx:
                evs = [e for k, e in events_from_logs((tx.get("meta") or {}).get("logMessages") or []) if k == "trade"]
                mine = [e for e in evs if e.get("user") == str(self.user)]
                if mine:
                    e = mine[-1]
                    return {"sol": e["sol_amount"], "tokens": e["token_amount"], "fee_lamports": (tx["meta"] or {}).get("fee")}
                return None
            await asyncio.sleep(0.5)
        return None

    async def buy(self, coin, stake_usd: float, sol_usd: float) -> dict:
        if coin.quote_mint not in self.cfg.tradeable_quotes:
            return {"ok": False, "err": f"quote_not_tradeable:{coin.quote_mint}"}
        curve = await self._curve(coin.mint)
        if curve["complete"]:
            return {"ok": False, "err": "curve_complete"}
        if curve["is_cashback_coin"]:
            return {"ok": False, "err": "cashback_coin_v3_unsupported"}
        btp = await self._token_program(coin.mint)
        spend = int(stake_usd / sol_usd * LAMPORTS)
        fee_bps = coin.fee_bps or 125
        exp = P.expected_tokens_out(spend, curve["virtual_quote_reserves"], curve["virtual_token_reserves"], fee_bps)
        min_out = exp * (10_000 - self.cfg.buy_slippage_bps) // 10_000
        ixs = self._budget() + [P.create_ata_idempotent(self.user, self.user, coin.mint, btp),
                                P.buy_exact_quote_in_v3(self.user, coin.mint, spend, max(1, min_out), "SOL", btp, P.TOKEN_PROGRAM,
                                                        int(time.time()) % 8)] + self._tip()
        tx = self._tx(ixs, await self.rpc.latest_blockhash())
        r = await self._submit(tx)
        r.update(spend_lamports=spend, expected_tokens=exp, min_tokens_out=min_out)
        if r.get("ok") and self.mode == "live":
            fill = await self._fill_from_tx(r["sig"])
            tokens = fill["tokens"] if fill else await self.rpc.token_balance(str(P.ata(self.user, coin.mint, btp)))
            self.holdings[coin.mint] = {"tokens": tokens, "btp": btp, "spend": spend}
            r["fill"] = fill
        elif r.get("ok"):
            self.holdings[coin.mint] = {"tokens": max(1, min_out), "btp": btp, "spend": spend}
        return r

    async def sell(self, coin, sol_usd: float) -> dict:
        h = self.holdings.get(coin.mint)
        if not h:
            return {"ok": False, "err": "no_holding"}
        btp = h["btp"]
        if self.mode == "live":
            bal = await self.rpc.token_balance(str(P.ata(self.user, coin.mint, btp)))
            h["tokens"] = bal or h["tokens"]
        last = None
        for slip in self.cfg.sell_slippage_bps:
            curve = await self._curve(coin.mint)
            exp = P.expected_quote_out(h["tokens"], curve["virtual_quote_reserves"], curve["virtual_token_reserves"], coin.fee_bps or 125)
            min_out = max(0, exp * (10_000 - slip) // 10_000)
            ixs = self._budget() + [P.sell_v3(self.user, coin.mint, h["tokens"], min_out, "SOL", btp, P.TOKEN_PROGRAM, int(time.time()) % 8),
                                    P.close_token_account(self.user, coin.mint, btp)] + self._tip()
            tx = self._tx(ixs, await self.rpc.latest_blockhash())
            last = await self._submit(tx)
            last.update(expected_lamports=exp, min_out=min_out, slippage_bps=slip)
            if last.get("ok"):
                if self.mode == "live":
                    last["fill"] = await self._fill_from_tx(last["sig"])
                self.holdings.pop(coin.mint, None)
                return last
            self.log(f"sell retry {coin.mint[:8]} slip={slip}bps err={last.get('err')}")
        return last or {"ok": False}
