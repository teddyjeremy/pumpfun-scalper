"""Decode Pump.fun program events from `Program data:` log lines (Anchor events, borsh).

Layouts follow the public IDL (github.com/pump-fun/pump-public-docs idl/pump.json, Nov 2026) and were
checked against live events by the research paper trader (quote mint after ix_name: +37 bytes).
Non-SOL-quoted curves (USDC, stock tokens, pump coins as quote) carry the trade in quote_* fields and
have virtual_sol_reserves == 0.
"""
from __future__ import annotations

import base64
import struct

import base58

TRADE_EVENT_DISC = bytes.fromhex("bddb7fd34ee661ee")
CREATE_EVENT_DISC = bytes.fromhex("1b72a94ddeeb6376")
COMPLETE_EVENT_DISC = bytes.fromhex("5f72619cd42e9808")
TRADE_B64 = base64.b64encode(TRADE_EVENT_DISC)[:8].decode()
CREATE_B64 = base64.b64encode(CREATE_EVENT_DISC)[:8].decode()
COMPLETE_B64 = base64.b64encode(COMPLETE_EVENT_DISC)[:8].decode()
DEFAULT_PK = "11111111111111111111111111111111"
WSOL = "So11111111111111111111111111111111111111112"
SOL = "SOL"


def b58(raw: bytes) -> str:
    return base58.b58encode(raw).decode()


class _R:
    def __init__(self, raw: bytes, off: int = 8):
        self.raw, self.o = raw, off

    def take(self, n):
        if self.o + n > len(self.raw):
            raise ValueError("short")
        b = self.raw[self.o:self.o + n]
        self.o += n
        return b

    def u8(self): return self.take(1)[0]
    def bool(self): return self.take(1)[0] == 1
    def u16(self): return struct.unpack("<H", self.take(2))[0]
    def u32(self): return struct.unpack("<I", self.take(4))[0]
    def u64(self): return struct.unpack("<Q", self.take(8))[0]
    def i64(self): return struct.unpack("<q", self.take(8))[0]
    def pk(self): return b58(self.take(32))

    def string(self):
        n = self.u32()
        if n > 1024:
            raise ValueError("bad string")
        return self.take(n).decode("utf-8", "replace")


def decode_trade(raw: bytes) -> dict | None:
    if raw[:8] != TRADE_EVENT_DISC:
        return None
    r = _R(raw)
    try:
        ev = {"mint": r.pk(), "sol_amount": r.u64(), "token_amount": r.u64(), "is_buy": r.bool(), "user": r.pk(),
              "timestamp": r.i64(), "virtual_sol_reserves": r.u64(), "virtual_token_reserves": r.u64(),
              "real_sol_reserves": r.u64(), "real_token_reserves": r.u64(), "fee_recipient": r.pk(),
              "fee_bps": r.u64(), "fee": r.u64(), "creator": r.pk(), "creator_fee_bps": r.u64(), "creator_fee": r.u64()}
    except ValueError:
        return None
    ev["quote_mint"] = SOL if ev["virtual_sol_reserves"] > 0 else None
    try:  # extended tail (present on current program versions)
        r.bool(); r.u64(); r.u64(); r.u64(); r.i64()           # track_volume, unclaimed, claimed, cur vol, last ts
        ev["ix_name"] = r.string()
        ev["mayhem_mode"] = r.bool()
        r.u64(); r.u64()                                       # cashback bps, cashback
        r.u64(); r.u64()                                       # buyback bps, buyback fee
        n = r.u32()
        if n > 64:
            raise ValueError("bad vec")
        r.take(34 * n)                                         # shareholders (pubkey, u16)
        qm = r.pk()
        qamt, vq, rq = r.u64(), r.u64(), r.u64()
        if ev["virtual_sol_reserves"] == 0 and qm not in (DEFAULT_PK, WSOL) and vq > 0:
            ev.update(quote_mint=qm, quote_amount=qamt, virtual_quote_reserves=vq, real_quote_reserves=rq)
        elif ev["virtual_sol_reserves"] == 0 and qm == WSOL and vq > 0:
            ev.update(quote_mint=SOL, sol_amount=qamt, virtual_sol_reserves=vq, real_sol_reserves=rq)
    except ValueError:
        pass
    return ev


def decode_create(raw: bytes) -> dict | None:
    if raw[:8] != CREATE_EVENT_DISC:
        return None
    r = _R(raw)
    try:
        ev = {"name": r.string()[:40], "symbol": r.string()[:20], "uri": r.string()[:200], "mint": r.pk(),
              "bonding_curve": r.pk(), "user": r.pk(), "creator": r.pk(), "timestamp": r.i64()}
    except ValueError:
        return None
    try:
        r.u64(); r.u64(); r.u64(); r.u64()
        ev["token_program"] = r.pk()
        ev["is_mayhem_mode"] = r.bool()
        ev["is_cashback_enabled"] = r.bool()
        qm = r.pk()
        ev["quote_mint"] = SOL if qm in (DEFAULT_PK, WSOL) else qm
    except ValueError:
        pass
    return ev


def decode_complete(raw: bytes) -> dict | None:
    if raw[:8] != COMPLETE_EVENT_DISC or len(raw) < 8 + 96:
        return None
    return {"user": b58(raw[8:40]), "mint": b58(raw[40:72]), "bonding_curve": b58(raw[72:104])}


def events_from_logs(logs: list[str]) -> list[tuple[str, dict]]:
    out = []
    for line in logs or []:
        if not line.startswith("Program data: "):
            continue
        b = line[14:]
        if not (b.startswith(TRADE_B64) or b.startswith(CREATE_B64) or b.startswith(COMPLETE_B64)):
            continue
        try:
            raw = base64.b64decode(b)
        except Exception:
            continue
        d = raw[:8]
        if d == TRADE_EVENT_DISC:
            ev = decode_trade(raw)
            if ev:
                out.append(("trade", ev))
        elif d == CREATE_EVENT_DISC:
            ev = decode_create(raw)
            if ev:
                out.append(("create", ev))
        else:
            ev = decode_complete(raw)
            if ev:
                out.append(("complete", ev))
    return out


def spot_quote_per_token(vq: int, vt: int, quote_decimals: int = 9) -> float:
    """Curve spot in quote units per whole token (tokens have 6 decimals)."""
    return (vq / 10 ** quote_decimals) / (vt / 1e6) if vq and vt else 0.0
