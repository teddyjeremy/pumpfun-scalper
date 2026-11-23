#!/usr/bin/env python3
"""Pull 15-min USD price history for every non-stable quote used by sampled coins (plan_*.json)."""
import glob, json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import quotes
meta = json.loads((HERE / "quotes_meta.json").read_text())
qs = set()
for p in glob.glob(str(HERE / "plan_*.json")):
    for m, x in json.load(open(p))["mints"].items(): qs.add(x["quote"])
todo = [q for q in qs if meta.get(q, {}).get("category") != "stable" and not (HERE / "quotes" / f"{q}.json").exists()]
print("quotes", len(qs), "to fetch", len(todo), flush=True)
for q in todo:
    try:
        pinfo, bars = quotes.history(q)
    except Exception as e:
        pinfo, bars = {"err": str(e)[:100]}, []
    json.dump({"mint": q, "meta": meta.get(q), "pool": pinfo, "bars15m": bars}, open(HERE / "quotes" / f"{q}.json", "w"))
    print(q[:8], (meta.get(q) or {}).get("symbol"), (pinfo or {}).get("pool_name"), len(bars), flush=True)
