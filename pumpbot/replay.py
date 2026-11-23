"""Backtest replay: feed recorded tapes trade-by-trade through the SAME engine the live bot uses.

  python -m pumpbot.replay --tapes ../screen/cache/scalp_tapes --paper-tapes ../paper/logs/tapes --profile rulebook

Profiles:
  rulebook  exactly the rulebook_v2 Lane B test (guards 1-5, no create-slot filter) -> reproduces RULES.md table
  bot       the bot defaults (adds the create-slot-wallets >25% rug filter)
Tape formats: research scalp tapes (screen/cache/scalp_tapes/<day>/<mint>.json.gz) and paper-trader tapes.
Paper tapes only use entries after the paper trader's own signal time (selection known at entry time).
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from .config import Config
from .engine import CoinEngine
from .tape import Tape

EAT = timezone(timedelta(hours=3))


def load(path: str) -> dict:
    with gzip.open(path, "rt") as f:
        return json.load(f)


def from_paper(b: dict) -> dict:
    t0 = b["t0"]
    tr = [{"t": x["bt"] - t0, "bt": x["bt"], "slot": x.get("slot"), "side": x["side"], "usd": x.get("volume_usd") or 0.0,
           "spot": x.get("spot_usd"), "trader": x.get("trader"), "tok": x.get("token_ui") or 0.0, "sol": x.get("sol_ui") or 0.0}
          for x in b["trades"]]
    return {"mint": b["mint"], "t0": t0, "creator": b.get("creator"), "trades": tr, "quote_mint": b.get("quote_mint", "SOL")}


def run_coin(blob: dict, cfg: Config, lanes, rug_bundle: bool, sel=None) -> list[dict]:
    tape = Tape(blob["mint"], blob["t0"], blob.get("creator"), blob.get("quote_mint", "SOL"))
    eng = CoinEngine(tape, cfg, lanes=lanes, rug_bundle=rug_bundle,
                     select_from_bt=sel[0] if sel else None, select_until_bt=sel[1] if sel else None)
    for x in blob["trades"]:
        eng.on_trade(x)
    eng.finish()
    return eng.closed


def iter_tapes(tapes_dir: str | None, paper_dir: str | None, days=None):
    if tapes_dir:
        for p in sorted(glob.glob(os.path.join(tapes_dir, "*", "*.json.gz"))):
            day = os.path.basename(os.path.dirname(p))
            if days and day not in days:
                continue
            yield day, "bt", p
    if paper_dir:
        for p in sorted(glob.glob(os.path.join(paper_dir, "*.json.gz"))):
            yield None, "paper", p


def replay(tapes_dir=None, paper_dir=None, profile="rulebook", lanes=("B",), days=None, cfg=None):
    cfg = cfg or Config()
    rug_bundle = profile != "rulebook"
    trades = []
    for day, src, p in iter_tapes(tapes_dir, paper_dir, days):
        try:
            b = load(p)
        except Exception:
            continue
        sel = None
        if src == "paper":
            if b.get("quote_mint", "SOL") != "SOL":
                continue
            dec = b.get("decision") or {}
            if not dec.get("signal_block_time"):
                continue
            s = int(dec["signal_block_time"]); sel = (s, s + 310)
            day = datetime.fromtimestamp(b["t0"], EAT).strftime("%Y-%m-%d") + "*live"
            b = from_paper(b)
        for r in run_coin(b, cfg, list(lanes), rug_bundle, sel):
            r["day"] = day
            trades.append(r)
    return trades


def summarize(trades: list[dict]) -> dict:
    by = defaultdict(list)
    for r in trades:
        by[r["day"]].append(r)
    out = {}
    for d in sorted(by):
        n = [r["net"] for r in by[d]]
        out[d] = {"n": len(n), "win": round(sum(x > 0 for x in n) / len(n), 2), "avg": round(st.mean(n), 4),
                  "worst": round(min(n), 3), "best": round(max(n), 3), "med_hold": sorted(r["hold_s"] for r in by[d])[len(n) // 2],
                  "exits": dict(Counter(r["exit"] for r in by[d]))}
    alln = [r["net"] for r in trades]
    if alln:
        out["ALL"] = {"n": len(alln), "avg": round(st.mean(alln), 4), "win": round(sum(x > 0 for x in alln) / len(alln), 2)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapes", default=None, help="dir with <day>/<mint>.json.gz research tapes")
    ap.add_argument("--paper-tapes", default=None, help="dir with paper-trader tapes")
    ap.add_argument("--profile", default="rulebook", choices=["rulebook", "bot"])
    ap.add_argument("--lanes", default="B")
    ap.add_argument("--days", default=None)
    ap.add_argument("--portfolio", action="store_true", help="also run the $2 wallet through the risk manager")
    ap.add_argument("--out", default=None, help="write trades as JSONL")
    a = ap.parse_args()
    T = replay(a.tapes, a.paper_tapes, a.profile, a.lanes.split(","), a.days.split(",") if a.days else None)
    print(json.dumps(summarize(T), indent=1))
    if a.out:
        with open(a.out, "w") as f:
            for r in T:
                f.write(json.dumps(r) + "\n")
    if a.portfolio:
        from .risk import portfolio_replay
        print(json.dumps(portfolio_replay(T, Config()), indent=1))


if __name__ == "__main__":
    main()
