#!/usr/bin/env python3
"""Build downloadable per-coin and missed-opportunity reports from a paper observation."""
from __future__ import annotations

import argparse
import csv
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


def write_csv(path: str, rows: list[dict], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = sorted({key for row in rows for key in row})
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            cooked = {k: json.dumps(v, separators=(",", ":")) if isinstance(v, (dict, list)) else v
                      for k, v in row.items()}
            writer.writerow(cooked)


def stats(rows: list[dict]) -> dict:
    vals = [float(x.get("net_per_1usd", 0)) for x in rows]
    return {
        "trades": len(vals), "wins": sum(x > 0 for x in vals),
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
    coins = read_jsonl(os.path.join(args.data_dir, "coins_paper.jsonl"))
    summaries = read_jsonl(os.path.join(args.data_dir, "coin_summaries_paper.jsonl"))
    decisions = read_jsonl(os.path.join(args.data_dir, "coin_decisions_paper.jsonl"))
    tape = read_jsonl(os.path.join(args.data_dir, "coin_tape_paper.jsonl"))
    raw_tape = read_jsonl(os.path.join(args.data_dir, "coin_raw_tape_paper.jsonl"))

    # Existing counterfactual: a configured lane signalled, but the bankroll gate rejected it.
    sig_index = {(s.get("mint"), s.get("lane"), int(s.get("t_plus", -1))): s for s in signals}
    risk_missed = []
    for t in shadow:
        key = (t.get("mint"), t.get("lane"), int(t.get("entry_t_plus", -1)))
        s = sig_index.get(key)
        if s and not s.get("taken", False):
            risk_missed.append({
                "symbol": t.get("symbol"), "mint": t.get("mint"), "lane": t.get("lane"),
                "entry_t_plus": t.get("entry_t_plus"), "gate_reason": s.get("gate"),
                "mcap_usd": s.get("mcap"), "exit": t.get("exit"),
                "net_per_1usd": t.get("net_per_1usd"), "mfe": t.get("mfe"), "mae": t.get("mae"),
            })

    # For every rejected decision, measure what the observed tape did afterwards.
    tape_by_mint = defaultdict(list)
    for row in tape:
        tape_by_mint[row.get("mint")].append(row)
    missed = []
    reason_counts = Counter()
    for d in decisions:
        if d.get("decision") == "signal":
            continue
        reasons = d.get("reasons") or ["unknown"]
        for reason in reasons:
            reason_counts[reason] += 1
        later = [x for x in tape_by_mint.get(d.get("mint"), [])
                 if int(x.get("t_plus", 0)) > int(d.get("t_plus", 0))]
        if not later or not d.get("spot"):
            continue
        entry = float(d["spot"])
        peak = max(later, key=lambda x: float(x.get("price_usd") or 0))
        last = max(later, key=lambda x: int(x.get("t_plus", 0)))
        peak_px, last_px = float(peak["price_usd"]), float(last["price_usd"])
        # Conservative fixed-cost model at $1; peak exit is an upper-bound diagnostic, not an executable strategy.
        net_peak = ((0.9875 ** 2) * peak_px / entry - 1) - 0.0285
        net_end = ((0.9875 ** 2) * last_px / entry - 1) - 0.0285
        missed.append({
            "mint": d.get("mint"), "symbol": d.get("symbol"), "decision_t_plus": d.get("t_plus"),
            "market_cap_usd": d.get("mcap"), "rejection_reasons": reasons,
            "lane_checks": d.get("lane_checks"), "entry_price_usd": entry,
            "future_peak_t_plus": peak.get("t_plus"), "future_peak_price_usd": peak_px,
            "future_end_t_plus": last.get("t_plus"), "future_end_price_usd": last_px,
            "hypothetical_net_at_peak_per_1usd_upper_bound": round(net_peak, 5),
            "hypothetical_net_at_observation_end_per_1usd": round(net_end, 5),
        })
    profitable_missed = sorted(
        [x for x in missed if x["hypothetical_net_at_observation_end_per_1usd"] > 0],
        key=lambda x: x["hypothetical_net_at_observation_end_per_1usd"], reverse=True
    )
    risk_profitable = sorted([x for x in risk_missed if float(x.get("net_per_1usd") or 0) > 0],
                             key=lambda x: float(x["net_per_1usd"]), reverse=True)

    write_csv(os.path.join(args.out, "coins.csv"), coins)
    write_csv(os.path.join(args.out, "coin_summaries.csv"), summaries)
    write_csv(os.path.join(args.out, "coin_raw_tape.csv"), raw_tape)
    write_csv(os.path.join(args.out, "coin_decisions.csv"), decisions)
    write_csv(os.path.join(args.out, "missed_opportunities.csv"), missed)
    write_csv(os.path.join(args.out, "profitable_missed_opportunities.csv"), profitable_missed)
    write_csv(os.path.join(args.out, "risk_gate_missed_trades.csv"), risk_missed)

    by_lane = {lane: stats([x for x in shadow if x.get("lane") == lane])
               for lane in sorted({x.get("lane", "?") for x in shadow})}
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(), "mode": "paper_only",
        "warning": "Post-decision peak is an upper-bound diagnostic, not an executable fill or proof of profitability. End-price estimates still omit latency and market impact.",
        "coins_detected": len(coins), "coins_with_summaries": len(summaries),
        "coins_with_priced_trades": sum(int(x.get("trade_count", 0)) > 0 for x in summaries),
        "coin_tape_rows": len(tape), "raw_coin_trade_rows": len(raw_tape), "entry_window_decisions": len(decisions),
        "rejection_reason_counts": dict(reason_counts),
        "rejected_decisions_with_later_observations": len(missed),
        "rejected_decisions_profitable_at_observation_end": len(profitable_missed),
        "signals_seen": len(signals),
        "signals_rejected_by_risk_gate": sum(1 for x in signals if not x.get("taken", False)),
        "completed_shadow_trades": len(shadow), "bankroll_gated_trades": len(taken),
        "shadow_performance": stats(shadow), "bankroll_performance": stats(taken), "by_lane": by_lane,
        "risk_gate_missed_completed": len(risk_missed), "risk_gate_missed_profitable": len(risk_profitable),
        "limitations": [
            "Only coins whose create event was observed by this WebSocket feed can be analyzed; missed create events cannot be reconstructed.",
            "Decision diagnostics explain configured Lane K/B and shared guard rejections at each completed second in the entry window.",
            "A future peak is an upper bound and must not be interpreted as a realizable trade; use observation-end return as the less optimistic comparison.",
            "The one-hour run is a diagnostic sample, not enough to establish a profitable edge or safely tune thresholds.",
            "Counterfactual calculations use the configured fee and fixed gas assumptions, not measured fills, real slippage, MEV or latency.",
        ],
    }
    with open(os.path.join(args.out, "hourly_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    lines = [
        "# Pump.fun per-coin paper observation", "",
        f"- Generated UTC: {report['generated_utc']}", "- Mode: paper only; no transactions sent.", "",
        "## Collection summary", "",
        f"- Coins detected: {len(coins)}", f"- Coins with completed summaries: {len(summaries)}",
        f"- Coins with priced trades: {report['coins_with_priced_trades']}",
        f"- Stored priced trade observations: {len(tape)}", f"- Stored raw on-chain trade events: {len(raw_tape)}",
        f"- Entry-window decision samples: {len(decisions)}", "",
        "## Why entries were rejected", "",
        "| Reason | Samples |", "|---|---:|",
    ]
    lines += [f"| {k} | {v} |" for k, v in reason_counts.most_common()]
    lines += ["", "## Rejected decisions with positive observation-end return", "",
              "| Token | Mint | t+ | Rejection reason | Net at t+600 on $1 | Peak upper bound |",
              "|---|---|---:|---|---:|---:|"]
    for x in profitable_missed[:100]:
        lines.append(f"| {x.get('symbol') or ''} | {x.get('mint')} | {x.get('decision_t_plus')} | "
                     f"{', '.join(x.get('rejection_reasons') or [])} | "
                     f"{x.get('hypothetical_net_at_observation_end_per_1usd')} | "
                     f"{x.get('hypothetical_net_at_peak_per_1usd_upper_bound')} |")
    if not profitable_missed:
        lines.append("| None recorded | | | | | |")
    lines += ["", "## Risk-gate missed signals", "",
              f"- Completed shadow trades rejected by bankroll/risk gate: {len(risk_missed)}",
              f"- Profitable among those completed shadow trades: {len(risk_profitable)}", "",
              "## Existing lane shadow performance", "",
              "| Lane | Trades | Win rate | Avg net per $1 | Total net per $1 |",
              "|---|---:|---:|---:|---:|"]
    for lane, x in by_lane.items():
        lines.append(f"| {lane} | {x['trades']} | {x['win_rate']} | {x['avg_net_per_1usd']} | {x['total_net_per_1usd']} |")
    lines += ["", "## Interpretation limits", ""] + [f"- {x}" for x in report["limitations"]]
    with open(os.path.join(args.out, "hourly_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("Wrote per-coin CSV exports and hourly_report.json/.md")


if __name__ == "__main__":
    main()
