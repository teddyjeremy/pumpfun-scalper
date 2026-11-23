#!/usr/bin/env python3
"""Stratified sample of ACTIVE non-SOL-quoted launches (prescreen n_ok_sigs_5m tiers, same cut points as the SOL scalp sample)
and fetch their full curve tapes t0-2..t0+WIN. Writes plan_<day>.json (tier universe / sampled -> weights)."""
import csv, json, random, sys, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import qrpc, tapes
TIERS = [(150, 300), (300, 500), (500, 1000), (1000, 10 ** 9)]
PER = [22, 25, 25, 28]
WIN = 480
def plan(day):
    pp = HERE / f"plan_{day}.json"
    if pp.exists(): return json.loads(pp.read_text())
    cl = {r["mint"]: r for r in csv.DictReader(open(HERE / f"class_{day}.csv"))}
    import glob
    from datetime import datetime, timezone
    bq = {}
    for fp in glob.glob(str(HERE.parent / "cache" / "bitquery" / day / "B_*_*.json")):
        for b in json.load(open(fp)):
            m = b["Trade"]["Currency"]["MintAddress"]
            if m not in cl: continue
            ts = int(datetime.strptime(b["Block"]["T"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
            t0 = int(cl[m]["launch_ts"])
            if ts + 60 > t0 and ts <= t0 + 300: bq[m] = bq.get(m, 0) + int(b["n"])
    # universe = Bitquery stage-B 5-min trade count (same definition as the SOL scalp sample, scalp_fetch.universe)
    pre = {m: {"n_ok_sigs_5m": n} for m, n in bq.items()}
    rng = random.Random(f"qc-{day}")
    P = {"day": day, "tiers": [], "mints": {}, "n_nonsol": 0, "n_nonsol_with_prescreen": 0}
    nq = [m for m, r in cl.items() if r["quote_mint"] not in ("SOL", "", "SOL?short")]
    P["n_nonsol"] = len(nq); P["n_nonsol_with_prescreen"] = sum(1 for m in nq if m in pre)
    for i, (a, b) in enumerate(TIERS):
        ms = sorted(m for m in nq if m in pre and a <= int(pre[m]["n_ok_sigs_5m"] or 0) < b)
        k = min(PER[i], len(ms)); pick = rng.sample(ms, k)
        P["tiers"].append({"lo": a, "hi": b, "universe": len(ms), "sampled": k})
        for m in pick:
            r = cl[m]
            P["mints"][m] = {"tier": i, "n_sigs5": int(pre[m]["n_ok_sigs_5m"] or 0), "curve": r["curve"], "t0": int(r["launch_ts"]),
                             "quote": r["quote_mint"], "creator": r["creator"], "symbol": r["symbol"], "complete": r["complete"]}
    pp.write_text(json.dumps(P, indent=0)); return P
def main(days, threads=3):
    qrpc.MIN_GAP = 0.7
    jobs = []
    for d in days:
        P = plan(d); print(d, P["tiers"], flush=True)
        for m, x in P["mints"].items(): jobs.append((d, m, x))
    random.Random(5).shuffle(jobs)
    t = time.time(); done = 0
    def one(j):
        d, m, x = j
        try:
            b = tapes.fetch(d, m, x["curve"], x["t0"], win=WIN, max_pages=15); return b["pages"]
        except Exception as e:
            print("ERR", m, str(e)[:100], flush=True); return 0
    with ThreadPoolExecutor(threads) as ex:
        for pg in ex.map(one, jobs):
            done += 1
            if done % 20 == 0: print("fetched", done, len(jobs), f"{time.time()-t:.0f}s", flush=True)
    print("DONE", done, flush=True)
if __name__ == "__main__": main(sys.argv[1:])
