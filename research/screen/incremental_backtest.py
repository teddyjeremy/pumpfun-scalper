#!/usr/bin/env python3
"""Rerun day backtests when new strict v0 passers appear. Local tapes only — no RPC, no analyze.

Safe to run while prescreen is using the Helius keys. Writes a short report when
anything new is scored; exits 0 with 'no_new' when nothing changed.
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from screen.common import CACHE, NBO, SCREEN, eat_str
from screen.backtest import series_stats

ROOT = Path("/workspace/pumpfun")
PY = sys.executable
LOG = CACHE / "incremental_backtest.log"
DATES = ["2026-11-22", "2026-11-21", "2026-11-20", "2026-11-19"]
RULES = CACHE / "walkforward" / "rulebook_v1.1.json"


def log(msg: str) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now(tz=NBO).strftime('%Y-%m-%d %H:%M:%S')} Africa/Nairobi] {msg}"
    with LOG.open("a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def passers(date: str) -> dict[str, dict]:
    p = SCREEN / f"prescreen_{date}.csv"
    if not p.exists():
        return {}
    by = {}
    for r in csv.DictReader(p.open()):
        if r.get("mint") and int(r.get("pass_v0") or 0):
            by[r["mint"]] = r
    return by


def backtested(date: str) -> set[str]:
    p = SCREEN / f"backtest_{date}.csv"
    if not p.exists():
        return set()
    return {r["mint"] for r in csv.DictReader(p.open()) if r.get("mint")}


def apply_v11(row: dict, tweaks: list) -> bool:
    for tw in tweaks:
        if tw.get("type") != "skip_if":
            continue
        v = row.get(tw["field"])
        if v in (None, ""):
            continue
        try:
            val, thr = float(v), float(tw["value"])
        except (TypeError, ValueError):
            continue
        op = tw.get("op", ">")
        if op == ">" and val > thr:
            return False
        if op == ">=" and val >= thr:
            return False
        if op == "<" and val < thr:
            return False
        if op == "<=" and val <= thr:
            return False
    return True


def summarize(date: str, new_mints: list[str]) -> str:
    rows = list(csv.DictReader((SCREEN / f"backtest_{date}.csv").open()))
    sim = [r for r in rows if not r.get("error")]
    tweaks = []
    if RULES.exists():
        tweaks = (json.loads(RULES.read_text()).get("tweaks") or [])
    kept = [r for r in sim if apply_v11(r, tweaks)]
    lines = [f"# Incremental backtest {date}", "",
             f"Written {datetime.now(tz=NBO).strftime('%Y-%m-%d %H:%M:%S')} Africa/Nairobi (UTC+3).",
             f"New passers scored this run: {len(new_mints)}.", ""]
    if new_mints:
        by = {r["mint"]: r for r in rows}
        lines += ["## New trades (TP 1.7x / 1.8x, no stop, 5-min cap)", "",
                  "| Name | Buy time EAT | t+ | Dip % | Mcap | Cum vol | Trades | Wallets obs | Top-1 seller | 1.7x net | 1.8x net | v1.1 |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
        for m in new_mints:
            r = by.get(m) or {}
            keep = apply_v11(r, tweaks) if r and not r.get("error") else True
            ts = r.get("entry_ts") or ""
            try:
                buy = datetime.fromtimestamp(int(float(ts)), tz=NBO).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
            except (TypeError, ValueError):
                buy = ""
            top = r.get("entry_top1_seller_share") or ""
            try:
                top_s = f"{100*float(top):.1f}%" if top not in ("", None) else "—"
            except (TypeError, ValueError):
                top_s = "—"
            name = f"{r.get('name') or ''} ({r.get('symbol') or ''})".strip()
            v11 = "kept" if (r and not r.get("error") and keep) else ("SKIP" if r and not r.get("error") else (r.get("error") or "—"))
            lines.append(
                f"| {name} | {buy} | {r.get('entry_t_plus') or '—'} | {r.get('entry_drop') or '—'} | "
                f"{r.get('entry_mcap') or '—'} | {r.get('entry_cum_vol') or '—'} | "
                f"{r.get('entry_cum_trades') or '—'} | {r.get('entry_wallets_obs') or '—'} | {top_s} | "
                f"{r.get('rule17_no_sl_net') or '—'} | {r.get('rule18_no_sl_net') or '—'} | {v11} |"
            )
        lines.append("")

    def row(label, rs, key):
        st = series_stats(rs, key, label)
        sk = st.get("streaks") or {}
        def pct(x):
            return "" if x is None else f"{100*x:.1f}%"
        def usd(x):
            return "" if x is None else f"${x:.3f}"
        return (f"| {label} | {st.get('n')} | {st.get('win_n')}/{st.get('loss_n')}/{st.get('be_n')} | "
                f"{pct(st.get('win_pct'))} | {usd(st.get('expectancy_per_1usd'))} | "
                f"{usd(st.get('cum_pnl'))} | {usd(st.get('max_drawdown'))} | "
                f"{sk.get('longest_win_streak')} | {sk.get('longest_loss_streak')} |")

    lines += ["## v1 vs v1.1 (all simulated passers so far)", "",
              "| Book | n | W/L/BE | Win % | Expectancy /$1 | Cum P&L | Max DD | Longest win | Longest loss |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
              row("v1  TP 1.7x no-SL 5-min", sim, "rule17_no_sl_net"),
              row("v1  TP 1.8x no-SL 5-min", sim, "rule18_no_sl_net"),
              row("v1.1 TP 1.7x no-SL 5-min", kept, "rule17_no_sl_net"),
              row("v1.1 TP 1.8x no-SL 5-min", kept, "rule18_no_sl_net"),
              ""]
    dest = CACHE / f"incremental_backtest_{date}.md"
    dest.write_text("\n".join(lines))
    return "\n".join(lines)


def run_date(date: str) -> str:
    p = passers(date)
    done = backtested(date)
    new = [m for m in p if m not in done]
    if not new:
        return "no_new"
    log(f"{date}: {len(new)} new passer(s) not yet backtested — running local backtest")
    rc = subprocess.run(
        [PY, "-u", str(ROOT / "screen" / "backtest.py"), "--date", date],
        cwd=str(ROOT),
        stdout=open(CACHE / f"backtest_{date}.log", "a"),
        stderr=subprocess.STDOUT,
    )
    if rc.returncode != 0:
        log(f"{date}: backtest exited {rc.returncode}")
        return "error"
    report = summarize(date, new)
    log(f"{date}: scored {len(new)} new; report {CACHE / f'incremental_backtest_{date}.md'}")
    print(report, flush=True)
    return "updated"


def main() -> int:
    any_new = False
    for date in DATES:
        src = SCREEN / f"prescreen_{date}.csv"
        if not src.exists():
            continue
        status = run_date(date)
        if status == "updated":
            any_new = True
    if not any_new:
        log("no new passers to backtest")
        print("no_new", flush=True)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
