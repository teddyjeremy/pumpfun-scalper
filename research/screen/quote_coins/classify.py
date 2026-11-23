#!/usr/bin/env python3
"""Classify every Pump.fun launch of a day by its bonding-curve quote mint (curve account offset 83;
System Program 111..1 = SOL). Reads screen/launches_<day>.csv (or quote_coins/launches_<day>.csv),
calls getMultipleAccounts (100/call) on Alchemy (fallback Helius key 2), throttled.
Output: quote_coins/class_<day>.csv. Research only."""
import base64, csv, struct, sys, os, time
from pathlib import Path
import base58
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import qrpc
SYS = "11111111111111111111111111111111"
def src(day):
    for p in (HERE.parent / f"launches_{day}.csv", HERE / f"launches_{day}.csv"):
        if p.exists(): return p
def main(day, kind=("alchemy", "helius")):
    out = HERE / f"class_{day}.csv"
    if out.exists(): print("exists", out); return
    rows = [r for r in csv.DictReader(open(src(day))) if len(r.get("curve") or "") >= 32 and r.get("mint")]
    seen = set(); uniq = []; import random
    rng = random.Random(day)
    for r in rows:
        if r["mint"] in seen: continue
        seen.add(r["mint"])
        # candidate = create tx touched a non-WSOL extra mint (the quote vault shows up there); plus a 3% validation sample of the rest
        r["_cand"] = 1 if r.get("extra_mints") else 0
        if r["_cand"] or rng.random() < 0.03: uniq.append(r)
    print(day, "unique", len(seen), "to check", len(uniq), flush=True)
    res = []; t = time.time()
    from concurrent.futures import ThreadPoolExecutor
    def one(i):
        ch = uniq[i:i + 100]
        try:
            v = qrpc.call("getMultipleAccounts", [[r["curve"] for r in ch], {"encoding": "base64"}], kind=kind)["value"]
        except Exception as e:
            print("ERR chunk", i, str(e)[:100], flush=True); v = [None] * len(ch)
        recs = []
        for r, a in zip(ch, v):
            rec = {"mint": r["mint"], "symbol": r.get("symbol", ""), "name": r.get("name", ""), "launch_ts": r["launch_ts"],
                   "curve": r["curve"], "creator": r.get("creator", ""), "quote_mint": "", "complete": "", "vt": "", "vq": "", "dlen": 0, "cand": r["_cand"], "extra_mints": r.get("extra_mints", "")}
            if a:
                d = base64.b64decode(a["data"][0]); rec["dlen"] = len(d)
                if len(d) >= 115 and d[:8].hex() == "17b7f83760d8ac60":
                    rec["quote_mint"] = base58.b58encode(d[83:115]).decode()
                    rec["vt"], rec["vq"] = struct.unpack_from("<QQ", d, 8); rec["complete"] = d[48]
                    if rec["quote_mint"] == SYS: rec["quote_mint"] = "SOL"
                elif len(d) >= 49:
                    rec["quote_mint"] = "SOL?short"; rec["complete"] = d[48]
            recs.append(rec)
        return recs
    with ThreadPoolExecutor(4) as ex:
        for k, recs in enumerate(ex.map(one, range(0, len(uniq), 100))):
            res.extend(recs)
            if k % 50 == 0: print(day, k * 100, len(uniq), f"{time.time()-t:.0f}s", flush=True)
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(res[0].keys())); w.writeheader(); w.writerows(res)
    tmp.rename(out); print("DONE", day, len(seen), len(res), f"{time.time()-t:.0f}s", flush=True)
if __name__ == "__main__":
    os.environ.setdefault("QC_MIN_GAP", "1.0")
    qrpc.MIN_GAP = float(os.environ["QC_MIN_GAP"])
    for d in sys.argv[1:]: main(d)
