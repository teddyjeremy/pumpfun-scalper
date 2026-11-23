#!/usr/bin/env python3
"""Fetch + decode full curve tapes (t0-2 .. t0+WIN) for quote-aware Pump.fun curves.
Helius getTransactionsForAddress (key 2), throttled. Decodes TradeEvents from 'Program data:' logs and from
self-CPI event ixs; reads the quote fields after the instruction-name string (len u32 at byte 258):
quote mint @+37, quote amount u64 @+69, virtual quote reserves u64 @+77 (o = 262 + name_len).
Stores raw units only (no USD); pricing is done offline in price.py. Research only."""
import base64, gzip, json, struct, sys, time
from pathlib import Path
import base58
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import qrpc
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
EVTAG = bytes.fromhex("e445a52e51cb9a1d")
TRADE = bytes.fromhex("bddb7fd34ee661ee"); COMPLETE = bytes.fromhex("5f72619cd42e9808")
OUT = HERE / "tapes"

def dec_trade(e):
    if len(e) < 266 or e[:8] != TRADE: return None
    mint = base58.b58encode(e[8:40]).decode()
    solamt, tok = struct.unpack_from("<QQ", e, 40); buy = e[56] == 1
    user = base58.b58encode(e[57:89]).decode(); ts = struct.unpack_from("<q", e, 89)[0]
    vs, vt, rs, rt = struct.unpack_from("<QQQQ", e, 97)
    r = {"mint": mint, "sol": solamt, "tok": tok, "buy": buy, "user": user, "ts": ts, "vs": vs, "vt": vt}
    try:
        nl = struct.unpack_from("<I", e, 258)[0]
        if 0 < nl < 64 and len(e) >= 262 + nl + 85:
            o = 262 + nl
            r["ix"] = e[262:o].decode(errors="replace")
            r["qmint"] = base58.b58encode(e[o + 37:o + 69]).decode()
            r["qamt"], r["vq"] = struct.unpack_from("<QQ", e, o + 69)
    except Exception:
        pass
    return r

def decode_tx(tx, mint):
    m = tx.get("meta") or {}
    if m.get("err") is not None: return [], False
    logs = m.get("logMessages") or []
    evs = []
    for l in logs:
        if l.startswith("Program data: "):
            try: evs.append(base64.b64decode(l[14:]))
            except Exception: pass
    keys = tx["transaction"]["message"]["accountKeys"]
    la = m.get("loadedAddresses") or {}
    keys = keys + la.get("writable", []) + la.get("readonly", [])
    cpi = []
    for g in m.get("innerInstructions") or []:
        for ix in g["instructions"]:
            try:
                if keys[ix["programIdIndex"]] == PUMP:
                    raw = base58.b58decode(ix["data"])
                    if raw[:8] == EVTAG: cpi.append(raw[8:])
            except Exception: pass
    tl = [e for e in evs if e[:8] == TRADE]; tc = [e for e in cpi if e[:8] == TRADE]
    use = tl if len(tl) >= len(tc) else tc
    complete = any(e[:8] == COMPLETE for e in evs + cpi)
    out = []
    for e in use:
        r = dec_trade(e)
        if r and r["mint"] == mint: out.append(r)
    return out, complete

def fetch(day, mint, curve, t0, win=600, max_pages=40, tag=""):
    dest = OUT / day / f"{mint}{tag}.json.gz"
    if dest.exists(): return json.load(gzip.open(dest, "rt"))
    tok = None; txs = 0; pages = 0; trades = []; migrated = None; create_sig = None
    while pages < max_pages:
        p = {"transactionDetails": "full", "encoding": "json", "maxSupportedTransactionVersion": 1, "sortOrder": "asc",
             "limit": 100, "commitment": "confirmed", "filters": {"blockTime": {"gte": t0 - 2, "lte": t0 + win}, "status": "succeeded"}}
        if tok: p["paginationToken"] = tok
        res = qrpc.call("getTransactionsForAddress", [curve, p], kind=["helius", "helius3"], timeout=120)
        d = (res or {}).get("data") or []; pages += 1; txs += len(d)
        for tx in d:
            sig = tx["transaction"]["signatures"][0]
            if create_sig is None: create_sig = sig
            evs, comp = decode_tx(tx, mint)
            if comp and migrated is None: migrated = tx["blockTime"] - t0
            for k, r in enumerate(evs):
                trades.append({"sig": sig, "slot": tx["slot"], "bt": tx["blockTime"], "t": tx["blockTime"] - t0, "k": k,
                               "side": "buy" if r["buy"] else "sell", "trader": r["user"], "tok": r["tok"], "vt": r["vt"],
                               "vs": r["vs"], "sol": r["sol"], "qmint": r.get("qmint"), "qamt": r.get("qamt"), "vq": r.get("vq"),
                               "ix": r.get("ix"), "create": sig == create_sig})
        tok = (res or {}).get("paginationToken")
        if not tok or not d: break
    blob = {"mint": mint, "curve": curve, "t0": t0, "win": win, "pages": pages, "n_txs": txs, "truncated": bool(tok) and pages >= max_pages,
            "migrated_t": migrated, "trades": trades}
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    with gzip.open(tmp, "wt") as f: json.dump(blob, f, separators=(",", ":"))
    tmp.rename(dest)
    return blob
