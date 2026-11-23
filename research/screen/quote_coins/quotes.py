#!/usr/bin/env python3
"""Identify quote mints (Helius getAsset) and pull USD price history (GeckoTerminal public API:
top pool by liquidity, 15-minute OHLCV, Nov 15 -> now). Stables pegged at $1 (USDC/USDT/PYUSD/USD1 etc. checked against bars).
Output quotes/<mint>.json. Research only."""
import json, sys, time, requests
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import qrpc
OUT = HERE / "quotes"; OUT.mkdir(exist_ok=True)
GT = "https://api.geckoterminal.com/api/v2/networks/solana"
T_FROM = 1795035600 - 86400  # 2026-11-15 00:00 EAT
def gt(path, params=None):
    for a in range(6):
        r = requests.get(GT + path, params=params, headers={"accept": "application/json"}, timeout=30)
        if r.status_code == 429: time.sleep(15 * (a + 1)); continue
        if r.status_code == 404: return None
        r.raise_for_status(); time.sleep(2.2); return r.json()
    return None
def asset(m):
    try:
        r = qrpc.call("getAsset", {"id": m}, kind="helius")
        c = (r or {}).get("content", {}).get("metadata", {}); ti = (r or {}).get("token_info", {})
        return {"name": c.get("name"), "symbol": c.get("symbol") or ti.get("symbol"), "decimals": ti.get("decimals"),
                "token_program": ti.get("token_program"), "interface": (r or {}).get("interface"),
                "price_now": (ti.get("price_info") or {}).get("price_per_token"), "supply": ti.get("supply")}
    except Exception as e:
        return {"err": str(e)[:100]}
def history(m):
    j = gt(f"/tokens/{m}/pools", {"page": 1})
    pools = (j or {}).get("data") or []
    if not pools: return None, []
    best = max(pools, key=lambda p: float(p["attributes"].get("reserve_in_usd") or 0))
    pid = best["id"].split("_", 1)[1]; a = best["attributes"]
    base_is_m = best["relationships"]["base_token"]["data"]["id"].endswith(m)
    bars = []; before = int(time.time()) + 60
    while before > T_FROM:
        j = gt(f"/pools/{pid}/ohlcv/minute", {"aggregate": 15, "limit": 1000, "before_timestamp": before,
                                               "currency": "usd", "token": "base" if base_is_m else "quote"})
        lst = ((j or {}).get("data") or {}).get("attributes", {}).get("ohlcv_list") or []
        if not lst: break
        bars += lst; before = min(b[0] for b in lst)
        if len(lst) < 1000: break
    bars = sorted({b[0]: b for b in bars}.values())
    info = {"pool": pid, "pool_name": a.get("name"), "reserve_usd": a.get("reserve_in_usd"), "vol24": (a.get("volume_usd") or {}).get("h24"),
            "n_pools": len(pools)}
    return info, bars
if __name__ == "__main__":
    for m in sys.argv[1:]:
        p = OUT / f"{m}.json"
        if p.exists(): continue
        a = asset(m); pinfo, bars = history(m)
        json.dump({"mint": m, "asset": a, "pool": pinfo, "bars15m": bars}, open(p, "w"))
        print(m, a.get("symbol"), a.get("name"), a.get("decimals"), (pinfo or {}).get("pool_name"), (pinfo or {}).get("reserve_usd"), len(bars),
              bars and (bars[0][0], bars[-1][4]), flush=True)
