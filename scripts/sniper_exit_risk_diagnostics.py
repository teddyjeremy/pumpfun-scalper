#!/usr/bin/env python3
"""Describe sell pressure before recorded sniper exit triggers.

This is an observational diagnostic only. It does not change strategy decisions,
produce simulated fills, or treat a later-slot market mark as an executable fill.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict

LANES = {
    "500ms": "sniper_latency_0500ms_trades_sniper.jsonl",
    "1000ms": "sniper_latency_1000ms_trades_sniper.jsonl",
    "1500ms": "sniper_latency_1500ms_trades_sniper.jsonl",
}


def _read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path}:{line_number}: {exc}") from exc
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _pct(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * percentile)]


def _mark_at_or_after(events: list[dict], start_index: int, end_index: int,
                      min_slot: int) -> dict | None:
    for event in events[start_index:end_index]:
        if int(event.get("slot") or 0) >= min_slot:
            return event
    return None


def _analyze_trade(trade: dict, events: list[dict], signature_index: dict[str, int]) -> dict:
    entry_index = signature_index.get(trade.get("entry_signature"))
    trigger_index = signature_index.get(trade.get("exit_trigger_signature"))
    base = {
        "mint": trade.get("mint"),
        "symbol": trade.get("symbol"),
        "entry_signature": trade.get("entry_signature"),
        "exit_trigger_signature": trade.get("exit_trigger_signature"),
        "latency_seconds": trade.get("latency_seconds"),
        "exit_reason": trade.get("exit_reason"),
        "recorded_net_pnl_usd": trade.get("net_pnl_usd"),
    }
    if entry_index is None or trigger_index is None:
        return {**base, "status": "entry_or_trigger_missing_from_tape", "sell_events": []}
    if trigger_index <= entry_index:
        return {**base, "status": "invalid_entry_trigger_order", "sell_events": []}

    entry_price = float(trade.get("entry_price_sol") or 0)
    entry_slot = int(trade.get("entry_slot") or 0)
    trigger_slot = int(trade.get("exit_trigger_slot") or 0)
    if entry_price <= 0:
        return {**base, "status": "invalid_entry_price", "sell_events": []}

    sells = []
    # Exclude the original trigger transaction: it cannot be a prior warning.
    for index in range(entry_index + 1, trigger_index):
        event = events[index]
        if event.get("side") != "sell":
            continue
        previous = events[index - 1]
        previous_reserve = int(previous.get("virtual_sol_reserves_raw") or 0)
        quote_amount = int(event.get("sol_amount_raw") or 0)
        if previous_reserve <= 0 or quote_amount <= 0:
            continue
        slot = int(event.get("slot") or 0)
        next_mark = _mark_at_or_after(events, index + 1, trigger_index, slot + 1)
        price = float(event.get("price_sol") or 0)
        if price <= 0:
            continue
        next_price = float(next_mark.get("price_sol") or 0) if next_mark else 0.0
        sells.append({
            "signature": event.get("signature"),
            "slot": slot,
            "block_timestamp": event.get("block_timestamp"),
            "trader": event.get("trader"),
            "is_creator_sell": bool(event.get("trader") and event.get("trader") == event.get("creator")),
            "sell_sol": quote_amount / 1_000_000_000,
            "sell_vs_pre_event_virtual_sol_reserve_pct": quote_amount / previous_reserve * 100.0,
            "move_from_entry_pct": (price / entry_price - 1.0) * 100.0,
            "next_later_slot_signature": next_mark.get("signature") if next_mark else None,
            "next_later_slot": int(next_mark.get("slot") or 0) if next_mark else None,
            "next_later_slot_move_from_entry_pct": (next_price / entry_price - 1.0) * 100.0 if next_price else None,
            "next_later_slot_move_from_sell_pct": (next_price / price - 1.0) * 100.0 if next_price else None,
            "slots_until_original_exit_trigger": trigger_slot - slot,
            "seconds_until_original_exit_trigger": (
                float(trade.get("exit_observed_at_epoch") or 0)
                - float(event.get("block_timestamp") or 0)
            ),
        })

    largest = max(sells, key=lambda row: row["sell_vs_pre_event_virtual_sol_reserve_pct"]) if sells else None
    return {
        **base,
        "status": "ok",
        "entry_slot": entry_slot,
        "trigger_slot": trigger_slot,
        "sell_event_count_before_trigger": len(sells),
        "creator_sell_event_count": sum(row["is_creator_sell"] for row in sells),
        "largest_sell_event": largest,
        "sell_events": sells,
    }


def build_report(data_dir: str, out_dir: str) -> dict:
    tape = _read_jsonl(os.path.join(data_dir, "sniper_tape_sniper.jsonl"))
    by_mint: dict[str, list[dict]] = defaultdict(list)
    for row in tape:
        if (row.get("quote_mint") == "SOL" and row.get("mint") and row.get("signature")
                and row.get("virtual_sol_reserves_raw") and row.get("virtual_token_reserves_raw")):
            by_mint[row["mint"]].append(row)

    lane_reports = {}
    all_trade_paths = []
    missing_lane_files = []
    for lane, filename in LANES.items():
        path = os.path.join(data_dir, filename)
        if not os.path.exists(path):
            missing_lane_files.append(filename)
            continue
        trades = _read_jsonl(path)
        details = []
        for trade in trades:
            events = by_mint.get(trade.get("mint"), [])
            index = {event["signature"]: i for i, event in enumerate(events)}
            detail = _analyze_trade(trade, events, index)
            detail["lane"] = lane
            details.append(detail)
            all_trade_paths.append(detail)

        valid = [row for row in details if row["status"] == "ok"]
        sell_events = [event for row in valid for event in row["sell_events"]]
        largest_per_trade = [row["largest_sell_event"] for row in valid if row["largest_sell_event"]]
        ratios = [event["sell_vs_pre_event_virtual_sol_reserve_pct"] for event in sell_events]
        next_moves = [event["next_later_slot_move_from_sell_pct"] for event in sell_events
                      if event["next_later_slot_move_from_sell_pct"] is not None]
        lane_reports[lane] = {
            "trade_rows": len(trades),
            "matched_trade_paths": len(valid),
            "unmatched_or_invalid_trade_paths": len(trades) - len(valid),
            "trade_paths_with_pre_trigger_sells": sum(bool(row["sell_events"]) for row in valid),
            "pre_trigger_sell_events": len(sell_events),
            "creator_sell_events": sum(event["is_creator_sell"] for event in sell_events),
            "sell_reserve_fraction_percentiles": {
                "p50": _pct(ratios, 0.50), "p75": _pct(ratios, 0.75),
                "p90": _pct(ratios, 0.90), "p95": _pct(ratios, 0.95),
            },
            "next_later_slot_move_from_sell_percentiles": {
                "p50": _pct(next_moves, 0.50), "p25": _pct(next_moves, 0.25),
                "p10": _pct(next_moves, 0.10),
            },
            "largest_sell_per_trade": largest_per_trade,
            "trades": details,
        }

    # Lane overlap is substantial; this is an opportunity count, not a P&L sum.
    unique = {}
    for row in all_trade_paths:
        if row.get("entry_signature"):
            unique[(row.get("mint"), row["entry_signature"])] = row

    report = {
        "model": "observed transaction sell-pressure diagnostics before recorded exit trigger",
        "paper_only": True,
        "strategy_decisions_changed": False,
        "tape_rows": len(tape),
        "sol_quote_mints": len(by_mint),
        "unique_entry_opportunities_across_lanes": len(unique),
        "lane_overlap_warning": "Latency lanes overlap. Do not sum lane counts or P&L; deduplicated opportunities are not necessarily statistically independent.",
        "missing_lane_files": missing_lane_files,
        "lanes": lane_reports,
        "method": [
            "Use only SOL-quoted tape events with captured virtual reserves.",
            "For each latency-lane trade, inspect sell transactions strictly after its entry signature and strictly before its original exit-trigger signature.",
            "Normalize each sell's recorded SOL amount by the previous observed transaction's virtual SOL reserve.",
            "Record price movement at the sell and the first observed transaction in a strictly later slot, if that event occurs before the original exit trigger.",
            "Report every qualifying pre-trigger sell event; no sell-size threshold is used to select events.",
            "Deduplicate opportunities by mint and entry signature only for the overlap count; lane-specific exit paths remain separate.",
        ],
        "limitations": [
            "This is descriptive market-tape analysis, not a counterfactual trade or fill simulation.",
            "A later-slot observed transaction is a market mark, not proof that an exit order could have been included or filled there.",
            "SOL amount divided by pre-event virtual SOL reserves is a normalized transaction-size proxy, not exact sell-side reserve impact.",
            "The tape's captured event order is preserved; missing or delayed feed events can affect the apparent sequence.",
            "Historical runs without complete fee and launch-mode metadata remain subject to the curve-replay limitations.",
        ],
    }

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "sniper_exit_risk_diagnostics.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    lines = [
        "# Sniper exit-risk diagnostics",
        "",
        "Observed sell transactions strictly between each latency-lane entry and original exit trigger.",
        "This report is descriptive only; it does not change strategy decisions or simulate executable fills.",
        "",
        f"- Tape rows: {len(tape):,}",
        f"- SOL-quoted mints: {len(by_mint):,}",
        f"- Unique entry opportunities across lanes: {len(unique):,}",
        "- Latency lanes overlap; do not sum their trade counts or P&L.",
        "",
        "| Lane | Trade paths | Matched | Paths with pre-trigger sells | Sell events | Creator sells | Median sell/reserve % | Median next-later-slot move from sell % |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for lane, summary in lane_reports.items():
        p50_sell = summary["sell_reserve_fraction_percentiles"]["p50"]
        p50_move = summary["next_later_slot_move_from_sell_percentiles"]["p50"]
        fmt = lambda value: "n/a" if value is None else f"{value:.3f}"
        lines.append(
            f"| {lane} | {summary['trade_rows']} | {summary['matched_trade_paths']} | "
            f"{summary['trade_paths_with_pre_trigger_sells']} | {summary['pre_trigger_sell_events']} | "
            f"{summary['creator_sell_events']} | {fmt(p50_sell)} | {fmt(p50_move)} |"
        )
    lines += [
        "",
        "## Interpretation safeguards",
        "",
        "- The sell/reserve percentage is a normalized transaction-size proxy, not a guaranteed price-impact estimate.",
        "- The next-later-slot mark is not a confirmed executable fill.",
        "- A sell event in the original exit-trigger transaction is excluded because it cannot be a prior warning.",
        "- Lane overlap means these rows are not independent observations.",
        "- No entry filter, exit threshold, stop-loss, take-profit, or wallet policy is changed by this report.",
        "",
        "## Method and limitations",
        "",
    ]
    lines.extend(f"- {item}" for item in report["method"])
    lines.append("")
    lines.extend(f"- {item}" for item in report["limitations"])
    with open(os.path.join(out_dir, "sniper_exit_risk_diagnostics.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--out", default="./report")
    args = parser.parse_args()
    report = build_report(args.data_dir, args.out)
    for lane, summary in report["lanes"].items():
        print(
            f"{lane}: matched={summary['matched_trade_paths']}/{summary['trade_rows']} "
            f"pre-trigger sells={summary['pre_trigger_sell_events']} "
            f"paths with sells={summary['trade_paths_with_pre_trigger_sells']}"
        )


if __name__ == "__main__":
    main()
