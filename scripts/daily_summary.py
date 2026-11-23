#!/usr/bin/env python3
"""Print per-day results from DATA_DIR/trades_<mode>.jsonl:  python scripts/daily_summary.py [paper|simulate|live]"""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pumpbot.journal import Journal, daily_summary
mode = sys.argv[1] if len(sys.argv) > 1 else "paper"
j = Journal(os.environ.get("DATA_DIR", "./data"), mode)
for kind in ("trades", "shadow"):
  T = j.read_kind(kind, mode)
  print(f"== {kind} ({'bankroll-gated wallet' if kind == 'trades' else 'every on-time signal at $1, used by the live gate'})")
  days = sorted({t["ts"][:10] for t in T})
  for d in days:
    print(json.dumps(daily_summary(T, d)))
  for ln in sorted({t["lane"] for t in T}):
    x = [t["net_per_1usd"] for t in T if t["lane"] == ln][-100:]
    gate = 'OPEN' if len(x) >= 100 and sum(x) > 0 else 'CLOSED'
    print(f"lane {ln}: last {len(x)} trades avg ${sum(x)/len(x):+.4f}/$1" + (f" -> live gate {gate}" if kind == "shadow" else ""))
