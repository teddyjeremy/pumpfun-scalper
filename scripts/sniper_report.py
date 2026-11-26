#!/usr/bin/env python3
"""Summarize one Pump.fun launch-sniper paper observation."""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from statistics import mean


def read_jsonl(path):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="./data")
    ap.add_argument("--out", default="./report")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    launches = read_jsonl(os.path.join(args.data_dir, "sniper_launches_sniper.jsonl"))
    tape = read_jsonl(os.path.join(args.data_dir, "sniper_tape_sniper.jsonl"))
    decisions = read_jsonl(os.path.join(args.data_dir, "sniper_decisions_sniper.jsonl"))
    entries = read_jsonl(os.path.join(args.data_dir, "sniper_entries_sniper.jsonl"))
    trades = read_jsonl(os.path.join(args.data_dir, "sniper_trades_sniper.jsonl"))
    status_rows = read_jsonl(os.path.join(args.data_dir, "unused.jsonl"))
    wins = [x for x in trades if float(x.get("net_pnl_usd", 0)) > 0]
    losses = [x for x in trades if float(x.get("net_pnl_usd", 0)) <= 0]
    net = sum(float(x.get("net_pnl_usd", 0)) for x in trades)
    by_reason = Counter(x.get("exit_reason", "unknown") for x in trades)
    decision_counts = Counter(x.get("decision", "unknown") for x in decisions)
    tape_mints = {x.get("mint") for x in tape}
    delays = [float(x.get("entry_delay_seconds", 0)) for x in entries]
    summary = {
        "launches_detected": len(launches),
        "sol_quoted_launches": sum(1 for x in launches if x.get("quote_mint") == "SOL"),
        "launches_with_tape": len(tape_mints),
        "tape_trades": len(tape),
        "first_buy_decisions": dict(decision_counts),
        "paper_entries": len(entries),
        "closed_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(trades)) if trades else None,
        "net_pnl_usd": round(net, 6),
        "average_entry_delay_seconds": mean(delays) if delays else None,
        "exit_reasons": dict(by_reason),
        "unclosed_entries": max(0, len(entries) - len(trades)),
        "limitations": [
            "Paper-only next-observed-transaction fills; not a guaranteed executable quote.",
            "Launch capture is limited to Pump.fun create events decoded by the configured websocket feed.",
            "Only SOL-quoted launches are eligible for this baseline; other quote types are recorded as observe-only.",
            "One open simulated position at a time; no creator/funder graph, bundle classification, or advanced launch filters in V1.",
            "Price is derived from virtual bonding-curve reserves; P&L includes configured fee, slippage and fixed gas assumptions.",
            "A single one-hour observation is exploratory, not evidence of profitability.",
        ],
    }
    with open(os.path.join(args.out, "sniper_report.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    lines = [
        "# Pump.fun Launch Sniper V1 — Paper Report", "",
        "## Coverage",
        f"- Launches detected: **{summary['launches_detected']}**",
        f"- SOL-quoted launches: **{summary['sol_quoted_launches']}**",
        f"- Launches with tape: **{summary['launches_with_tape']}**",
        f"- Recorded trade events: **{summary['tape_trades']}**",
        "", "## Paper results",
        f"- Entries: **{summary['paper_entries']}**",
        f"- Closed trades: **{summary['closed_trades']}**",
        f"- Wins / losses: **{summary['wins']} / {summary['losses']}**",
        f"- Win rate: **{summary['win_rate']:.1%}**" if summary["win_rate"] is not None else "- Win rate: n/a",
        f"- Net simulated P&L: **${summary['net_pnl_usd']:.4f}**",
        f"- Average signal-to-fill delay: **{summary['average_entry_delay_seconds']:.2f}s**" if summary["average_entry_delay_seconds"] is not None else "- Average signal-to-fill delay: n/a",
        f"- Unclosed entries at shutdown: **{summary['unclosed_entries']}**",
        "", "## Exit reasons",
    ]
    lines.extend(f"- {k}: {v}" for k, v in sorted(by_reason.items()))
    lines += ["", "## Decision counts"]
    lines.extend(f"- {k}: {v}" for k, v in sorted(decision_counts.items()))
    lines += ["", "## Limitations"] + [f"- {x}" for x in summary["limitations"]]
    with open(os.path.join(args.out, "sniper_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
