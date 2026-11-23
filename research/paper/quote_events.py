"""Quote-aware Pump.fun TradeEvent decoding + quote-token USD prices (PAPER TRADER ONLY; research, no trading).

Pump curves can be quoted in SOL or in another SPL token (USDC/USDT, tokenized stocks like SPCX, LSTs,
other tokens). For non-SOL curves the TradeEvent's SOL fields are 0 and the trade is carried by the
quote fields that follow the ix_name string:
    u32 name_len @258, name @262, then (o = 262+name_len):
    o+37 quote_mint (32), o+69 quote_amount u64, o+77 virtual_quote_reserves u64, o+85 real_quote_reserves u64
(verified on live events: for SOL curves quote_mint = default pubkey, quote_amount == sol_amount and
virtual_quote_reserves == virtual_sol_reserves). Events whose SOL reserves are > 0 use the SOL path unchanged.

Non-SOL trades are converted to SOL-equivalent lamports (amount and virtual reserve x quote_usd / sol_usd),
so the trader's constant-product fills and USD stats work unchanged.
"""
from __future__ import annotations

import struct
import time

import requests

import rules as R

SOL = "SOL"
DEFAULT_PK = "11111111111111111111111111111111"
WSOL = "So11111111111111111111111111111111111111112"
STABLES = {  # $1 by definition
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": ("USDC", 6),
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": ("USDT", 6),
    "USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB": ("USD1", 6),
    "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo": ("PYUSD", 6),
}
KNOWN_SYMBOLS = {"SPCXxcqXj6e5dJDVNovHN8744zkbhM2bYudU45BimGb": "SPCX",
                 "he1iusmfkpAdwvxLNGV8Y1iSbj4rUy6yMhEA3fotn9A": "hSOL",
                 "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn": "PUMP"}
QUOTE_TAIL = 125          # bytes after the name in the standard (non-extended) layout
MIN_LIQ_USD = 25_000.0    # Jupiter liquidity floor for a quote price to count as reliable
MAX_AGE_S = 180.0         # a quote price older than this is stale
MAX_DISAGREE = 0.05       # Jupiter vs DexScreener disagreement that makes a price unreliable


def decode_trade(raw: bytes) -> dict | None:
    """R.parse_trade_event + quote fields. Adds quote_mint ('SOL' for SOL curves), quote_layout."""
    ev = R.parse_trade_event(raw)
    if not ev:
        return None
    if ev["virtual_sol_reserves"] > 0:
        ev["quote_mint"] = SOL
        ev["quote_layout"] = "sol"
        return ev
    ev["quote_mint"] = None
    ev["quote_layout"] = "unknown"
    if len(raw) < 262:
        return ev
    nl = struct.unpack_from("<I", raw, 258)[0]
    o = 262 + nl
    if nl > 64 or len(raw) - o != QUOTE_TAIL:
        return ev
    qmint = R.b58(raw[o + 37:o + 69])
    qamt, vq, rq = struct.unpack_from("<QQQ", raw, o + 69)
    if qmint in (DEFAULT_PK, WSOL) or vq <= 0:
        return ev
    ev.update(quote_mint=qmint, quote_amount=qamt, virtual_quote_reserves=vq, real_quote_reserves=rq,
              quote_layout="quote", ix_name=raw[262:o].decode("utf-8", "replace"))
    return ev


def to_sol_equiv(ev: dict, quote_usd: float, decimals: int, sol_usd: float) -> dict:
    """Rewrite a quote-token event's SOL fields as SOL-equivalent lamports (in place)."""
    f = quote_usd / sol_usd * 1e9 / (10 ** decimals)
    ev["sol_amount"] = int(round(ev["quote_amount"] * f))
    ev["virtual_sol_reserves"] = int(round(ev["virtual_quote_reserves"] * f))
    ev["real_sol_reserves"] = int(round(ev.get("real_quote_reserves", 0) * f))
    ev["quote_usd"] = quote_usd
    return ev


class QuotePrices:
    """USD price per quote mint. Stables = $1. Others: Jupiter price v3 (price, decimals, liquidity) refreshed
    every `every` s for mints in use, cross-checked with DexScreener when it has a pair. No keys, no RPC."""

    def __init__(self, every: float = 30.0, log=None):
        self.every = every
        self.log = log or (lambda m: None)
        self.p: dict[str, dict] = {}      # mint -> {usd, decimals, ts, liq, src, dex_usd, reason}
        self.want: dict[str, float] = {}  # mint -> last time asked for
        self.syms: dict[str, str] = dict(KNOWN_SYMBOLS)

    def symbol(self, mint):
        if mint in (SOL, None):
            return mint or "unknown"
        if mint in STABLES:
            return STABLES[mint][0]
        return self.syms.get(mint) or mint[:6]

    def get(self, mint: str):
        """(usd, decimals) if a reliable fresh price exists, else None. Registers interest."""
        self.want[mint] = time.time()
        if mint in STABLES:
            return 1.0, STABLES[mint][1]
        q = self.p.get(mint)
        if q and q.get("usd") and q.get("decimals") is not None and not q.get("reason") \
                and time.time() - q["ts"] <= MAX_AGE_S:
            return q["usd"], q["decimals"]
        return None

    def status(self, mint):
        q = self.p.get(mint) or {}
        return q.get("reason") or ("pending" if not q else "stale")

    def refresh(self, mints: list[str]):
        mints = [m for m in mints if m not in STABLES]
        if not mints:
            return
        t = time.time()
        jup = {}
        try:
            r = requests.get("https://lite-api.jup.ag/price/v3?ids=" + ",".join(mints[:50]), timeout=8)
            jup = r.json() or {}
        except Exception as e:  # transient: keep previous prices (they go stale after MAX_AGE_S on their own)
            self.log(f"quote price: jupiter error {type(e).__name__}")
            for m in mints[:50]:
                self.p.setdefault(m, {"ts": 0, "reason": "no_jupiter_price"})["try"] = t
            return
        for m in mints[:50]:
            j = jup.get(m) or {}
            q = self.p.setdefault(m, {"ts": 0})
            usd, dec, liq = j.get("usdPrice"), j.get("decimals"), j.get("liquidity")
            dex = None
            try:
                if m not in self.syms or (t - q.get("dex_ts", 0) > 120):
                    d = requests.get(f"https://api.dexscreener.com/tokens/v1/solana/{m}", timeout=8).json() or []
                    pairs = [x for x in d if (x.get("baseToken") or {}).get("address") == m and x.get("priceUsd")]
                    if pairs:
                        best = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd") or 0)
                        dex = float(best["priceUsd"])
                        self.syms.setdefault(m, best["baseToken"].get("symbol") or m[:6])
                    q["dex_ts"] = t
                    q["dex_usd"] = dex
                else:
                    dex = q.get("dex_usd")
            except Exception:
                dex = q.get("dex_usd")
            reason = None
            if not usd or dec is None:
                reason = "no_jupiter_price"
            elif (liq or 0) < MIN_LIQ_USD:
                reason = f"low_liquidity:{(liq or 0):.0f}"
            elif dex and abs(dex / float(usd) - 1) > MAX_DISAGREE:
                reason = f"sources_disagree:jup={float(usd):.6g},dex={dex:.6g}"
            q.update(usd=float(usd) if usd else None, decimals=dec, liq=liq, src="jupiter_v3",
                     reason=reason, ts=t if usd else q.get("ts", 0))
            if reason and q.get("_last_reason") != reason:
                self.log(f"quote price {self.symbol(m)} {m[:8]} unreliable: {reason}")
            q["_last_reason"] = reason

    def due(self):
        """Mints that should be (re)fetched now."""
        t = time.time()
        out = []
        for m, w in list(self.want.items()):
            if m in STABLES:
                continue
            if t - w > 900:                    # nobody asked for 15 min
                self.want.pop(m, None)
                continue
            q = self.p.get(m)
            if q is None or t - q.get("ts", 0) >= self.every or (q.get("reason") and t - q.get("try", 0) >= 15):
                out.append(m)
                if q is not None:
                    q["try"] = t
        return out
