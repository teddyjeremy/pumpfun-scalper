#!/usr/bin/env python3
"""Summarize one paper observation and identify profitable signals rejected by risk gates."""
from __future__ import annotations

import argparse
import json
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone


def read_jsonl(path: str) -> list[dict]:
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def stats(rows: list[dict]) -> dict:
    vals = [float(x.get("net_per_1usd", 0)) for x in rows]
    return {
        "trades": len(vals),
        "wins": sum(x > 0 for x in vals),
        "win_rate": round(sum(x > 0 for x in vals) / len(vals), 4) if vals else None,
        "avg_net_per_1usd": round(statistics.mean(vals), 5) if vals else None,
        "total_net_per_1usd": round(sum(vals), 5),
        "best": round(max(vals), 5) if vals else None,
        "worst": round(min(vals), 5) if vals else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="./data")
    ap.add_argument("--out", default="./report")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    signals = read_jsonl(os.path.join(args.data_dir, "signals_paper.jsonl"))
    shadow = read_jsonl(os.path.join(args.data_dir, "shadow_paper.jsonl"))
    taken = read_jsonl(os.path.join(args.data_dir, "trades_paper.jsonl"))
    # Shadow engine runs the same configured lanes without the bankroll/risk gate.
    # Match each completed shadow result to its original signal.
    sig_index = {}
    for s in signals:
        key = (s.get("mint"), s.get("lane"), int(s.get("t_plus", -1)))
        sig_index[key] = s

    missed = []
    for t in shadow:
        key = (t.get("mint"), t.get("lane"), int(t.get("entry_t_plus", -1)))
        s = sig_index.get(key)
        if s and not s.get("taken", False):
            missed.append({
                "symbol": t.get("symbol"), "mint": t.get("mint"), "lane": t.get("lane"),
                "entry_t_plus": t.get("entry_t_plus"), "gate_reason": s.get("gate"),
                "mcap_usd": s.get("mcap"), "exit": t.get("exit"),
                "net_per_1usd": t.get("net_per_1usd"), "mfe": t.get("mfe"), "mae": t.get("mae"),
            })

    profitable_missed = sorted(
        [x for x in missed if float(x.get("net_per_1usd") or 0) > 0],
        key=lambda x: float(x["net_per_1usd"]), reverse=True
    )
    by_lane = {}
    for lane in sorted({x.get("lane", "?") for x in shadow}):
        by_lane[lane] = stats([x for x in shadow if x.get("lane") == lane])

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "paper_only",
        "warning": "Counterfactual paper results, not executable fills or proof of future profitability.",
        "signals_seen": len(signals),
        "signals_rejected_by_risk_gate": sum(1 for x in signals if not x.get("taken", False)),
        "completed_shadow_trades": len(shadow),
        "bankroll_gated_trades": len(taken),
        "shadow_performance": stats(shadow),
        "bankroll_performance": stats(taken),
        "by_lane": by_lane,
        "missed_due_to_risk_gate_completed": len(missed),
        "missed_profitable": profitable_missed,
        "missed_unprofitable": [x for x in missed if float(x.get("net_per_1usd") or 0) <= 0],
        "limitations": [
            "This identifies completed signals rejected by the bankroll/risk gate, not every coin that never triggered Lane K or B.",
            "Open positions and signals without completed shadow exits are not included in missed-profitable ranking.",
            "One hour is a smoke-test sample; do not tune thresholds or infer an edge from it.",
            "Paper prices and fills can differ from real fills because of RPC latency, liquidity, priority fees, and MEV.",
        ],
    }
    json_path = os.path.join(args.out, "hourly_report.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    lines = [
        "# Pump.fun one-hour paper observation",
        "",
        f"- Generated: {report['generated_utc']}",
        "- Mode: paper only; no transactions sent.",
        "",
        "## Summary",
        "",
        f"- Signals: {len(signals)}",
        f"- Signals rejected by risk gate: {report['signals_rejected_by_risk_gate']}",
        f"- Completed shadow trades: {len(shadow)}",
        f"- Bankroll-gated trades: {len(taken)}",
        f"- Shadow average net per $1: {report['shadow_performance']['avg_net_per_1usd']}",
        f"- Shadow win rate: {report['shadow_performance']['win_rate']}",
        f"- Missed completed signals due to risk gate: {len(missed)}",
        f"- Profitable missed signals: {len(profitable_missed)}",
        "",
        "## Profitable signals rejected by risk controls",
        "",
        "| Token | Mint | Lane | Entry t+ | Rejection reason | Net per $1 | MFE | MAE |",
        "|---|---|---:|---:|---|---:|---:|---:|",
    ]
    for x in profitable_missed[:50]:
        lines.append(
            f"| {x.get('symbol') or ''} | {x.get('mint') or ''} | {x.get('lane')} | {x.get('entry_t_plus')} "
            f"| {x.get('gate_reason')} | {x.get('net_per_1usd')} | {x.get('mfe')} | {x.get('mae')} |"
        )
    if not profitable_missed:
        lines.append("| None recorded | | | | | | | |")
    lines += [
        "",
        "## Performance by lane",
        "",
        "| Lane | Trades | Win rate | Avg net per $1 | Total net per $1 |",
        "|---|---:|---:|---:|---:|",
    ]
    for lane, x in by_lane.items():
        lines.append(f"| {lane} | {x['trades']} | {x['win_rate']} | {x['avg_net_per_1usd']} | {x['total_net_per_1usd']} |")
    lines += ["", "## Interpretation limits", ""] + [f"- {x}" for x in report["limitations"]]
    with open(os.path.join(args.out, "hourly_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote {json_path} and {os.path.join(args.out, 'hourly_report.md')}")


if __name__ == "__main__":
    main()
