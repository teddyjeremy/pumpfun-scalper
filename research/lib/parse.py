"""Parse Pump.fun transactions into a consistent trade schema."""
from __future__ import annotations

import base64
import struct
from typing import Any, Optional

import base58

from lib.constants import (
    PUMP_PROGRAM, TRADE_EVENT_DISC, CREATE_EVENT_DISC, COMPLETE_EVENT_DISC,
    JITO_TIPS, TOKEN_DECIMALS, TOTAL_SUPPLY_UI, WSOL,
)

def b58(raw: bytes) -> str:
    return base58.b58encode(raw).decode()

def account_keys(tx: dict) -> list[str]:
    msg = ((tx or {}).get("transaction") or {}).get("message") or {}
    keys = []
    for k in msg.get("accountKeys") or []:
        if isinstance(k, dict):
            keys.append(k.get("pubkey") or "")
        else:
            keys.append(str(k))
    meta = (tx or {}).get("meta") or {}
    loaded = meta.get("loadedAddresses") or {}
    # If already merged (jsonParsed often does this), preBalances matches keys
    pre = meta.get("preBalances") or []
    if pre and len(keys) < len(pre):
        for group in ("writable", "readonly"):
            for a in loaded.get(group) or []:
                if isinstance(a, dict):
                    keys.append(a.get("pubkey") or a.get("address") or "")
                else:
                    keys.append(str(a))
    return keys

def signers_of(tx: dict) -> list[str]:
    msg = ((tx or {}).get("transaction") or {}).get("message") or {}
    out = []
    for k in msg.get("accountKeys") or []:
        if isinstance(k, dict) and k.get("signer"):
            out.append(k.get("pubkey"))
    if out:
        return out
    hdr = msg.get("header") or {}
    n = hdr.get("numRequiredSignatures") or 1
    keys = account_keys(tx)
    return keys[:n]

def fee_payer_of(tx: dict) -> Optional[str]:
    keys = account_keys(tx)
    return keys[0] if keys else None

def _u64(raw: bytes, off: int) -> tuple[int, int]:
    return struct.unpack_from("<Q", raw, off)[0], off + 8

def _i64(raw: bytes, off: int) -> tuple[int, int]:
    return struct.unpack_from("<q", raw, off)[0], off + 8

def parse_trade_event(raw: bytes) -> Optional[dict]:
    if len(raw) < 8 or raw[:8] != TRADE_EVENT_DISC:
        return None
    off = 8
    if len(raw) < 8 + 32 + 8 + 8 + 1 + 32:
        return None
    mint = b58(raw[off:off+32]); off += 32
    sol_amount, off = _u64(raw, off)
    token_amount, off = _u64(raw, off)
    is_buy = raw[off] == 1; off += 1
    user = b58(raw[off:off+32]); off += 32
    timestamp, off = _i64(raw, off)
    virtual_sol, off = _u64(raw, off)
    virtual_token, off = _u64(raw, off)
    real_sol, off = _u64(raw, off)
    real_token, off = _u64(raw, off)
    fee_recipient = b58(raw[off:off+32]); off += 32
    fee_bps, off = _u64(raw, off)
    fee, off = _u64(raw, off)
    creator = b58(raw[off:off+32]); off += 32
    creator_fee_bps, off = _u64(raw, off)
    creator_fee, off = _u64(raw, off)
    ev = {
        "mint": mint,
        "sol_amount": sol_amount,
        "token_amount": token_amount,
        "is_buy": is_buy,
        "user": user,
        "timestamp": timestamp,
        "virtual_sol_reserves": virtual_sol,
        "virtual_token_reserves": virtual_token,
        "real_sol_reserves": real_sol,
        "real_token_reserves": real_token,
        "fee_recipient": fee_recipient,
        "fee_bps": fee_bps,
        "fee": fee,
        "creator": creator,
        "creator_fee_bps": creator_fee_bps,
        "creator_fee": creator_fee,
        "event_bytes": len(raw),
    }
    return ev

def parse_create_event(raw: bytes) -> Optional[dict]:
    if len(raw) < 16 or raw[:8] != CREATE_EVENT_DISC:
        return None
    # After disc: often name/symbol/uri strings then keys. Be defensive.
    off = 8
    def read_str(o):
        if o + 4 > len(raw):
            return None, o
        n = struct.unpack_from("<I", raw, o)[0]
        o += 4
        if n > 1024 or o + n > len(raw):
            return None, o
        try:
            s = raw[o:o+n].decode("utf-8", errors="replace")
        except Exception:
            s = ""
        return s, o + n
    name, off = read_str(off)
    symbol, off = read_str(off)
    uri, off = read_str(off)
    out = {"name": name, "symbol": symbol, "uri": uri}
    # remaining often includes mint, bonding_curve, creator pubkeys — search later if needed
    return out

def decode_program_data_logs(logs: list[str]) -> list[dict]:
    events = []
    for line in logs or []:
        if not line.startswith("Program data:"):
            continue
        try:
            raw = base64.b64decode(line.split("Program data:", 1)[1].strip())
        except Exception:
            continue
        if raw[:8] == TRADE_EVENT_DISC:
            ev = parse_trade_event(raw)
            if ev:
                ev["event"] = "TradeEvent"
                events.append(ev)
        elif raw[:8] == CREATE_EVENT_DISC:
            ev = parse_create_event(raw) or {}
            ev["event"] = "CreateEvent"
            events.append(ev)
        elif raw[:8] == COMPLETE_EVENT_DISC:
            events.append({"event": "CompleteEvent", "raw_len": len(raw)})
        else:
            events.append({"event": "Unknown", "disc": raw[:8].hex(), "raw_len": len(raw)})
    return events

def instruction_names(logs: list[str]) -> list[str]:
    names = []
    for line in logs or []:
        if "Instruction: " in line:
            names.append(line.split("Instruction: ", 1)[1].strip())
    return names

def compute_budget(tx: dict) -> dict:
    """Parse SetComputeUnitLimit / SetComputeUnitPrice from top-level ixs."""
    msg = ((tx or {}).get("transaction") or {}).get("message") or {}
    cu_limit = None
    cu_price = None  # microlamports per CU
    for ix in msg.get("instructions") or []:
        pid = ix.get("programId") or ""
        if pid != "ComputeBudget111111111111111111111111111111":
            # parsed form may use program name
            if ix.get("program") != "computeBudget" and "ComputeBudget" not in str(pid):
                continue
        parsed = ix.get("parsed")
        if isinstance(parsed, dict):
            typ = parsed.get("type")
            info = parsed.get("info") or {}
            if typ in ("setComputeUnitLimit", "SetComputeUnitLimit"):
                cu_limit = int(info.get("units") or info.get("computeUnitLimit") or 0)
            if typ in ("setComputeUnitPrice", "SetComputeUnitPrice"):
                cu_price = int(info.get("microLamports") or info.get("microLamportsPerComputeUnit") or 0)
            continue
        data = ix.get("data")
        if not data:
            continue
        try:
            raw = base58.b58decode(data)
        except Exception:
            continue
        if not raw:
            continue
        if raw[0] == 2 and len(raw) >= 5:
            cu_limit = struct.unpack_from("<I", raw, 1)[0]
        elif raw[0] == 3 and len(raw) >= 9:
            cu_price = struct.unpack_from("<Q", raw, 1)[0]
    meta = (tx or {}).get("meta") or {}
    fee = int(meta.get("fee") or 0)
    priority_lamports = 0
    if cu_limit and cu_price:
        priority_lamports = cu_limit * cu_price // 1_000_000
    return {
        "cu_limit": cu_limit,
        "cu_price_micro": cu_price,
        "priority_lamports_est": priority_lamports,
        "tx_fee_lamports": fee,
    }

def sol_delta_map(tx: dict) -> dict[str, dict]:
    keys = account_keys(tx)
    meta = (tx or {}).get("meta") or {}
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    out = {}
    for i, k in enumerate(keys):
        if i >= len(pre) or i >= len(post):
            break
        out[k] = {"pre": pre[i], "post": post[i], "delta": post[i] - pre[i], "index": i}
    return out

def jito_tip_lamports(tx: dict) -> int:
    tip = 0
    meta = (tx or {}).get("meta") or {}
    # walk parsed system transfers in top + inner ixs
    def walk(ixs):
        nonlocal tip
        for ix in ixs or []:
            parsed = ix.get("parsed")
            if isinstance(parsed, dict) and parsed.get("type") == "transfer":
                info = parsed.get("info") or {}
                dest = info.get("destination")
                if dest in JITO_TIPS:
                    tip += int(info.get("lamports") or 0)
    msg = ((tx or {}).get("transaction") or {}).get("message") or {}
    walk(msg.get("instructions") or [])
    for g in meta.get("innerInstructions") or []:
        walk(g.get("instructions") or [])
    # fallback: positive deltas to known tip accounts
    if tip == 0:
        for k, d in sol_delta_map(tx).items():
            if k in JITO_TIPS and d["delta"] > 0:
                tip += d["delta"]
    return tip

def token_deltas(tx: dict, mint: str) -> list[dict]:
    meta = (tx or {}).get("meta") or {}
    pre = {(x.get("accountIndex"), x.get("mint")): x for x in (meta.get("preTokenBalances") or [])}
    post = {(x.get("accountIndex"), x.get("mint")): x for x in (meta.get("postTokenBalances") or [])}
    keys = account_keys(tx)
    idxs = set([k[0] for k in pre] + [k[0] for k in post])
    out = []
    for i in idxs:
        a = pre.get((i, mint))
        b = post.get((i, mint))
        if not a and not b:
            continue
        pre_amt = int((a or {}).get("uiTokenAmount", {}).get("amount") or 0)
        post_amt = int((b or {}).get("uiTokenAmount", {}).get("amount") or 0)
        owner = (b or a or {}).get("owner")
        out.append({
            "accountIndex": i,
            "account": keys[i] if i < len(keys) else None,
            "owner": owner,
            "pre": pre_amt,
            "post": post_amt,
            "delta": post_amt - pre_amt,
        })
    return out

def parse_tx(sig: str, tx: dict, mint: str, curve: Optional[str] = None) -> dict:
    """Parse one transaction into a row plus extra structured fields."""
    meta = (tx or {}).get("meta") or {}
    logs = meta.get("logMessages") or []
    names = instruction_names(logs)
    events = decode_program_data_logs(logs)
    trade_evs = [e for e in events if e.get("event") == "TradeEvent"]
    create_evs = [e for e in events if e.get("event") == "CreateEvent"]
    complete = any(e.get("event") == "CompleteEvent" for e in events)
    err = meta.get("err")
    payer = fee_payer_of(tx)
    signs = signers_of(tx)
    cb = compute_budget(tx)
    tip = jito_tip_lamports(tx)
    solmap = sol_delta_map(tx)
    payer_bal = solmap.get(payer) if payer else None
    tds = token_deltas(tx, mint) if not err else []

    row = {
        "signature": sig,
        "slot": tx.get("slot"),
        "block_time": tx.get("blockTime"),
        "err": None if err is None else str(err),
        "ok": err is None,
        "fee_payer": payer,
        "signers": ",".join(signs),
        "ix_names": ",".join(names),
        "is_create": any(n.startswith("Create") for n in names),
        "is_buy": any(n.startswith("Buy") for n in names) or any(e.get("is_buy") for e in trade_evs),
        "is_sell": any(n.startswith("Sell") for n in names) or any(e.get("is_buy") is False for e in trade_evs),
        "is_migrate": any("Migrat" in n or "Complete" in n for n in names) or complete,
        "tx_fee_lamports": cb["tx_fee_lamports"],
        "priority_lamports_est": cb["priority_lamports_est"],
        "cu_limit": cb["cu_limit"],
        "cu_price_micro": cb["cu_price_micro"],
        "jito_tip_lamports": tip,
        "payer_sol_pre": payer_bal["pre"] if payer_bal else None,
        "payer_sol_post": payer_bal["post"] if payer_bal else None,
        "payer_sol_delta": payer_bal["delta"] if payer_bal else None,
        "n_trade_events": len(trade_evs),
        "n_create_events": len(create_evs),
    }

    trades = []
    if err is None:
        for ev in trade_evs:
            if ev.get("mint") and ev["mint"] != mint:
                continue
            tok = ev["token_amount"] / (10 ** TOKEN_DECIMALS)
            sol = ev["sol_amount"] / 1e9
            price = (sol / tok) if tok else None
            vsol = ev["virtual_sol_reserves"]
            vtok = ev["virtual_token_reserves"]
            # post-trade reserves in event
            price_curve = None
            mcap_sol = None
            if vsol and vtok:
                price_curve = (vsol / 1e9) / (vtok / 10 ** TOKEN_DECIMALS)
                mcap_sol = price_curve * TOTAL_SUPPLY_UI
            side = "buy" if ev["is_buy"] else "sell"
            trades.append({
                **{k: ev[k] for k in ev if k != "event_bytes"},
                "signature": sig,
                "slot": tx.get("slot"),
                "block_time": tx.get("blockTime") or ev.get("timestamp"),
                "side": side,
                "trader": ev["user"],
                "sol_ui": sol,
                "token_ui": tok,
                "price_sol": price,
                "price_curve_sol": price_curve,
                "mcap_sol": mcap_sol,
                "fee_sol": ev["fee"] / 1e9,
                "creator_fee_sol": ev["creator_fee"] / 1e9,
                "tx_fee_lamports": cb["tx_fee_lamports"],
                "priority_lamports_est": cb["priority_lamports_est"],
                "jito_tip_lamports": tip,
                "fee_payer": payer,
                "real_sol_ui": ev["real_sol_reserves"] / 1e9,
                "real_token_ui": ev["real_token_reserves"] / 10 ** TOKEN_DECIMALS,
                "curve_progress_pct": (ev["real_sol_reserves"] / 1e9) / 85.0 * 100.0,
                "is_create_tx": row["is_create"],
            })
        # fallback from token deltas if no event but pump buy/sell
        if not trades and (row["is_buy"] or row["is_sell"]) and tds:
            # user is token owner with nonzero delta, not the curve
            user_td = [d for d in tds if d["delta"] != 0 and (not curve or d["owner"] != curve)]
            curve_td = [d for d in tds if curve and d["owner"] == curve]
            if user_td:
                d = max(user_td, key=lambda x: abs(x["delta"]))
                side = "buy" if d["delta"] > 0 else "sell"
                tok = abs(d["delta"]) / (10 ** TOKEN_DECIMALS)
                # SOL into curve
                curve_sol = 0
                if curve and curve in solmap:
                    # on buy, curve SOL increases by (sol - ? fees often leave curve)
                    curve_sol = abs(solmap[curve]["delta"]) / 1e9
                price = (curve_sol / tok) if tok and curve_sol else None
                trades.append({
                    "signature": sig,
                    "slot": tx.get("slot"),
                    "block_time": tx.get("blockTime"),
                    "side": side,
                    "trader": d["owner"],
                    "sol_ui": curve_sol,
                    "token_ui": tok,
                    "price_sol": price,
                    "price_curve_sol": price,
                    "mcap_sol": (price * TOTAL_SUPPLY_UI) if price else None,
                    "fee_sol": None,
                    "creator_fee_sol": None,
                    "tx_fee_lamports": cb["tx_fee_lamports"],
                    "priority_lamports_est": cb["priority_lamports_est"],
                    "jito_tip_lamports": tip,
                    "fee_payer": payer,
                    "fallback": True,
                    "is_create_tx": row["is_create"],
                })
    row["_trades"] = trades
    row["_create_events"] = create_evs
    row["_solmap"] = solmap
    row["_token_deltas"] = tds
    return row


def parse_enhanced_tx(item: dict, mint: str, curve: str) -> dict:
    """Parse a Helius enhanced transaction into the same row/trades shape."""
    sig = item.get("signature")
    err = item.get("transactionError")
    payer = item.get("feePayer")
    tts = item.get("tokenTransfers") or []
    nts = item.get("nativeTransfers") or []
    tip = 0
    sol_to_curve = 0
    sol_from_curve = 0
    fee_sol_extra = 0
    for nt in nts:
        amt = int(nt.get("amount") or 0)
        src = nt.get("fromUserAccount")
        dst = nt.get("toUserAccount")
        if dst in JITO_TIPS:
            tip += amt
        if dst == curve:
            sol_to_curve += amt
        if src == curve:
            sol_from_curve += amt
    # Pump.fun sells often omit curve->user SOL in nativeTransfers; use accountData.
    curve_native = 0
    for ad in item.get("accountData") or []:
        if ad.get("account") == curve:
            curve_native = int(ad.get("nativeBalanceChange") or 0)
            break
    if sol_to_curve == 0 and curve_native > 0:
        sol_to_curve = curve_native
    if sol_from_curve == 0 and curve_native < 0:
        sol_from_curve = -curve_native
    # Quote token (custom pump.fun pairs, e.g. VVV) transferred against the curve
    quote_flows = {}  # mint -> [to_curve, from_curve]
    for tt in tts:
        m = tt.get("mint")
        if not m or m == mint:
            continue
        amt = float(tt.get("tokenAmount") or 0)
        if tt.get("toUserAccount") == curve:
            quote_flows.setdefault(m, [0.0, 0.0])
            quote_flows[m][0] += amt
        elif tt.get("fromUserAccount") == curve:
            quote_flows.setdefault(m, [0.0, 0.0])
            quote_flows[m][1] += amt
    quote_mint = None
    quote_to_curve = quote_from_curve = 0.0
    if quote_flows:
        quote_mint, (quote_to_curve, quote_from_curve) = max(
            quote_flows.items(), key=lambda kv: kv[1][0] + kv[1][1]
        )

    mint_tt = [tt for tt in tts if tt.get("mint") == mint]
    buys, sells = [], []
    for tt in mint_tt:
        amt = float(tt.get("tokenAmount") or 0)
        if tt.get("fromUserAccount") == curve or (curve and tt.get("fromUserAccount") == curve):
            buys.append((tt.get("toUserAccount"), amt))
        elif tt.get("toUserAccount") == curve:
            sells.append((tt.get("fromUserAccount"), amt))
    side = None
    trader = None
    token_ui = 0.0
    sol_ui = 0.0
    if buys and not sells:
        side = "buy"
        trader, token_ui = buys[0]
        if len(buys) > 1:
            token_ui = sum(a for _, a in buys)
            # prefer non-none trader with max amount
            trader, token_ui = max(buys, key=lambda x: x[1])
        sol_ui = sol_to_curve / 1e9
    elif sells and not buys:
        side = "sell"
        trader, token_ui = max(sells, key=lambda x: x[1])
        sol_ui = sol_from_curve / 1e9
    elif buys and sells:
        # rare exact swap both ways; treat net
        net_tok = sum(a for _, a in buys) - sum(a for _, a in sells)
        if net_tok > 0:
            side = "buy"; token_ui = net_tok; trader = max(buys, key=lambda x: x[1])[0]; sol_ui = sol_to_curve / 1e9
        else:
            side = "sell"; token_ui = -net_tok; trader = max(sells, key=lambda x: x[1])[0]; sol_ui = sol_from_curve / 1e9

    # instruction programs
    ixs = item.get("instructions") or []
    has_pump = any((ix.get("programId") == PUMP_PROGRAM) for ix in ixs)
    typ = item.get("type")
    is_create = typ in ("CREATE", "CREATE_ACCOUNT") or "create" in (item.get("description") or "").lower()[:40]
    # fee
    tx_fee = int(item.get("fee") or 0)
    if side == "buy":
        quote_ui = quote_to_curve or sol_ui
    elif side == "sell":
        quote_ui = quote_from_curve or sol_ui
    else:
        quote_ui = 0.0
    if not quote_mint and sol_ui:
        quote_mint = WSOL
        quote_ui = sol_ui
    is_sol_quote = (not quote_mint) or quote_mint == WSOL
    if is_sol_quote:
        sol_ui = quote_ui or sol_ui
    price = (sol_ui / token_ui) if (sol_ui and token_ui) else None
    price_quote = (quote_ui / token_ui) if (quote_ui and token_ui) else None
    typ_u = (typ or "").upper()
    is_lp = typ_u in {
        "CREATE_POOL", "MIGRATE", "ADD_LIQUIDITY", "REMOVE_LIQUIDITY",
        "DEPOSIT", "WITHDRAW", "ADD_TO_POOL", "REMOVE_FROM_POOL",
    } or typ_u.startswith("CREATE_POOL")
    trades = []
    if err is None and side and token_ui and quote_ui and not is_lp:
        trades.append({
            "signature": sig,
            "slot": item.get("slot"),
            "block_time": item.get("timestamp"),
            "side": side,
            "trader": trader or payer,
            "sol_ui": sol_ui if is_sol_quote else None,
            "quote_mint": quote_mint,
            "quote_ui": quote_ui,
            "is_sol_quote": is_sol_quote,
            "token_ui": token_ui,
            "price_sol": price,
            "price_quote": price_quote,
            "price_curve_sol": price,
            "mcap_sol": (price * TOTAL_SUPPLY_UI) if price else None,
            "fee_sol": None,
            "creator_fee_sol": None,
            "tx_fee_lamports": tx_fee,
            "priority_lamports_est": None,
            "jito_tip_lamports": tip,
            "fee_payer": payer,
            "source": "enhanced",
            "is_create_tx": is_create,
        })
    row = {
        "signature": sig,
        "slot": item.get("slot"),
        "block_time": item.get("timestamp"),
        "err": None if err is None else str(err),
        "ok": err is None,
        "fee_payer": payer,
        "signers": payer or "",
        "ix_names": typ or "",
        "is_create": is_create,
        "is_buy": side == "buy",
        "is_sell": side == "sell",
        "is_migrate": typ in ("MIGRATE",) or "migrat" in (item.get("description") or "").lower(),
        "tx_fee_lamports": tx_fee,
        "priority_lamports_est": None,
        "cu_limit": None,
        "cu_price_micro": None,
        "jito_tip_lamports": tip,
        "payer_sol_pre": None,
        "payer_sol_post": None,
        "payer_sol_delta": None,
        "n_trade_events": len(trades),
        "n_create_events": 0,
        "source": "enhanced",
        "_trades": trades,
        "_create_events": [],
        "_solmap": {},
        "_token_deltas": [],
        "_enhanced": item,
    }
    # approximate payer sol delta if accountData present
    for ad in item.get("accountData") or []:
        if ad.get("account") == payer:
            ch = ad.get("nativeBalanceChange")
            if ch is not None:
                row["payer_sol_delta"] = int(ch)
    return row
