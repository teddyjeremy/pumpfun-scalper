#!/usr/bin/env python3
"""Describe how each latency-lane decision changes before its later-slot fill.

Research only. Reads the canonical ordered tape and existing curve replay output;
never changes a signal, fill, or strategy decision.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from statistics import median


def read_jsonl(path: str) -> list[dict]:
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _positive_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def summarize_transaction(events: list[dict]) -> dict:
    """Summarize all Pump trade events emitted by one transaction signature."""
    if not events:
        raise ValueError("transaction events cannot be empty")
    buys = [row for row in events if row.get("side") == "buy"]
    sells = [row for row in events if row.get("side") == "sell"]
    buy_sol = sum(int(row.get("sol_amount_raw") or 0) for row in buys) / 1e9
    sell_sol = sum(int(row.get("sol_amount_raw") or 0) for row in sells) / 1e9
    sides = {row.get("side") for row in events if row.get("side") in ("buy", "sell")}
    traders_buy = {row.get("trader") for row in buys
                   if row.get("trader") and row.get("trader") != row.get("creator")}
    traders_sell = {row.get("trader") for row in sells
                    if row.get("trader") and row.get("trader") != row.get("creator")}
    last = events[-1]
    return {
        "signature": last.get("signature"),
        "slot": int(last.get("slot") or 0),
        "block_timestamp": last.get("block_timestamp"),
        "event_count": len(events),
        "side": "mixed" if len(sides) > 1 else next(iter(sides), "unknown"),
        "buy_sol": buy_sol,
        "sell_sol": sell_sol,
        "net_flow_sol": buy_sol - sell_sol,
        "distinct_external_buyers": len(traders_buy),
        "distinct_external_sellers": len(traders_sell),
        "price_sol": _positive_float(last.get("price_sol")),
    }


def _transaction_index(tape: list[dict]) -> dict[tuple[str, str], dict]:
    grouped = defaultdict(list)
    for row in tape:
        mint, signature = row.get("mint"), row.get("signature")
        if mint and signature and row.get("quote_mint") == "SOL":
            grouped[(str(mint), str(signature))].append(row)
    return {key: summarize_transaction(events) for key, events in grouped.items()}


def _transition(before: dict | None, after: dict | None, prefix: str) -> dict:
    if before is None or after is None:
        return {f"{prefix}_{name}": None for name in (
            "slot_gap", "price_move_pct", "fill_side", "fill_buy_sol",
            "fill_sell_sol", "fill_net_flow_sol", "fill_external_buyers",
            "fill_external_sellers")}
    start, end = before.get("price_sol"), after.get("price_sol")
    move = ((end / start) - 1.0) * 100.0 if start and end else None
    return {
        f"{prefix}_slot_gap": after["slot"] - before["slot"],
        f"{prefix}_price_move_pct": move,
        f"{prefix}_fill_side": after["side"],
        f"{prefix}_fill_buy_sol": after["buy_sol"],
        f"{prefix}_fill_sell_sol": after["sell_sol"],
        f"{prefix}_fill_net_flow_sol": after["net_flow_sol"],
        f"{prefix}_fill_external_buyers": after["distinct_external_buyers"],
        f"{prefix}_fill_external_sellers": after["distinct_external_sellers"],
    }


def build_diagnostics(replay: dict, tape: list[dict]) -> dict:
    """Join next-later-slot replay trades to their decision/fill transactions."""
    tx_index = _transaction_index(tape)
    lanes = {}
    for lane, trades in (replay.get("next_later_slot_trades") or {}).items():
        details = []
        missing = Counter()
        for trade in trades:
            mint = str(trade.get("mint") or "")
            keys = {
                "entry_decision": (mint, str(trade.get("entry_signature") or "")),
                "entry_fill": (mint, str(trade.get("entry_fill_signature") or "")),
                "exit_trigger": (mint, str(trade.get("exit_signature") or "")),
                "exit_fill": (mint, str(trade.get("exit_fill_signature") or "")),
            }
            tx = {name: tx_index.get(key) for name, key in keys.items()}
            for name, item in tx.items():
                if item is None:
                    missing[name] += 1
            row = {
                "mint": mint,
                "symbol": trade.get("symbol"),
                "curve_replay_pnl_usd": trade.get("curve_replay_pnl_usd"),
                "entry_decision_signature": trade.get("entry_signature"),
                "entry_fill_signature": trade.get("entry_fill_signature"),
                "exit_trigger_signature": trade.get("exit_signature"),
                "exit_fill_signature": trade.get("exit_fill_signature"),
                "entry_decision": tx["entry_decision"],
                "entry_fill": tx["entry_fill"],
                "exit_trigger": tx["exit_trigger"],
                "exit_fill": tx["exit_fill"],
            }
            row.update(_transition(tx["entry_decision"], tx["entry_fill"], "entry"))
            row.update(_transition(tx["exit_trigger"], tx["exit_fill"], "exit"))
            details.append(row)

        valid = [row for row in details if row.get("curve_replay_pnl_usd") is not None]
        pnls = [float(row["curve_replay_pnl_usd"]) for row in valid]
        entry_moves = [row["entry_price_move_pct"] for row in valid
                       if row.get("entry_price_move_pct") is not None]
        entry_flows = [row["entry_fill_net_flow_sol"] for row in valid
                       if row.get("entry_fill_net_flow_sol") is not None]
        sell_fills = [row for row in valid if row.get("entry_fill_side") == "sell"]
        buy_fills = [row for row in valid if row.get("entry_fill_side") == "buy"]
        mixed_fills = [row for row in valid if row.get("entry_fill_side") == "mixed"]
        lanes[lane] = {
            "trade_records": len(details),
            "unique_mints": len({row["mint"] for row in details}),
            "mapped_entry_decisions": sum(row["entry_decision"] is not None for row in details),
            "mapped_entry_fills": sum(row["entry_fill"] is not None for row in details),
            "mapped_exit_triggers": sum(row["exit_trigger"] is not None for row in details),
            "mapped_exit_fills": sum(row["exit_fill"] is not None for row in details),
            "missing_transaction_records": dict(missing),
            "wins": sum(pnl > 0 for pnl in pnls),
            "losses": sum(pnl <= 0 for pnl in pnls),
            "curve_replay_net_pnl_usd": round(sum(pnls), 6),
            "median_entry_price_move_pct": round(median(entry_moves), 6) if entry_moves else None,
            "median_entry_fill_net_flow_sol": round(median(entry_flows), 6) if entry_flows else None,
            "entry_fill_side_counts": {
                "buy": len(buy_fills), "sell": len(sell_fills), "mixed": len(mixed_fills),
            },
            "sell_fill_wins": sum(float(row["curve_replay_pnl_usd"]) > 0 for row in sell_fills),
            "sell_fill_losses": sum(float(row["curve_replay_pnl_usd"]) <= 0 for row in sell_fills),
            "trades": details,
        }
    return {
        "model": "descriptive decision-to-fill transaction diagnostics for existing next-later-slot replay trades",
        "paper_only": True,
        "strategy_decisions_changed": False,
        "lane_overlap_warning": "Lanes overlap in mints and events. Do not add lane P&Ls or treat lane records as independent samples.",
        "price_definition": "Last captured event price_sol in each transaction signature group; decision-to-fill movement is descriptive, not an executable quote.",
        "flow_definition": "Buy SOL minus sell SOL decoded from all Pump trade events in that transaction; creator wallets are excluded only from distinct external buyer/seller counts.",
        "lanes": lanes,
        "limitations": [
            "This report does not change or evaluate strategy rules; it describes already-recorded decisions and counterfactual replay fills.",
            "A later-slot replay fill is a sensitivity model, not a confirmed order submission or on-chain execution.",
            "Price movement uses captured reserve-derived transaction marks, not a wallet-specific executable quote.",
            "SOL flow is transaction-level decoded Pump trade volume, not proof of net market-wide order flow.",
            "The same mint may appear in multiple latency lanes; lane P&Ls and counts are overlapping variants.",
            "Only SOL-quoted tape transactions with a mint and signature can be mapped.",
        ],
    }


def write_report(result: dict, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "sniper_signal_fill_diagnostics.json"), "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    lines = [
        "# Sniper signal-to-fill diagnostics", "",
        "Research-only transaction diagnostics. No strategy rules or decisions are changed.", "",
        "| Lane | Records | Unique mints | Wins/Losses | Net replay P&L | Median decision-to-fill move | Median fill net flow | Buy/Sell/Mixed fills |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for lane, summary in result["lanes"].items():
        sides = summary["entry_fill_side_counts"]
        move, flow = summary["median_entry_price_move_pct"], summary["median_entry_fill_net_flow_sol"]
        move_text = f"{move:.3f}%" if move is not None else "n/a"
        flow_text = f"{flow:.4f} SOL" if flow is not None else "n/a"
        lines.append(
            f"| {lane} | {summary['trade_records']} | {summary['unique_mints']} | "
            f"{summary['wins']}/{summary['losses']} | {summary['curve_replay_net_pnl_usd']:.4f} USD | "
            f"{move_text} | {flow_text} | {sides['buy']}/{sides['sell']}/{sides['mixed']} |"
        )
    lines += ["", result["lane_overlap_warning"], "", "## Per-trade detail", ""]
    for lane, summary in result["lanes"].items():
        lines += [f"### {lane}", "", "| Symbol | Entry move | Fill side | Fill net SOL | Exit move | Replay P&L |", "|---|---:|---|---:|---:|---:|"]
        for row in summary["trades"]:
            def fmt(value, suffix=""):
                return "n/a" if value is None else f"{float(value):.3f}{suffix}"
            lines.append(
                f"| {row.get('symbol') or row['mint'][:8]} | "
                f"{fmt(row.get('entry_price_move_pct'), '%')} | "
                f"{row.get('entry_fill_side') or 'n/a'} | "
                f"{fmt(row.get('entry_fill_net_flow_sol'))} | "
                f"{fmt(row.get('exit_price_move_pct'), '%')} | "
                f"{fmt(row.get('curve_replay_pnl_usd'))} USD |"
            )
        lines.append("")
    lines += ["## Limitations", ""]
    lines.extend(f"- {item}" for item in result["limitations"])
    with open(os.path.join(out_dir, "sniper_signal_fill_diagnostics.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--report-dir", default="./report")
    parser.add_argument("--tape-file", default="sniper_tape_block_ordered_sniper.jsonl")
    parser.add_argument("--replay-file", default="sniper_curve_execution_replay.json")
    args = parser.parse_args()
    with open(os.path.join(args.report_dir, args.replay_file), encoding="utf-8") as handle:
        replay = json.load(handle)
    tape = read_jsonl(os.path.join(args.data_dir, args.tape_file))
    result = build_diagnostics(replay, tape)
    write_report(result, args.report_dir)
    for lane, summary in result["lanes"].items():
        print(f"{lane}: mapped={summary['mapped_entry_fills']}/{summary['trade_records']} "
              f"median_entry_move={summary['median_entry_price_move_pct']}% "
              f"median_fill_flow={summary['median_entry_fill_net_flow_sol']} SOL")


if __name__ == "__main__":
    main()
