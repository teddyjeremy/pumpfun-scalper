#!/usr/bin/env python3
"""Run analyze.py on strict v0 passers only, then refresh the v1 backtest.

Watches screen/prescreen_<date>.csv. Quality over quantity: no loose-only coins.
"""
from __future__ import annotations

import csv
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from screen.common import DATE_EAT, SCREEN

ROOT = Path("/workspace/pumpfun")
COINS = ROOT / "coins"
ANALYZE = ROOT / "analyze.py"
PY = sys.executable
CSV_PATH = SCREEN / f"prescreen_{DATE_EAT}.csv"
LOG_DIR = SCREEN / "cache" / "analyze_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def passers():
    if not CSV_PATH.exists():
        return []
    by = {}
    for r in csv.DictReader(CSV_PATH.open()):
        if r.get("mint") and int(r.get("pass_v0") or 0):
            by[r["mint"]] = r
    return list(by.values())


def done(mint: str) -> bool:
    return (COINS / mint / "summary.json").exists()


def run_one(mint: str) -> tuple[str, int, float]:
    log = LOG_DIR / f"{mint}.log"
    t0 = time.time()
    with log.open("w") as f:
        proc = subprocess.run(
            [PY, "-u", str(ANALYZE), mint],
            cwd=str(ROOT),
            stdout=f,
            stderr=subprocess.STDOUT,
        )
    return mint, proc.returncode, time.time() - t0


def refresh_backtest():
    r = subprocess.run([PY, "-u", str(ROOT / "screen" / "backtest.py")], cwd=str(ROOT))
    return r.returncode


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--poll", type=float, default=20)
    ap.add_argument("--once", action="store_true", help="run current passers and exit")
    ap.add_argument("--wait-s", type=float, default=0, help="if >0, poll this many seconds for new passers")
    args = ap.parse_args()

    seen_running = set()
    deadline = time.time() + args.wait_s if args.wait_s else None
    while True:
        rows = passers()
        todo = [r["mint"] for r in rows if not done(r["mint"]) and r["mint"] not in seen_running]
        print(f"analyze passers={len(rows)} todo={len(todo)} done={sum(1 for r in rows if done(r['mint']))}", flush=True)
        if todo:
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futs = {ex.submit(run_one, m): m for m in todo}
                seen_running.update(todo)
                for fut in as_completed(futs):
                    mint, rc, elapsed = fut.result()
                    print(f"  analyze {mint} rc={rc} elapsed={elapsed:.1f}s", flush=True)
            refresh_backtest()
        if args.once:
            if rows:
                refresh_backtest()
            break
        if deadline is not None and time.time() >= deadline and not todo:
            if rows:
                refresh_backtest()
            break
        time.sleep(args.poll)


if __name__ == "__main__":
    main()
