#!/usr/bin/env python3
"""Describe observed price paths around paired-latency time-cap exits.

This is a market-mark diagnostic only. It does not simulate executable exits,
change strategy decisions, or treat intrapath extrema as realizable fills.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict

LANES = (
    "latency_500ms_no_wallet_lock",
    "latency_500ms_wallet_lock",
    "latency_500ms_wallet_lock_flow_gate",
    "latency_1000ms_no_wallet_lock",
    "latency_1000ms_wallet_lock",
    "latency_1000ms_wallet_lock_flow_gate",
    "latency_1500ms_no_wallet_lock",
    "latency_1500ms_wallet_lock",
    "latency_1500ms_wallet_lock_flow_gate",
)


def _read_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path}:{line_number}: {exc}") from exc
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[round((len(values) - 1) * fraction)]


def _move(price: float, base: float) -> float | None:
    if price <= 0 or base <= 0:
        return None
    return (price / base - 1.0) * 100.0


def _analyze_trade(trade: dict, events: list[dict], index: dict[str, int]) -> dict:
    entry_i = index.get(trade.get("entry_signature"))
    trigger_i = index.get(trade.get("paired_exit_trigger_signature"))
    exit_i = index.get(trade.get("exit_signature"))
    base = {
        "mint": trade.get("mint"),
        "symbol": trade.get("symbol"),
        "entry_signature": trade.get("entry_signature"),
        "exit_trigger_signature": trade.get("paired_exit_trigger_signature"),
        "exit_signature": trade.get("exit_signature"),
        "exit_reason": trade.get("paired_exit_reason"),
        "curve_replay_pnl_usd": trade.get("curve_replay_pnl_usd"),
    }
    if None in (entry_i, trigger_i, exit_i):
        return {**base, "status": "entry_trigger_or_exit_missing"}
    if not entry_i < trigger_i <= exit_i:
        return {**base, "status": "invalid_signature_order"}

    entry, trigger, exit_event = events[entry_i], events[trigger_i], events[exit_i]
    entry_price = float(entry.get("price_sol") or 0)
    trigger_price = float(trigger.get("price_sol") or 0)
    exit_price = float(exit_event.get("price_sol") or 0)
    path = [row for row in events[entry_i:exit_i + 1] if float(row.get("price_sol") or 0) > 0]
    if not path or entry_price <= 0 or trigger_price <= 0 or exit_price <= 0:
        return {**base, "status": "invalid_path_price"}

    high = max(path, key=lambda row: float(row["price_sol"]))
    low = min(path, key=lambda row: float(row["price_sol"]))
    post_trigger = [row for row in path if int(row.get("slot") or 0) >= int(trigger.get("slot") or 0)]
    post_high = max(post_trigger, key=lambda row: float(row["price_sol"]))
    post_low = min(post_trigger, key=lambda row: float(row["price_sol"]))
    entry_ts = int(entry.get("block_timestamp") or 0)
    trigger_ts = int(trigger.get("block_timestamp") or 0)
    exit_ts = int(exit_event.get("block_timestamp") or 0)
    return {
        **base,
        "status": "ok",
        "entry_slot": int(entry.get("slot") or 0),
        "trigger_slot": int(trigger.get("slot") or 0),
        "exit_slot": int(exit_event.get("slot") or 0),
        "entry_to_trigger_seconds": max(0, trigger_ts - entry_ts),
        "trigger_to_exit_seconds": max(0, exit_ts - trigger_ts),
        "entry_to_exit_seconds": max(0, exit_ts - entry_ts),
        "trigger_to_exit_slot_gap": max(0, int(exit_event.get("slot") or 0) - int(trigger.get("slot") or 0)),
        "observed_path_rows": len(path),
        "entry_price_sol": entry_price,
        "trigger_price_sol": trigger_price,
        "exit_price_sol": exit_price,
        "max_favorable_move_from_entry_pct": _move(float(high["price_sol"]), entry_price),
        "max_adverse_move_from_entry_pct": _move(float(low["price_sol"]), entry_price),
        "exit_mark_move_from_entry_pct": _move(exit_price, entry_price),
        "max_favorable_move_after_trigger_pct": _move(float(post_high["price_sol"]), trigger_price),
        "max_adverse_move_after_trigger_pct": _move(float(post_low["price_sol"]), trigger_price),
        "exit_mark_move_from_trigger_pct": _move(exit_price, trigger_price),
        "max_favorable_signature": high.get("signature"),
        "max_adverse_signature": low.get("signature"),
        "post_trigger_high_signature": post_high.get("signature"),
        "post_trigger_low_signature": post_low.get("signature"),
        "entry_t_plus": entry.get("t_plus"),
        "trigger_t_plus": trigger.get("t_plus"),
        "exit_t_plus": exit_event.get("t_plus"),
    }


def build_report(data_dir: str, out_dir: str, paired_file: str) -> dict:
    tape_path = os.path.join(data_dir, "sniper_tape_block_ordered_sniper.jsonl")
    tape = _read_jsonl(tape_path)
    by_mint: dict[str, list[dict]] = defaultdict(list)
    for row in tape:
        if row.get("quote_mint") == "SOL" and row.get("mint") and row.get("signature"):
            by_mint[row["mint"]].append(row)

    paired = json.load(open(paired_file, encoding="utf-8"))
    lane_reports = {}
    for lane in LANES:
        details = []
        for trade in paired.get("trades", {}).get(lane, []):
            if trade.get("paired_exit_reason") != "time_cap":
                continue
            events = by_mint.get(trade.get("mint"), [])
            event_index = {row["signature"]: i for i, row in enumerate(events)}
            details.append(_analyze_trade(trade, events, event_index))
        valid = [row for row in details if row["status"] == "ok"]
        def values(key):
            return [float(row[key]) for row in valid if row.get(key) is not None]
        lane_reports[lane] = {
            "time_cap_trades": len(details),
            "matched_paths": len(valid),
            "unmatched_paths": len(details) - len(valid),
            "profitable_curve_replays": sum(float(row.get("curve_replay_pnl_usd") or 0) > 0 for row in valid),
            "curve_replay_net_pnl_usd": round(sum(float(row.get("curve_replay_pnl_usd") or 0) for row in valid), 6),
            "median_max_favorable_move_from_entry_pct": _percentile(values("max_favorable_move_from_entry_pct"), 0.5),
            "median_max_adverse_move_from_entry_pct": _percentile(values("max_adverse_move_from_entry_pct"), 0.5),
            "median_exit_mark_move_from_entry_pct": _percentile(values("exit_mark_move_from_entry_pct"), 0.5),
            "median_trigger_to_exit_seconds": _percentile(values("trigger_to_exit_seconds"), 0.5),
            "median_trigger_to_exit_slot_gap": _percentile(values("trigger_to_exit_slot_gap"), 0.5),
            "trades": details,
        }

    report = {
        "model": "observed transaction price-path diagnostics for paired curve-replay time-cap trades",
        "paper_only": True,
        "strategy_decisions_changed": False,
        "tape_file": os.path.basename(tape_path),
        "paired_curve_replay_file": os.path.basename(paired_file),
        "lanes": lane_reports,
        "method": [
            "Select only paired curve-replay trades whose original exit reason is time_cap.",
            "Locate the exact paired entry, original exit-trigger, and delayed exit-fill signatures in the canonical ordered tape.",
            "Measure observed transaction price_sol extrema from entry through delayed exit, and separately from trigger through delayed exit.",
            "Use the observed transaction price as a descriptive market mark; do not interpret extrema as executable exit quotes.",
            "Keep every latency and wallet-policy lane separate because trade paths overlap.",
        ],
        "limitations": [
            "Observed price_sol extrema are not curve-quoted liquidation proceeds and do not include fees, gas, or order inclusion.",
            "The maximum favorable mark may occur between trigger and delayed exit and may not be actionable.",
            "The paired exit signature is a selected observed transaction, not proof that a submitted exit could be included there.",
            "Missing tape events, feed timing, and same-slot ordering can affect observed path extrema.",
            "This report does not recommend or apply a new timer, take-profit, stop-loss, or trailing rule.",
        ],
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "sniper_timecap_path_diagnostics.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    lines = [
        "# Sniper time-cap path diagnostics",
        "",
        "Observed transaction price paths for paired curve-replay trades ending by time cap.",
        "Intrapat h extrema are market marks, not executable fills. Lanes overlap and must not be added.",
        "",
        "| Lane | Time-cap trades | Matched | Curve winners | Curve P&L | Median max favorable % | Median max adverse % | Median trigger-to-exit sec | Median slot gap |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for lane, row in lane_reports.items():
        fmt = lambda value: "n/a" if value is None else f"{value:.3f}"
        lines.append(
            f"| {lane} | {row['time_cap_trades']} | {row['matched_paths']} | "
            f"{row['profitable_curve_replays']} | {row['curve_replay_net_pnl_usd']:.6f} | "
            f"{fmt(row['median_max_favorable_move_from_entry_pct'])} | "
            f"{fmt(row['median_max_adverse_move_from_entry_pct'])} | "
            f"{fmt(row['median_trigger_to_exit_seconds'])} | "
            f"{fmt(row['median_trigger_to_exit_slot_gap'])} |"
        )
    lines += ["", "## Interpretation safeguards", ""]
    lines.extend(f"- {item}" for item in report["method"] + report["limitations"])
    with open(os.path.join(out_dir, "sniper_timecap_path_diagnostics.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--out", default="./report")
    parser.add_argument("--paired-file", default="./report/paired_latency_curve_replay.json")
    args = parser.parse_args()
    report = build_report(args.data_dir, args.out, args.paired_file)
    for lane, row in report["lanes"].items():
        print(f"{lane}: timecap={row['time_cap_trades']} matched={row['matched_paths']} "
              f"curve_winners={row['profitable_curve_replays']} pnl={row['curve_replay_net_pnl_usd']:.6f}")


if __name__ == "__main__":
    main()
