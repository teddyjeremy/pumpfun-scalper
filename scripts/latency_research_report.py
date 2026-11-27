#!/usr/bin/env python3
"""Report isolated latency-aware sniper shadow lanes from one observation."""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from statistics import median


def read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--out", default="./report")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    result = {}
    for ms in (500, 1000, 1500):
        prefix = f"sniper_latency_{ms:04d}ms"
        entries = read_jsonl(os.path.join(args.data_dir, f"{prefix}_entries_sniper.jsonl"))
        trades = read_jsonl(os.path.join(args.data_dir, f"{prefix}_trades_sniper.jsonl"))
        decisions = read_jsonl(os.path.join(args.data_dir, f"{prefix}_decisions_sniper.jsonl"))
        wins = [x for x in trades if float(x.get("net_pnl_usd", 0)) > 0]
        losses = [x for x in trades if float(x.get("net_pnl_usd", 0)) <= 0]
        positive = sum(float(x.get("net_pnl_usd", 0)) for x in wins)
        negative = abs(sum(float(x.get("net_pnl_usd", 0)) for x in losses))
        ordered = sorted(trades, key=lambda x: float(x.get("exit_observed_at_epoch", 0)))
        streak = max_streak = 0
        for row in ordered:
            if float(row.get("net_pnl_usd", 0)) <= 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        entry_delays = [float(x.get("entry_latency_seconds", 0)) for x in entries]
        exit_delays = [float(x.get("exit_latency_seconds", 0)) for x in trades]
        same_slot_entries = sum(int(x.get("entry_slot", 0)) <= int(x.get("signal_slot", 0)) for x in entries)
        same_slot_exits = sum(int(x.get("exit_slot", 0)) <= int(x.get("exit_trigger_slot", 0)) for x in trades)
        counts = Counter(x.get("decision", "unknown") for x in decisions)
        result[prefix] = {
            "assumed_latency_seconds": ms / 1000,
            "entries": len(entries), "closed_trades": len(trades),
            "unclosed_entries": max(0, len(entries) - len(trades)),
            "wins": len(wins), "losses": len(losses),
            "win_rate": len(wins) / len(trades) if trades else None,
            "net_pnl_usd": round(sum(float(x.get("net_pnl_usd", 0)) for x in trades), 6),
            "profit_factor": round(positive / negative, 4) if negative else None,
            "maximum_loss_streak": max_streak,
            "median_entry_latency_seconds": round(median(entry_delays), 6) if entry_delays else None,
            "median_exit_latency_seconds": round(median(exit_delays), 6) if exit_delays else None,
            "same_or_earlier_slot_entries": same_slot_entries,
            "same_or_earlier_slot_exits": same_slot_exits,
            "exit_reasons": dict(Counter(x.get("exit_reason", "unknown") for x in trades)),
            "decisions": dict(counts),
        }

    json_path = os.path.join(args.out, "latency_research_report.json")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)

    lines = [
        "# Latency-aware wallet-guard research",
        "",
        "All lanes are isolated paper simulations. Existing strategy lanes are unchanged.",
        "An entry must occur in a later Solana slot and after the assumed response delay.",
        "An exit must occur in a later slot after the assumed delay from the TP, SL, or time-cap trigger.",
        "Time-cap triggers do not liquidate at stale marks; positions remain open until an eligible later transaction.",
        "Signal selection mirrors the wallet guard's multi-buyer heuristic. Positive flow and creator-sell status are recorded, not used as new gates.",
        "",
        "| Assumed delay | Entries | Closed | Unclosed | W/L | Win rate | Net P&L | PF | Max loss streak |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for prefix, row in result.items():
        wr = f"{row['win_rate']:.1%}" if row["win_rate"] is not None else "n/a"
        pf = f"{row['profit_factor']:.2f}" if row["profit_factor"] is not None else "n/a"
        lines.append(
            f"| {row['assumed_latency_seconds']:.1f}s | {row['entries']} | {row['closed_trades']} | "
            f"{row['unclosed_entries']} | {row['wins']}/{row['losses']} | {wr} | "
            f"${row['net_pnl_usd']:.4f} | {pf} | {row['maximum_loss_streak']} |"
        )
    for prefix, row in result.items():
        lines += [
            "",
            f"## {row['assumed_latency_seconds']:.1f}s lane",
            f"- Median observed entry delay: {row['median_entry_latency_seconds']}",
            f"- Median observed exit delay: {row['median_exit_latency_seconds']}",
            f"- Same/earlier-slot entries: {row['same_or_earlier_slot_entries']}",
            f"- Same/earlier-slot exits: {row['same_or_earlier_slot_exits']}",
            f"- Exit reasons: {row['exit_reasons']}",
            f"- Decisions: {row['decisions']}",
        ]
    lines += [
        "",
        "## Interpretation limits",
        "- These are delayed reserve-mark proxies, not confirmed executable fills or chain-submitted transactions.",
        "- Callback receive time is used as the start of assumed execution delay; real signing, RPC, priority-fee, and confirmation delays can differ.",
        "- A later slot is a minimum ordering constraint, not proof of a fill at the recorded price.",
        "- Do not select a winning delay from one session. Compare across independent sessions and retain unclosed positions in the report.",
    ]
    with open(os.path.join(args.out, "latency_research_report.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
