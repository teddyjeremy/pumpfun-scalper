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
    shadow_entries = read_jsonl(os.path.join(args.data_dir, "sniper_shadow_entries_sniper.jsonl"))
    shadow_trades = read_jsonl(os.path.join(args.data_dir, "sniper_shadow_trades_sniper.jsonl"))
    cluster_entries = read_jsonl(os.path.join(args.data_dir, "sniper_cluster_entries_sniper.jsonl"))
    cluster_trades = read_jsonl(os.path.join(args.data_dir, "sniper_cluster_trades_sniper.jsonl"))
    cluster_decisions = read_jsonl(os.path.join(args.data_dir, "sniper_cluster_decisions_sniper.jsonl"))
    cluster_portfolio_entries = read_jsonl(os.path.join(args.data_dir, "sniper_cluster_portfolio_entries_sniper.jsonl"))
    cluster_portfolio_trades = read_jsonl(os.path.join(args.data_dir, "sniper_cluster_portfolio_trades_sniper.jsonl"))
    cluster_portfolio_decisions = read_jsonl(os.path.join(args.data_dir, "sniper_cluster_portfolio_decisions_sniper.jsonl"))
    tx_summaries = read_jsonl(os.path.join(args.data_dir, "sniper_transactions_sniper.jsonl"))
    tx_entries = read_jsonl(os.path.join(args.data_dir, "sniper_tx_entries_sniper.jsonl"))
    tx_trades = read_jsonl(os.path.join(args.data_dir, "sniper_tx_trades_sniper.jsonl"))
    tx_decisions = read_jsonl(os.path.join(args.data_dir, "sniper_tx_decisions_sniper.jsonl"))
    v2_entries = read_jsonl(os.path.join(args.data_dir, "sniper_v2_entries_sniper.jsonl"))
    v2_trades = read_jsonl(os.path.join(args.data_dir, "sniper_v2_trades_sniper.jsonl"))
    v2_decisions = read_jsonl(os.path.join(args.data_dir, "sniper_v2_decisions_sniper.jsonl"))
    wins = [x for x in trades if float(x.get("net_pnl_usd", 0)) > 0]
    losses = [x for x in trades if float(x.get("net_pnl_usd", 0)) <= 0]
    net = sum(float(x.get("net_pnl_usd", 0)) for x in trades)
    by_reason = Counter(x.get("exit_reason", "unknown") for x in trades)
    decision_counts = Counter(x.get("decision", "unknown") for x in decisions)

    tape_mints = {x.get("mint") for x in tape}
    wallet_stats = {}
    for row in shadow_trades:
        wallet = row.get("signal_wallet") or "unknown"
        item = wallet_stats.setdefault(wallet, {"trades": 0, "wins": 0, "losses": 0, "net_pnl_usd": 0.0})
        pnl = float(row.get("net_pnl_usd", 0))
        item["trades"] += 1
        item["wins"] += int(pnl > 0)
        item["losses"] += int(pnl <= 0)
        item["net_pnl_usd"] += pnl
    wallet_ranked = sorted(
        ({"wallet": k, **v, "net_pnl_usd": round(v["net_pnl_usd"], 6)} for k, v in wallet_stats.items()),
        key=lambda x: (x["net_pnl_usd"], x["trades"]), reverse=True
    )
    capacity_results = {}
    ordered = sorted(shadow_trades, key=lambda x: (int(x.get("entry_timestamp", 0)), int(x.get("exit_timestamp", 0))))
    for capacity in (1, 3, 5):
        active = []
        selected = []
        for row in ordered:
            start = int(row.get("entry_timestamp", 0))
            end = int(row.get("exit_timestamp", start))
            active = [finish for finish in active if finish > start]
            if len(active) < capacity:
                selected.append(row)
                active.append(end)
        capacity_results[str(capacity)] = {
            "trades": len(selected),
            "wins": sum(float(x.get("net_pnl_usd", 0)) > 0 for x in selected),
            "losses": sum(float(x.get("net_pnl_usd", 0)) <= 0 for x in selected),
            "net_pnl_usd": round(sum(float(x.get("net_pnl_usd", 0)) for x in selected), 6),
        }
    delays = [float(x.get("entry_delay_seconds", 0)) for x in entries]
    cluster_wins = [x for x in cluster_trades if float(x.get("net_pnl_usd", 0)) > 0]
    cluster_losses = [x for x in cluster_trades if float(x.get("net_pnl_usd", 0)) <= 0]
    cluster_buyer_counts = Counter(int(x.get("signal_transaction_buyer_count", 0)) for x in cluster_entries)
    cluster_decision_counts = Counter(x.get("decision", "unknown") for x in cluster_decisions)
    cluster_portfolio_wins = [x for x in cluster_portfolio_trades if float(x.get("net_pnl_usd", 0)) > 0]
    cluster_portfolio_losses = [x for x in cluster_portfolio_trades if float(x.get("net_pnl_usd", 0)) <= 0]
    cluster_portfolio_decision_counts = Counter(x.get("decision", "unknown") for x in cluster_portfolio_decisions)
    tx_wins = [x for x in tx_trades if float(x.get("net_pnl_usd", 0)) > 0]
    tx_losses = [x for x in tx_trades if float(x.get("net_pnl_usd", 0)) <= 0]
    tx_decision_counts = Counter(x.get("decision", "unknown") for x in tx_decisions)
    v2_wins = [x for x in v2_trades if float(x.get("net_pnl_usd", 0)) > 0]
    v2_losses = [x for x in v2_trades if float(x.get("net_pnl_usd", 0)) <= 0]
    v2_net = sum(float(x.get("net_pnl_usd", 0)) for x in v2_trades)
    v2_positive = sum(float(x.get("net_pnl_usd", 0)) for x in v2_trades if float(x.get("net_pnl_usd", 0)) > 0)
    v2_negative = abs(sum(float(x.get("net_pnl_usd", 0)) for x in v2_trades if float(x.get("net_pnl_usd", 0)) < 0))
    v2_exit_counts = Counter(x.get("exit_reason", "unknown") for x in v2_trades)
    v2_decision_counts = Counter(x.get("decision", "unknown") for x in v2_decisions)
    v2_ordered = sorted(v2_trades, key=lambda x: (int(x.get("exit_timestamp") or 0), int(x.get("exit_sequence") or 0)))
    v2_equity = 0.0
    v2_peak = 0.0
    v2_max_drawdown = 0.0
    for row in v2_ordered:
        v2_equity += float(row.get("net_pnl_usd", 0))
        v2_peak = max(v2_peak, v2_equity)
        v2_max_drawdown = min(v2_max_drawdown, v2_equity - v2_peak)
    tape_sol_missing_price = sum(1 for x in tape if x.get("quote_mint") == "SOL" and not x.get("price_sol"))
    tx_multi_event_count = sum(1 for x in tx_summaries if int(x.get("event_count") or 0) > 1)
    tx_missing_final_price = sum(1 for x in tx_summaries if not x.get("final_price_sol"))
    tx_abs_moves = [abs(float(x["intra_transaction_price_move"])) for x in tx_summaries if x.get("intra_transaction_price_move") is not None]
    tx_cohorts, tx_wallets = {}, {}
    for row in tx_trades:
        pnl = float(row.get("net_pnl_usd", 0))
        cohort = row.get("cohort_id") or "unknown"
        item = tx_cohorts.setdefault(cohort, {"trades": 0, "wins": 0, "losses": 0, "net_pnl_usd": 0.0, "buyer_wallets": row.get("signal_buyer_wallets", [])})
        item["trades"] += 1
        item["wins"] += int(pnl > 0)
        item["losses"] += int(pnl <= 0)
        item["net_pnl_usd"] += pnl
        for wallet in row.get("signal_buyer_wallets", []):
            w = tx_wallets.setdefault(wallet, {"trades": 0, "wins": 0, "losses": 0, "net_pnl_usd": 0.0})
            w["trades"] += 1
            w["wins"] += int(pnl > 0)
            w["losses"] += int(pnl <= 0)
            w["net_pnl_usd"] += pnl
    tx_cohort_ranked = sorted(({"cohort_id": k, **v, "net_pnl_usd": round(v["net_pnl_usd"], 6)} for k, v in tx_cohorts.items()), key=lambda x: (x["net_pnl_usd"], x["trades"]), reverse=True)
    tx_wallet_ranked = sorted(({"wallet": k, **v, "net_pnl_usd": round(v["net_pnl_usd"], 6)} for k, v in tx_wallets.items()), key=lambda x: (x["net_pnl_usd"], x["trades"]), reverse=True)
    tx_intramoves = [float(x["intra_transaction_price_move"]) for x in tx_summaries if x.get("intra_transaction_price_move") is not None]
    tx_multi_buyer = [x for x in tx_summaries if int(x.get("buyer_count", 0)) >= 2]
    summary = {
        "launches_detected": len(launches),
        "sol_quoted_launches": sum(1 for x in launches if x.get("quote_mint") == "SOL"),
        "launches_with_tape": len(tape_mints),
        "tape_trades": len(tape),
        "first_buy_decisions": dict(decision_counts),
        "paper_entries": len(entries),
        "independent_shadow_entries": len(shadow_entries),
        "independent_shadow_closed_trades": len(shadow_trades),
        "independent_shadow_wins": sum(float(x.get("net_pnl_usd", 0)) > 0 for x in shadow_trades),
        "independent_shadow_losses": sum(float(x.get("net_pnl_usd", 0)) <= 0 for x in shadow_trades),
        "independent_shadow_net_pnl_usd": round(sum(float(x.get("net_pnl_usd", 0)) for x in shadow_trades), 6),
        "multi_buyer_cluster_entries": len(cluster_entries),
        "multi_buyer_cluster_closed_trades": len(cluster_trades),
        "multi_buyer_cluster_wins": len(cluster_wins),
        "multi_buyer_cluster_losses": len(cluster_losses),
        "multi_buyer_cluster_win_rate": (len(cluster_wins) / len(cluster_trades)) if cluster_trades else None,
        "multi_buyer_cluster_net_pnl_usd": round(sum(float(x.get("net_pnl_usd", 0)) for x in cluster_trades), 6),
        "multi_buyer_cluster_signal_buyer_counts": dict(cluster_buyer_counts),
        "multi_buyer_cluster_decisions": dict(cluster_decision_counts),
        "multi_buyer_cluster_unclosed_entries": max(0, len(cluster_entries) - len(cluster_trades)),
        "multi_buyer_one_position_entries": len(cluster_portfolio_entries),
        "multi_buyer_one_position_closed_trades": len(cluster_portfolio_trades),
        "multi_buyer_one_position_wins": len(cluster_portfolio_wins),
        "multi_buyer_one_position_losses": len(cluster_portfolio_losses),
        "multi_buyer_one_position_win_rate": (len(cluster_portfolio_wins) / len(cluster_portfolio_trades)) if cluster_portfolio_trades else None,
        "multi_buyer_one_position_net_pnl_usd": round(sum(float(x.get("net_pnl_usd", 0)) for x in cluster_portfolio_trades), 6),
        "multi_buyer_one_position_unclosed_entries": max(0, len(cluster_portfolio_entries) - len(cluster_portfolio_trades)),
        "multi_buyer_one_position_decisions": dict(cluster_portfolio_decision_counts),
        "transaction_summary_count": len(tx_summaries),
        "transaction_multi_buyer_count": len(tx_multi_buyer),
        "transaction_unique_mints": len({x.get("mint") for x in tx_summaries}),
        "transaction_mean_intra_transaction_price_move": mean(tx_intramoves) if tx_intramoves else None,
        "transaction_lane_entries": len(tx_entries),
        "transaction_lane_closed_trades": len(tx_trades),
        "transaction_lane_wins": len(tx_wins),
        "transaction_lane_losses": len(tx_losses),
        "transaction_lane_win_rate": len(tx_wins) / len(tx_trades) if tx_trades else None,
        "transaction_lane_net_pnl_usd": round(sum(float(x.get("net_pnl_usd", 0)) for x in tx_trades), 6),
        "transaction_lane_unclosed_entries": max(0, len(tx_entries) - len(tx_trades)),
        "transaction_lane_decisions": dict(tx_decision_counts),
        "high_conviction_entries": len(v2_entries),
        "high_conviction_closed_trades": len(v2_trades),
        "high_conviction_wins": len(v2_wins),
        "high_conviction_losses": len(v2_losses),
        "high_conviction_win_rate": len(v2_wins) / len(v2_trades) if v2_trades else None,
        "high_conviction_net_pnl_usd": round(v2_net, 6),
        "high_conviction_profit_factor": round(v2_positive / v2_negative, 4) if v2_negative else None,
        "high_conviction_max_drawdown_usd": round(v2_max_drawdown, 6),
        "high_conviction_exit_reasons": dict(v2_exit_counts),
        "high_conviction_decisions": dict(v2_decision_counts),
        "high_conviction_unclosed_entries": max(0, len(v2_entries) - len(v2_trades)),
        "capture_quality": {
            "sol_tape_events_missing_price": tape_sol_missing_price,
            "transaction_multi_event_count": tx_multi_event_count,
            "transaction_missing_final_price_count": tx_missing_final_price,
            "transaction_mean_abs_intra_move": mean(tx_abs_moves) if tx_abs_moves else None,
        },
        "transaction_lane_top_cohorts": tx_cohort_ranked[:20],
        "transaction_lane_top_wallets": tx_wallet_ranked[:20],
        "portfolio_capacity_comparison": capacity_results,
        "first_buy_wallet_performance": wallet_ranked,
        "closed_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(trades)) if trades else None,
        "net_pnl_usd": round(net, 6),
        "average_entry_delay_seconds": mean(delays) if delays else None,
        "exit_reasons": dict(by_reason),
        "unclosed_entries": max(0, len(entries) - len(trades)),
        "limitations": [
            "Independent shadow lane is a counterfactual per-mint simulation; it assumes one position per mint and does not model shared capital constraints.",
            "Timer-driven max-hold exits use the last observed bonding-curve price; stale quote age is recorded and may not be executable.",
            "Portfolio capacity comparisons greedily schedule shadow trades by entry time and are not live fill simulations.",
            "Paper-only next-observed-transaction fills; not a guaranteed executable quote.",
            "Launch capture is limited to Pump.fun create events decoded by the configured websocket feed.",
            "Only SOL-quoted launches are eligible for this baseline; other quote types are recorded as observe-only.",
            "The multi-buyer research lane requires at least two distinct non-creator buyers in the signal transaction; this is a transaction-pattern heuristic, not proof of a coordinated bundle.",
            "The independent multi-buyer lane permits concurrent per-mint positions; the separate one-position multi-buyer portfolio lane enforces one shared slot.",
            "Both multi-buyer lanes are counterfactual paper research and do not change baseline entries or the broad independent shadow lane.",
            "Price is derived from virtual bonding-curve reserves; P&L includes configured fee, slippage and fixed gas assumptions.",
            "A single one-hour observation is exploratory, not evidence of profitability.",
            "Transaction-complete research aggregates all decoded events for a signature and uses the final observed reserve-derived price per mint; this remains a paper approximation, not an executable quote.",
            "Wallet-cohort results are descriptive within-session observations and do not establish wallet identity, coordination, or future performance.",
            "The high-conviction lane waits for a separate execution transaction after its confirmation transaction and uses conservative first/final price marks; it remains a paper fill proxy, not a guaranteed executable quote.",
            "The high-conviction confirmation thresholds are research hypotheses and require fresh out-of-sample validation; no profitability is established.",
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
        "", "## Capture quality",
        f"- SOL tape events missing a reserve-derived price: **{summary['capture_quality']['sol_tape_events_missing_price']}**",
        f"- Multi-event transaction summaries: **{summary['capture_quality']['transaction_multi_event_count']}**",
        f"- Transaction summaries missing a final price: **{summary['capture_quality']['transaction_missing_final_price_count']}**",
        f"- Mean absolute within-transaction price movement: **{summary['capture_quality']['transaction_mean_abs_intra_move']:.2%}**" if summary["capture_quality"]["transaction_mean_abs_intra_move"] is not None else "- Mean absolute within-transaction price movement: n/a",
        "", "## Paper results",
        f"- Entries: **{summary['paper_entries']}**",
        f"- Closed trades: **{summary['closed_trades']}**",
        f"- Wins / losses: **{summary['wins']} / {summary['losses']}**",
        f"- Win rate: **{summary['win_rate']:.1%}**" if summary["win_rate"] is not None else "- Win rate: n/a",
        f"- Net simulated P&L: **${summary['net_pnl_usd']:.4f}**",
        f"- Average signal-to-fill delay: **{summary['average_entry_delay_seconds']:.2f}s**" if summary["average_entry_delay_seconds"] is not None else "- Average signal-to-fill delay: n/a",
        f"- Unclosed entries at shutdown: **{summary['unclosed_entries']}**",
        "", "## Transaction-complete research",
        f"- Per-mint transaction summaries: **{summary['transaction_summary_count']}**",
        f"- Multi-buyer transactions: **{summary['transaction_multi_buyer_count']}**",
        f"- Unique mints represented: **{summary['transaction_unique_mints']}**",
        f"- Mean within-transaction price movement: **{summary['transaction_mean_intra_transaction_price_move']:.2%}**" if summary["transaction_mean_intra_transaction_price_move"] is not None else "- Mean within-transaction price movement: n/a",
        f"- Entries / closed trades: **{summary['transaction_lane_entries']} / {summary['transaction_lane_closed_trades']}**",
        f"- Wins / losses: **{summary['transaction_lane_wins']} / {summary['transaction_lane_losses']}**",
        f"- Win rate: **{summary['transaction_lane_win_rate']:.1%}**" if summary["transaction_lane_win_rate"] is not None else "- Win rate: n/a",
        f"- Net simulated P&L: **{summary['transaction_lane_net_pnl_usd']:.4f} USD**",
        f"- Unclosed entries: **{summary['transaction_lane_unclosed_entries']}**",
        f"- Decisions: **{summary['transaction_lane_decisions']}**",
        "", "### Top transaction-level buyer cohorts",
        *[f"- {x['buyer_wallets']}: {x['trades']} trades, {x['wins']} wins, {x['losses']} losses, net {x['net_pnl_usd']:.4f} USD" for x in tx_cohort_ranked[:10]],
        "", "### Top wallets participating in transaction-level entries",
        *[f"- {x['wallet']}: {x['trades']} trades, {x['wins']} wins, {x['losses']} losses, net {x['net_pnl_usd']:.4f} USD" for x in tx_wallet_ranked[:10]],
        "", "## High-conviction confirmation lane (new, isolated)",
        "- Rules: multi-buyer positive-flow signal; next transaction must have at least 2 buyers, at least 0.50 SOL buy volume, positive net flow, no creator sell, and at least 5% positive within-transaction price movement; a separate later positive-flow transaction is required for entry.",
        "- Entry mark uses the higher of first/final prices in the execution transaction; exit mark uses the lower, to reduce favourable within-transaction marking.",
        f"- Entries / closed trades: **{summary['high_conviction_entries']} / {summary['high_conviction_closed_trades']}**",
        f"- Wins / losses: **{summary['high_conviction_wins']} / {summary['high_conviction_losses']}**",
        f"- Win rate: **{summary['high_conviction_win_rate']:.1%}**" if summary["high_conviction_win_rate"] is not None else "- Win rate: n/a",
        f"- Net simulated P&L: **{summary['high_conviction_net_pnl_usd']:.4f} USD**",
        f"- Profit factor: **{summary['high_conviction_profit_factor']:.2f}**" if summary["high_conviction_profit_factor"] is not None else "- Profit factor: n/a",
        f"- Exit-order max drawdown: **{summary['high_conviction_max_drawdown_usd']:.4f} USD**",
        f"- Exit reasons: **{summary['high_conviction_exit_reasons']}**",
        f"- Decisions: **{summary['high_conviction_decisions']}**",
        f"- Unclosed entries: **{summary['high_conviction_unclosed_entries']}**",
        "", "## Independent shadow research",
        f"- Shadow entries: **{summary['independent_shadow_entries']}**",
        f"- Shadow closed trades: **{summary['independent_shadow_closed_trades']}**",
        f"- Shadow wins / losses: **{summary['independent_shadow_wins']} / {summary['independent_shadow_losses']}**",
        f"- Shadow net P&L: **${summary['independent_shadow_net_pnl_usd']:.4f}**",
        "", "### Multi-buyer signal transaction research",
        f"- Filtered entries: **{summary['multi_buyer_cluster_entries']}**",
        f"- Closed trades: **{summary['multi_buyer_cluster_closed_trades']}**",
        f"- Wins / losses: **{summary['multi_buyer_cluster_wins']} / {summary['multi_buyer_cluster_losses']}**",
        f"- Win rate: **{summary['multi_buyer_cluster_win_rate']:.1%}**" if summary["multi_buyer_cluster_win_rate"] is not None else "- Win rate: n/a",
        f"- Net simulated P&L: **${summary['multi_buyer_cluster_net_pnl_usd']:.4f}**",
        f"- Signal-transaction buyer counts: **{summary['multi_buyer_cluster_signal_buyer_counts']}**",
        f"- Decisions: **{summary['multi_buyer_cluster_decisions']}**",
        "", "### Multi-buyer one-position portfolio",
        f"- Entries: **{summary['multi_buyer_one_position_entries']}**",
        f"- Closed trades: **{summary['multi_buyer_one_position_closed_trades']}**",
        f"- Wins / losses: **{summary['multi_buyer_one_position_wins']} / {summary['multi_buyer_one_position_losses']}**",
        f"- Win rate: **{summary['multi_buyer_one_position_win_rate']:.1%}**" if summary["multi_buyer_one_position_win_rate"] is not None else "- Win rate: n/a",
        f"- Net simulated P&L: **${summary['multi_buyer_one_position_net_pnl_usd']:.4f}**",
        f"- Decisions: **{summary['multi_buyer_one_position_decisions']}**",
        "", "### Portfolio capacity comparison",
    ]
    lines.extend(f"- {cap} concurrent: {v['trades']} trades, {v['wins']} wins, {v['losses']} losses, net ${v['net_pnl_usd']:.4f}" for cap, v in capacity_results.items())
    lines += ["", "### First-buy wallet performance (top 10)"]
    lines.extend(f"- {x['wallet']}: {x['trades']} trades, {x['wins']} wins, {x['losses']} losses, net ${x['net_pnl_usd']:.4f}" for x in wallet_ranked[:10])
    lines += ["", "## Exit reasons",
    ]
    lines.extend(f"- {k}: {v}" for k, v in sorted(by_reason.items()))
    lines += ["", "## Decision counts"]
    lines.extend(f"- {k}: {v}" for k, v in sorted(decision_counts.items()))
    lines += ["", "## Multi-buyer lane decisions"]
    lines.extend(f"- {k}: {v}" for k, v in sorted(cluster_decision_counts.items()))
    lines += ["", "## Multi-buyer one-position portfolio decisions"]
    lines.extend(f"- {k}: {v}" for k, v in sorted(cluster_portfolio_decision_counts.items()))
    lines += ["", "## Limitations"] + [f"- {x}" for x in summary["limitations"]]
    with open(os.path.join(args.out, "sniper_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
