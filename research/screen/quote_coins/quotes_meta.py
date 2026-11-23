#!/usr/bin/env python3
"""Identify all quote mints seen in class_*.csv via GeckoTerminal /tokens/multi (30 per call, public API).
Writes quotes_meta.json: mint -> {symbol,name,decimals,price_now,reserve_usd,top_pool,category}."""
import csv, glob, json, time, requests, collections
from pathlib import Path
HERE = Path(__file__).resolve().parent
GT = "https://api.geckoterminal.com/api/v2/networks/solana"
STABLES = {"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
           "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo": "PYUSD", "USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB": "USD1",
           "USDSwr9ApdHk5bvJKMjzff41FfuX8bSxdKcR81vTwcA": "USDS", "2u1tszSeqZ3qBWF3uNGPFc8TzMk2tdiwknnRMWGWjGWH": "USDG",
           "CASHx9KJUStyftLFWGvEVf59SGeG9sh5FfcnZMVPCASH": "CASH", "9zNQRsGLjNKwCUU5Gq5LR8beUCPzQMVMqKAi3SSZh54u": "FDUSD"}
def cat(m, a):
    if m in STABLES: return "stable"
    nm = (a.get("name") or "") + " " + (a.get("symbol") or "")
    if "Securities" in nm or "xStock" in nm or "Tokenized" in nm or (m.startswith("Xs") and (a.get("symbol") or "").endswith("x")): return "tokenized_stock"
    try:
        if 0.97 <= float(a.get("price_usd") or 0) <= 1.03 and "usd" in nm.lower(): return "stable"
    except Exception: pass
    if m.endswith("pump"): return "pump_memecoin"
    return "other_token"
def main():
    cnt = collections.Counter()
    for f in glob.glob(str(HERE / "class_*.csv")):
        for r in csv.DictReader(open(f)):
            if r["quote_mint"] not in ("SOL", "", "SOL?short"): cnt[r["quote_mint"]] += 1
    outp = HERE / "quotes_meta.json"
    meta = json.loads(outp.read_text()) if outp.exists() else {}
    todo = [m for m in cnt if m not in meta]
    for i in range(0, len(todo), 30):
        ch = todo[i:i + 30]
        for a_ in range(5):
            r = requests.get(f"{GT}/tokens/multi/{','.join(ch)}", params={"include": "top_pools"}, timeout=30)
            if r.status_code == 429: time.sleep(20); continue
            break
        got = {}
        if r.status_code == 200:
            for t in r.json().get("data", []):
                a = t["attributes"]; tp = [p["id"].split("_", 1)[1] for p in t["relationships"]["top_pools"]["data"]]
                got[a["address"]] = {"symbol": a.get("symbol"), "name": a.get("name"), "decimals": a.get("decimals"),
                                     "price_now": a.get("price_usd"), "reserve_usd": a.get("total_reserve_in_usd"), "top_pool": tp[0] if tp else None,
                                     "vol24": (a.get("volume_usd") or {}).get("h24")}
        for m in ch:
            g = got.get(m, {"symbol": None, "name": None, "decimals": None, "price_now": None, "top_pool": None, "missing": True})
            g["category"] = cat(m, dict(g, price_usd=g.get("price_now"))); meta[m] = g
        time.sleep(2.2)
        outp.write_text(json.dumps(meta, indent=0))
    for m, n in cnt.most_common(25):
        g = meta[m]; print(n, m[:8], g.get("symbol"), (g.get("name") or "")[:40], g.get("category"), g.get("price_now"))
    print("quotes", len(cnt), "missing GT", sum(1 for m in cnt if meta[m].get("missing")))
if __name__ == "__main__": main()
