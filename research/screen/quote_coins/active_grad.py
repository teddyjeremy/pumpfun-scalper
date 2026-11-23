#!/usr/bin/env python3
"""Graduation rate among ACTIVE coins (Bitquery >=150 trades in first 5 min), SOL vs non-SOL, Nov 19-22.
Reads curve complete flag (byte 48) with getMultipleAccounts for SOL active coins; non-SOL flags come from class_*.csv."""
import base64, csv, glob, json, sys
from datetime import datetime, timezone
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import qrpc
qrpc.MIN_GAP = 1.0
out = {}
for d in ["2026-11-22", "2026-11-21", "2026-11-20", "2026-11-19"]:
    launches = {r["mint"]: r for r in csv.DictReader(open(HERE.parent / f"launches_{d}.csv")) if len(r.get("curve") or "") >= 32}
    cl = {r["mint"]: r for r in csv.DictReader(open(HERE / f"class_{d}.csv"))}
    bq = {}
    for fp in glob.glob(str(HERE.parent / "cache" / "bitquery" / d / "B_*_*.json")):
        for b in json.load(open(fp)):
            m = b["Trade"]["Currency"]["MintAddress"]
            if m not in launches: continue
            ts = int(datetime.strptime(b["Block"]["T"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
            t0 = int(launches[m]["launch_ts"])
            if ts + 60 > t0 and ts <= t0 + 300: bq[m] = bq.get(m, 0) + int(b["n"])
    act = [m for m, n in bq.items() if n >= 150]
    nonsol = [m for m in act if m in cl and cl[m]["quote_mint"] not in ("SOL", "", "SOL?short")]
    sol = [m for m in act if m not in nonsol]
    comp = {}
    for i in range(0, len(sol), 100):
        ch = sol[i:i + 100]
        v = qrpc.call("getMultipleAccounts", [[launches[m]["curve"] for m in ch], {"encoding": "base64", "dataSlice": {"offset": 48, "length": 1}}], kind=["alchemy", "helius"])["value"]
        for m, a in zip(ch, v):
            if a: comp[m] = base64.b64decode(a["data"][0])[0]
    out[d] = {"sol_active": len(sol), "sol_grad": sum(1 for m in sol if comp.get(m) == 1), "sol_read": len(comp),
              "nonsol_active": len(nonsol), "nonsol_grad": sum(1 for m in nonsol if cl[m]["complete"] == "1")}
    print(d, out[d], flush=True)
json.dump(out, open(HERE / "active_grad.json", "w"), indent=1)
