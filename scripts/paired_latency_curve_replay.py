#!/usr/bin/env python3
"""Curve-quote paired latency lanes using their already-selected delayed fills.

Research only. This reuses the paired replay's actual entry and exit signatures.
It deliberately uses the curve replay's after_observed policy so it does not
apply a second latency delay to trades that are already latency-delayed.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict

from pumpbot.curve_replay import (
    MAX_FORMULA_REL_ERROR,
    _replay_one,
    _summarize,
    read_jsonl,
)

LANE_SUFFIXES = ("wallet_lock", "wallet_lock_flow_gate")


def _paired_trade_for_curve(trade: dict) -> dict:
    """Map the paired lane's selected fill signatures to curve replay inputs."""
    return {
        "mint": trade.get("mint"),
        "symbol": trade.get("symbol"),
        "entry_signature": trade.get("entry_signature"),
        # This is the paired replay's delayed exit fill, not its trigger.
        "exit_signature": trade.get("exit_signature"),
        "net_pnl_usd": trade.get("net_pnl_usd"),
        "market_move_at_exit": trade.get("market_move_at_exit"),
    }


def replay_paired_trade(trade: dict, mint_tape: list[dict],
                        signature_indices: dict[str, int],
                        complete_slots: set[int], *, sol_usd: float,
                        stake_usd: float, gas_usd: float,
                        max_formula_error: float) -> dict:
    """Curve-quote the paired replay's exact selected entry and exit signatures."""
    curve_trade = _paired_trade_for_curve(trade)
    replay = _replay_one(
        curve_trade, mint_tape, signature_indices, complete_slots,
        sol_usd=sol_usd, stake_usd=stake_usd, gas_usd=gas_usd,
        max_formula_error=max_formula_error, fill_policy="after_observed",
    )
    if replay.get("status") == "ok":
        replay.update({
            "paired_signal_signature": trade.get("signal_signature"),
            "paired_entry_signature": trade.get("entry_signature"),
            "paired_exit_trigger_signature": trade.get("exit_trigger_signature"),
            "paired_exit_signature": trade.get("exit_signature"),
            "paired_entry_slot": trade.get("entry_slot"),
            "paired_exit_trigger_slot": trade.get("exit_trigger_slot"),
            "paired_exit_slot": trade.get("exit_slot"),
            "paired_recorded_pnl_usd": trade.get("net_pnl_usd"),
            "paired_exit_reason": trade.get("exit_reason"),
        })
    return replay


def _lane_delay_ms(lane_name: str) -> int:
    return int(lane_name.split("_")[1].removesuffix("ms"))


def build_report(data_dir: str, out_dir: str, *, paired_file: str,
                 sol_usd: float, stake_usd: float = 1.0,
                 gas_usd: float = 0.0285,
                 max_formula_error: float = MAX_FORMULA_REL_ERROR,
                 tape_filename: str = "sniper_tape_block_ordered_sniper.jsonl") -> dict:
    if sol_usd <= 0 or stake_usd <= 0 or gas_usd < 0:
        raise ValueError("SOL/USD and stake must be positive; gas cannot be negative")
    with open(paired_file, encoding="utf-8") as handle:
        paired = json.load(handle)

    tape = read_jsonl(os.path.join(data_dir, tape_filename))
    launches = {
        row["mint"]: row for row in read_jsonl(
            os.path.join(data_dir, "sniper_launches_sniper.jsonl")
        ) if row.get("mint")
    }
    complete_by_mint = defaultdict(set)
    for event in read_jsonl(os.path.join(data_dir, "sniper_launch_events_sniper.jsonl")):
        if event.get("event") == "bonding_curve_complete" and event.get("mint"):
            complete_by_mint[event["mint"]].add(int(event.get("slot") or 0))

    by_mint, signatures = defaultdict(list), defaultdict(dict)
    for row in tape:
        if row.get("quote_mint") != "SOL":
            continue
        if not row.get("virtual_sol_reserves_raw") or not row.get("virtual_token_reserves_raw"):
            continue
        mint, signature = row.get("mint"), row.get("signature")
        if not mint or not signature:
            continue
        signatures[mint][signature] = len(by_mint[mint])
        by_mint[mint].append(row)

    lane_summaries, lane_details = {}, {}
    all_paths = paired.get("trade_details") or {}
    for lane_name, lane_data in all_paths.items():
        if not lane_name.endswith(LANE_SUFFIXES):
            continue
        replayed, skipped = [], Counter()
        for trade in lane_data.get("trades") or []:
            mint = trade.get("mint")
            if mint not in by_mint:
                skipped["mint_missing_from_sol_tape"] += 1
                continue
            result = replay_paired_trade(
                trade, by_mint[mint], signatures[mint], complete_by_mint[mint],
                sol_usd=sol_usd, stake_usd=stake_usd, gas_usd=gas_usd,
                max_formula_error=max_formula_error,
            )
            if result.get("status") == "ok":
                replayed.append(result)
            else:
                skipped[result.get("status", "unknown_skip")] += 1
        lane_summaries[lane_name] = _summarize(
            lane_data.get("trades") or [], replayed, skipped
        )
        lane_details[lane_name] = replayed

    result = {
        "model": "counterfactual Pump curve replay of paired latency lane selected transaction fills",
        "paper_only": True,
        "strategy_decisions_changed": False,
        "paired_replay_input_transactions": paired.get("input_transactions"),
        "paired_replay_file": os.path.basename(paired_file),
        "tape_filename": tape_filename,
        "sol_usd_assumption": sol_usd,
        "stake_usd": stake_usd,
        "gas_usd_round_trip": gas_usd,
        "max_formula_relative_error": max_formula_error,
        "fill_policy": "after_observed at the paired replay's already-delayed entry and exit transaction signatures; no second latency delay is added",
        "lanes": lane_summaries,
        "trades": lane_details,
        "method": [
            "Reuse the paired latency replay's exact entry_signature and exit_signature selected by its delay, wallet-lock, and flow-gate logic.",
            "Use the paired trade's exit_signature (the delayed exit fill), not exit_trigger_signature, for the curve sell quote.",
            "Quote immediately after the already-selected entry and exit transaction marks using the existing exact integer Pump curve quote logic.",
            "Replay all intervening observed curve trades against the counterfactual position and reject reserve discontinuities, curve completion, and unsupported cashback.",
            "Keep wallet-lock-only and wallet-lock-plus-positive-flow lanes separate at each latency.",
        ],
        "limitations": [
            "This is not a confirmed on-chain fill or transaction-inclusion simulation; actual submission, leader inclusion, priority bidding, and failed transactions are not recorded.",
            "The paired latency model selects delayed transactions from event timestamps and later-slot rules; curve quoting those marks does not establish that a user's transaction could have been included at that point.",
            "The paired positive-flow gate is exactly the existing gate: at least one buy, positive net SOL flow, and no creator sell in the selected entry transaction. It does not require signal-buyer overlap.",
            "The paired lanes overlap in mints and transaction paths. Do not add their P&Ls or treat them as independent samples.",
            "SOL/USD is a supplied reference assumption for converting the USD stake into lamports.",
            "Curve-completion and cashback exclusions and reserve-continuity checks are inherited from the existing curve replay.",
        ],
    }

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "paired_latency_curve_replay.json"), "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)

    lines = [
        "# Paired latency lanes: Pump curve execution replay", "",
        f"- SOL/USD assumption: {sol_usd:.4f}",
        f"- Stake: {stake_usd:.2f} USD",
        f"- Round-trip gas assumption: {gas_usd:.4f} USD",
        "- The paired replay's selected delayed entry and exit signatures are quoted directly. No additional latency delay is applied.",
        "",
        "| Delay | Variant | Paired trades | Curve replayed | W/L | Curve P&L | PF | Skipped | Unique mints |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for lane_name, summary in lane_summaries.items():
        variant = "wallet lock + flow gate" if lane_name.endswith("wallet_lock_flow_gate") else "wallet lock"
        lines.append(
            f"| {_lane_delay_ms(lane_name) / 1000:.1f}s | {variant} | "
            f"{summary['trades_in_lane']} | {summary['replayed_trades']} | "
            f"{summary['wins']}/{summary['losses']} | "
            f"${summary['curve_replay_net_pnl_usd']:.4f} | "
            f"{summary['profit_factor'] if summary['profit_factor'] is not None else 'n/a'} | "
            f"{summary['skipped_trades']} | "
            f"{len({row['mint'] for row in lane_details[lane_name]})} |"
        )
    lines += ["", "## Per-trade results", ""]
    for lane_name, trades in lane_details.items():
        lines += [f"### {lane_name}", "",
                  "| Mint | Entry signature | Exit signature | Trigger signature | Curve P&L | Paired P&L |",
                  "|---|---|---|---|---:|---:|"]
        for trade in trades:
            lines.append(
                f"| {trade['mint']} | {trade['paired_entry_signature']} | "
                f"{trade['paired_exit_signature']} | "
                f"{trade.get('paired_exit_trigger_signature') or 'n/a'} | "
                f"${trade['curve_replay_pnl_usd']:.5f} | "
                f"${float(trade.get('paired_recorded_pnl_usd') or 0):.5f} |"
            )
        lines.append("")
    lines += ["## Method and limitations", ""]
    lines.extend(f"- {item}" for item in result["method"])
    lines += ["", "### Limitations", ""]
    lines.extend(f"- {item}" for item in result["limitations"])
    with open(os.path.join(out_dir, "paired_latency_curve_replay.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--out", default="./report")
    parser.add_argument("--paired-file", default="./report/paired_latency_replay.json")
    parser.add_argument("--sol-usd", type=float, required=True)
    parser.add_argument("--stake-usd", type=float, default=1.0)
    parser.add_argument("--gas-usd", type=float, default=0.0285)
    parser.add_argument("--tape-file", default="sniper_tape_block_ordered_sniper.jsonl")
    parser.add_argument("--max-formula-error", type=float, default=MAX_FORMULA_REL_ERROR)
    args = parser.parse_args()
    report = build_report(
        args.data_dir, args.out, paired_file=args.paired_file,
        sol_usd=args.sol_usd, stake_usd=args.stake_usd,
        gas_usd=args.gas_usd, tape_filename=args.tape_file,
        max_formula_error=args.max_formula_error,
    )
    for lane, summary in report["lanes"].items():
        print(
            f"{lane}: replayed={summary['replayed_trades']}/{summary['trades_in_lane']} "
            f"W/L={summary['wins']}/{summary['losses']} "
            f"net={summary['curve_replay_net_pnl_usd']:.4f} USD "
            f"skipped={summary['skipped_trades']}"
        )


if __name__ == "__main__":
    main()
