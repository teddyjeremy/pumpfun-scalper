#!/usr/bin/env python3
"""Full (unsampled) curve trade tapes t0..t0+480s for a stratified random sample of
active coins, for the scalp-expansion research. Research only; no trading.

Universe per day = coins with Bitquery stage-B minute bars (cached; 0 points spent),
tiered by Bitquery trades in the first ~5 min. Light RPC: Helius getTransactionsForAddress
(full, 100/page) through lib/rpc.py, globally throttled (default 3 calls/s, 2 threads)
so the running pipeline is not starved. Output: cache/scalp_tapes/<day>/<mint>.json.gz
"""
from __future__ import annotations
import argparse, csv, glob, gzip, json, random, sys, threading, time
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "screen"))
from lib.rpc import rpc_call  # noqa: E402
from lib.parse import parse_tx  # noqa: E402
from lib.fetch import sol_price_fn  # noqa: E402
from lib.constants import TOTAL_SUPPLY_UI  # noqa: E402

SCREEN = ROOT / "screen"
OUT = SCREEN / "cache" / "scalp_tapes"
WIN = 480
TIERS = [(150, 300), (300, 500), (500, 1000), (1000, 10 ** 9)]


def universe(day: str) -> tuple[dict, dict]:
    rows = {r["mint"]: r for r in csv.DictReader(open(SCREEN / f"prescreen_{day}.csv"))}
    bars: dict[str, list] = {}
    for f in glob.glob(str(SCREEN / "cache" / "bitquery" / day / "B_*_*.json")):
        for b in json.load(open(f)):
            m = b["Trade"]["Currency"]["MintAddress"]
            ts = int(datetime.strptime(b["Block"]["T"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
            bars.setdefault(m, []).append((ts, int(b["n"])))
    cnt = {}
    for m, bs in bars.items():
        if m in rows and rows[m].get("curve") and rows[m].get("launch_ts"):
            t0 = int(rows[m]["launch_ts"])
            cnt[m] = sum(n for ts, n in bs if ts + 60 > t0 and ts <= t0 + 300)
    return rows, cnt


def tier_of(n: int):
    for i, (a, b) in enumerate(TIERS):
        if a <= n < b:
            return i
    return None


def sample_plan(day: str, per_tier: list[int], seed: int = 7) -> dict:
    rows, cnt = universe(day)
    rng = random.Random(f"{seed}-{day}")
    plan = {"day": day, "tiers": [], "mints": {}}
    for i, (a, b) in enumerate(TIERS):
        ms = sorted(m for m, n in cnt.items() if a <= n < b)
        k = min(per_tier[i], len(ms))
        pick = rng.sample(ms, k)
        plan["tiers"].append({"lo": a, "hi": b, "universe": len(ms), "sampled": k, "weight": len(ms) / max(1, k)})
        for m in pick:
            plan["mints"][m] = {"tier": i, "bq_trades_5m": cnt[m]}
    return plan


class Throttle:
    def __init__(self, rps: float):
        self.gap = 1.0 / rps; self.lock = threading.Lock(); self.next = 0.0

    def wait(self):
        with self.lock:
            now = time.time()
            t = max(now, self.next)
            self.next = t + self.gap
        if t > now:
            time.sleep(t - now)


def fetch_one(day: str, mint: str, row: dict, solpx, thr: Throttle) -> dict:
    dest = OUT / day / f"{mint}.json.gz"
    if dest.exists():
        return {"mint": mint, "cached": True}
    t0 = int(row["launch_ts"]); curve = row["curve"]
    tok = None; txs = []; pages = 0
    while pages < 60:
        p = {"transactionDetails": "full", "encoding": "json", "maxSupportedTransactionVersion": 1,
             "sortOrder": "asc", "limit": 100, "commitment": "confirmed",
             "filters": {"blockTime": {"gte": t0 - 2, "lte": t0 + WIN}, "status": "succeeded"}}
        if tok:
            p["paginationToken"] = tok
        thr.wait()
        res = rpc_call("getTransactionsForAddress", [curve, p], timeout=60, retries=4)
        d = (res or {}).get("data") or []
        pages += 1
        txs.extend(d)
        tok = (res or {}).get("paginationToken")
        if not tok or not d:
            break
    trades = []
    migrated = None
    for tx in txs:
        sig = ((tx.get("transaction") or {}).get("signatures") or [None])[0]
        try:
            row_ = parse_tx(sig, tx, mint, curve)
        except Exception:
            continue
        if row_.get("is_migrate") and migrated is None:
            migrated = (tx.get("blockTime") or 0) - t0
        for tr in row_["_trades"]:
            bt = tr.get("block_time")
            if bt is None:
                continue
            su = float(solpx(int(bt)))
            pc = tr.get("price_curve_sol") or tr.get("price_sol")
            trades.append({
                "sig": sig, "slot": tr.get("slot"), "bt": int(bt), "t": int(bt) - t0,
                "side": tr["side"], "trader": tr["trader"], "sol": tr.get("sol_ui") or 0.0,
                "tok": tr.get("token_ui") or 0.0,
                "px": (tr["price_sol"] * su) if tr.get("price_sol") else None,      # avg fill USD/token
                "spot": (pc * su) if pc else None,                                 # post-trade curve spot USD/token
                "mcap": (pc * su * TOTAL_SUPPLY_UI) if pc else None,
                "usd": (tr.get("sol_ui") or 0.0) * su, "create": bool(tr.get("is_create_tx")),
            })
    trades.sort(key=lambda x: (x["slot"] or 0, x["bt"]))
    blob = {"mint": mint, "symbol": row.get("symbol"), "name": row.get("name"), "t0": t0, "creator": row.get("creator"),
            "curve": curve, "pages": pages, "n_txs": len(txs), "migrated_t": migrated, "win": WIN, "trades": trades}
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    with gzip.open(tmp, "wt") as f:
        json.dump(blob, f, separators=(",", ":"))
    tmp.rename(dest)
    return {"mint": mint, "pages": pages, "n": len(trades)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    ap.add_argument("--per-tier", default="50,70,70,60")
    ap.add_argument("--rps", type=float, default=3.0)
    ap.add_argument("--threads", type=int, default=2)
    a = ap.parse_args()
    per = [int(x) for x in a.per_tier.split(",")]
    planp = OUT / f"plan_{a.day}.json"
    plan = sample_plan(a.day, per)
    planp.parent.mkdir(parents=True, exist_ok=True)
    planp.write_text(json.dumps(plan, indent=1))
    rows, _ = universe(a.day)
    kl = json.load(open(SCREEN / "cache" / f"sol_usd_klines_{a.day}.json"))
    solpx = sol_price_fn(kl)
    thr = Throttle(a.rps)
    done = 0; calls = 0; t = time.time()
    with ThreadPoolExecutor(a.threads) as ex:
        order = list(plan["mints"]); random.Random(11).shuffle(order)  # interleave tiers
        futs = [ex.submit(fetch_one, a.day, m, rows[m], solpx, thr) for m in order]
        for f in futs:
            try:
                r = f.result(); done += 1; calls += r.get("pages", 0)
            except Exception as e:
                print("ERR", str(e)[:120], flush=True)
            if done % 25 == 0:
                print(f"{a.day} {done}/{len(futs)} calls={calls} {time.time()-t:.0f}s", flush=True)
    print(f"DONE {a.day} {done}/{len(futs)} calls={calls} {time.time()-t:.0f}s", flush=True)


if __name__ == "__main__":
    main()
