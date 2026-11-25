#!/usr/bin/env python3
"""Evaluate fixed entry filters against completed ungated shadow outcomes.

This is diagnostic research only. It does not alter K/B signals, paper entries,
risk limits, exits, or the wallet. Outcomes are available only where the
existing ungated shadow engine completed a matching trade.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
from collections import defaultdict


def read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def filter_a(f: dict) -> bool:
    """Pullback candidate: 5-20% below high, high made 3-120s ago."""
    dip = f.get("dip")
    age = f.get("secs_high")
    return dip is not None and age is not None and 0.05 <= float(dip) <= 0.20 and 3 <= int(age) <= 120


def filter_b(f: dict) -> bool:
    """Participation/flow candidate: steady, broad participation and buy-led flow."""
    return (
        bool(f.get("steady"))
        and int(f.get("nw30") or 0) >= 15
        and float(f.get("uniq60") or 0) >= 0.45
        and float(f.get("b10") or 0) >= float(f.get("s10") or 0)
    )


FILTERS = {"filter_a_pullback": filter_a, "filter_b_participation": filter_b}


def summarize(rows: list[dict]) -> dict:
    values = [float(r["net_per_1usd"]) for r in rows]
    wins = sum(v > 0 for v in values)
    losses = sum(v < 0 for v in values)
    return {
        "trades_with_matched_shadow_outcome": len(values),
        "wins": wins,
        "losses": losses,
        "breakeven": len(values) - wins - losses,
        "win_rate": round(wins / len(values), 4) if values else None,
        "losses_below_wins": bool(losses < wins) if values else False,
        "net_per_1usd": round(sum(values), 5),
        "average_net_per_1usd": round(statistics.mean(values), 5) if values else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="./data")
    ap.add_argument("--out", default="./report")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    signals = read_jsonl(os.path.join(args.data_dir, "signals_paper.jsonl"))
    shadow = read_jsonl(os.path.join(args.data_dir, "shadow_paper.jsonl"))
    sig_index = {
        (s.get("mint"), s.get("lane"), int(s.get("t_plus", -1))): s
        for s in signals
    }
    outcomes = defaultdict(list)
    for trade in shadow:
        key = (trade.get("mint"), trade.get("lane"), int(trade.get("entry_t_plus", -1)))
        outcomes[key].append(trade)

    rows = []
    for s in signals:
        key = (s.get("mint"), s.get("lane"), int(s.get("t_plus", -1)))
        matches = outcomes.get(key, [])
        feat = s.get("feat") or {}
        row = {
            "mint": s.get("mint"), "symbol": s.get("symbol"), "lane": s.get("lane"),
            "entry_t_plus": s.get("t_plus"), "baseline_taken": s.get("taken"),
            "dip": feat.get("dip"), "secs_high": feat.get("secs_high"),
            "steady": feat.get("steady"), "new_buyers_30s": feat.get("nw30"),
            "unique_traders_60s": feat.get("uniq60"), "trades_10s": feat.get("n10"),
            "buy_usd_10s": feat.get("b10"), "sell_usd_10s": feat.get("s10"),
            "matched_shadow_outcome": bool(matches),
        }
        for name, predicate in FILTERS.items():
            passed = predicate(feat)
            row[name + "_pass"] = passed
            if matches:
                row[name + "_shadow_net_per_1usd"] = round(
                    sum(float(x.get("net_per_1usd") or 0) for x in matches), 5
                )
                row[name + "_shadow_wins"] = sum(float(x.get("net_per_1usd") or 0) > 0 for x in matches)
                row[name + "_shadow_losses"] = sum(float(x.get("net_per_1usd") or 0) < 0 for x in matches)
        for trade in matches:
            rows.append({
                **row,
                "shadow_exit": trade.get("exit"),
                "shadow_hold_s": trade.get("hold_s"),
                "shadow_net_per_1usd": trade.get("net_per_1usd"),
                "filter_a_included": predicate_a := FILTERS["filter_a_pullback"](feat),
                "filter_b_included": FILTERS["filter_b_participation"](feat),
            })

    # One row per matched shadow trade; retain every signal separately in a companion CSV.
    signal_rows = []
    for s in signals:
        feat = s.get("feat") or {}
        key = (s.get("mint"), s.get("lane"), int(s.get("t_plus", -1)))
        signal_rows.append({
            "mint": s.get("mint"), "symbol": s.get("symbol"), "lane": s.get("lane"),
            "entry_t_plus": s.get("t_plus"), "baseline_taken": s.get("taken"),
            "matched_shadow_outcome": bool(outcomes.get(key)),
            "filter_a_pass": FILTERS["filter_a_pullback"](feat),
            "filter_b_pass": FILTERS["filter_b_participation"](feat),
        })

    with open(os.path.join(args.out, "entry_filter_signals.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(signal_rows[0]) if signal_rows else ["mint"])
        w.writeheader()
        w.writerows(signal_rows)

    matched = []
    for trade in shadow:
        key = (trade.get("mint"), trade.get("lane"), int(trade.get("entry_t_plus", -1)))
        s = sig_index.get(key)
        if not s:
            continue
        feat = s.get("feat") or {}
        matched.append((trade, feat))

    summary = {
        "mode": "paper_only_diagnostic",
        "signals_seen": len(signals),
        "shadow_trades": len(shadow),
        "signals_with_matched_shadow_outcomes": len(matched),
        "baseline_matched_shadow": summarize([t for t, _ in matched]),
        "filters": {},
        "filter_definitions": {
            "filter_a_pullback": "dip from recent high 5%-20%, with recent high 3-120 seconds ago",
            "filter_b_participation": "steady=true, at least 15 new buyers in 30s, unique-trader ratio >=0.45, and 10s buy USD >= sell USD",
        },
        "limitations": [
            "Filters are evaluated on existing strategy signals and matched ungated shadow outcomes; they do not change baseline K/B decisions.",
            "A signal without a completed matching shadow trade has no outcome and is excluded from win/loss statistics.",
            "The existing shadow engine is sequential and permits only its configured position/entry cadence. This fixed-signal comparison does not simulate how skipping an entry could free the engine to take a later signal.",
            "The filters are hypotheses, not optimized or validated thresholds. This one-hour sample is not evidence of a durable edge.",
            "Paper shadow outcomes use the existing simulated fill and exit model; they are not live execution results.",
        ],
    }
    for name, predicate in FILTERS.items():
        selected = [(t, feat) for t, feat in matched if predicate(feat)]
        summary["filters"][name] = {
            "signals_passed": sum(predicate(s.get("feat") or {}) for s in signals),
            "signals_rejected": sum(not predicate(s.get("feat") or {}) for s in signals),
            **summarize([t for t, _ in selected]),
        }
    with open(os.path.join(args.out, "entry_filter_comparison.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    with open(os.path.join(args.out, "entry_filter_comparison.md"), "w", encoding="utf-8") as f:
        f.write("# Parallel entry-filter research\n\n")
        f.write("Paper-only diagnostics. Baseline K/B decisions and exits are unchanged.\n\n")
        f.write("| Variant | Signals pass | Signals rejected | Matched trades | Wins | Losses | Win rate | Net / $1 | Losses < wins? |\n")
        f.write("|---|---:|---:|---:|---:|---:|---:|---:|---|\n")
        base = summary["baseline_matched_shadow"]
        f.write(f"| Baseline shadow | {len(signals)} | 0 | {base['trades_with_matched_shadow_outcome']} | {base['wins']} | {base['losses']} | {base['win_rate']} | {base['net_per_1usd']} | {base['losses_below_wins']} |\n")
        for name, x in summary["filters"].items():
            f.write(f"| {name} | {x['signals_passed']} | {x['signals_rejected']} | {x['trades_with_matched_shadow_outcome']} | {x['wins']} | {x['losses']} | {x['win_rate']} | {x['net_per_1usd']} | {x['losses_below_wins']} |\n")
        f.write("\n## Filter definitions\n\n")
        for name, definition in summary["filter_definitions"].items():
            f.write(f"- **{name}:** {definition}.\n")
        f.write("\n## Limitations\n\n")
        for item in summary["limitations"]:
            f.write(f"- {item}\n")
    print("Wrote entry_filter_signals.csv and entry_filter_comparison.json/.md")


if __name__ == "__main__":
    main()
