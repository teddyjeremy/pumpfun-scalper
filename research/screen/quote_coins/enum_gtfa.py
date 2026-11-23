#!/usr/bin/env python3
"""Enumerate Pump.fun creates for an EAT day via Helius getTransactionsForAddress on the mint-authority PDA
(full txs, 100/page), split into time slices fetched in parallel (each call ~15s, so total rate stays < 1/s).
Extracts with screen.common.extract_create (read-only import). Output quote_coins/launches_<day>.csv (+ partial jsonl)."""
import csv, json, sys, time, threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
HERE = Path(__file__).resolve().parent; ROOT = HERE.parent.parent
sys.path[:0] = [str(HERE), str(ROOT)]
import qrpc
from screen.common import extract_create, day_bounds
MA = "TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM"
FIELDS = ["mint", "name", "symbol", "launch_ts", "launch_slot", "create_sig", "creator", "curve", "extra_mints"]
lock = threading.Lock()
def slice_fetch(a, b, sink):
    tok = None; n = 0
    while True:
        p = {"transactionDetails": "full", "encoding": "json", "maxSupportedTransactionVersion": 1, "sortOrder": "asc",
             "limit": 100, "commitment": "confirmed", "filters": {"blockTime": {"gte": a, "lt": b}, "status": "succeeded"}}
        if tok: p["paginationToken"] = tok
        r = qrpc.call("getTransactionsForAddress", [MA, p], timeout=120)
        d = (r or {}).get("data") or []
        recs = []
        for tx in d:
            sig = tx["transaction"]["signatures"][0]
            e = extract_create(sig, tx, tx.get("blockTime"), tx.get("slot"), None)
            if e.get("extract_ok"): recs.append({k: e.get(k, "") for k in FIELDS})
        with lock:
            for x in recs: sink.write(json.dumps(x) + "\n")
            sink.flush()
        n += len(d); tok = (r or {}).get("paginationToken")
        if not tok or not d: return n
def main(day, until=None, threads=6):
    a, b = day_bounds(day)
    if until: b = min(b, until)
    part = HERE / f"launches_{day}.partial.jsonl"
    done_sl = HERE / f"launches_{day}.slices_done.json"
    done = set(json.loads(done_sl.read_text())) if done_sl.exists() else set()
    nsl = 48; step = (b - a + nsl - 1) // nsl
    sl = [(a + i * step, min(b, a + (i + 1) * step)) for i in range(nsl) if a + i * step < b]
    t = time.time()
    with open(part, "a") as sink, ThreadPoolExecutor(threads) as ex:
        futs = {ex.submit(slice_fetch, x, y, sink): (x, y) for (x, y) in sl if f"{x}-{y}" not in done}
        for k, f in enumerate(futs):
            x, y = futs[f]
            try:
                n = f.result(); done.add(f"{x}-{y}"); done_sl.write_text(json.dumps(sorted(done)))
                print(day, "slice", x, n, f"{len(done)}/{len(sl)}", f"{time.time()-t:.0f}s", flush=True)
            except Exception as e:
                print("ERR slice", x, str(e)[:120], flush=True)
    if len(done) == len(sl):
        seen = {}
        for line in open(part):
            r = json.loads(line); seen.setdefault(r["mint"], r)
        rows = sorted(seen.values(), key=lambda r: int(r["launch_ts"] or 0))
        with open(HERE / f"launches_{day}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
        print("DONE", day, len(rows), f"{time.time()-t:.0f}s", flush=True)
if __name__ == "__main__":
    qrpc.MIN_GAP = 1.0
    day = sys.argv[1]; until = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] != "-" else None
    main(day, until)
