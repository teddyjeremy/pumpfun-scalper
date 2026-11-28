#!/usr/bin/env python3
"""Transaction-boundary curve-path diagnostics for paired sniper replays.

Research only. This script does not alter strategy decisions or submit transactions.
It reconstructs the counterfactual position after each complete canonical transaction
between the paired replay's selected entry and exit signatures.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from statistics import median

from pumpbot.curve_replay import (
    apply_external_trade,
    quote_buy_exact_quote_in,
    quote_sell_exact_tokens,
    read_jsonl,
    validate_observed_transition,
)

LAMPORTS_PER_SOL = 1_000_000_000
DEFAULT_LANE_SUFFIX = "wallet_lock_flow_gate"


def _write_csv(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if not rows:
        with open(path, "w", newline="", encoding="utf-8") as handle:
            handle.write("")
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _group_transactions(rows: list[dict]) -> list[list[dict]]:
    """Group contiguous canonical tape rows by signature (one atomic transaction)."""
    groups: list[list[dict]] = []
    for row in rows:
        signature = row.get("signature")
        if groups and groups[-1][0].get("signature") == signature:
            groups[-1].append(row)
        else:
            groups.append([row])
    return groups


def _pnl_usd(tokens: int, state: dict, fee_event: dict, *,
             sol_usd: float, stake_usd: float, gas_usd: float) -> float:
    sell = quote_sell_exact_tokens(
        tokens,
        int(state["virtual_quote_reserves"]),
        int(state["virtual_token_reserves"]),
        int(fee_event.get("fee_bps") or 0),
        int(fee_event.get("creator_fee_bps") or 0),
    )
    return sell["net_quote_out"] / LAMPORTS_PER_SOL * sol_usd - stake_usd - gas_usd


def _analyse_trade(trade: dict, mint_tape: list[dict],
                   signature_indices: dict[str, int], *,
                   sol_usd: float, stake_usd: float, gas_usd: float,
                   max_formula_error: float, lane: str) -> tuple[dict | None, list[dict], list[dict], str | None]:
    entry_signature = trade.get("paired_entry_signature") or trade.get("entry_signature")
    exit_signature = trade.get("paired_exit_signature") or trade.get("exit_signature")
    if not entry_signature or not exit_signature:
        return None, [], [], "missing_entry_or_exit_signature"
    if entry_signature not in signature_indices or exit_signature not in signature_indices:
        return None, [], [], "signature_missing_from_tape"

    # The signature index deliberately points to the final event row in a
    # transaction. A fill cannot occur between inner Pump events in one signature.
    entry_index = signature_indices[entry_signature]
    exit_index = signature_indices[exit_signature]
    if exit_index <= entry_index:
        return None, [], [], "invalid_entry_exit_order"

    entry_event = mint_tape[entry_index]
    exit_event = mint_tape[exit_index]
    entry_slot = int(entry_event.get("slot") or 0)
    exit_slot = int(exit_event.get("slot") or 0)
    if exit_slot <= entry_slot:
        return None, [], [], "exit_not_in_later_slot"

    interval = mint_tape[entry_index + 1:exit_index + 1]
    if any(int(row.get("cashback_bps") or 0) > 0 or
           int(row.get("cashback_fee_raw") or 0) > 0 for row in [entry_event, *interval]):
        return None, [], [], "unsupported_cashback_path"

    previous = entry_event
    max_error = 0.0
    for event in interval:
        check = validate_observed_transition(previous, event, max_formula_error)
        if not check.get("ok"):
            return None, [], [], check.get("reason") or "invalid_observed_transition"
        max_error = max(max_error, float(check.get("relative_error") or 0.0))
        previous = event

    vq = int(entry_event["virtual_sol_reserves_raw"])
    vt = int(entry_event["virtual_token_reserves_raw"])
    spend_lamports = int(round(stake_usd / sol_usd * LAMPORTS_PER_SOL))
    buy = quote_buy_exact_quote_in(
        spend_lamports, vq, vt,
        int(entry_event.get("fee_bps") or 0),
        int(entry_event.get("creator_fee_bps") or 0),
    )
    tokens = int(buy["tokens_out"])
    real_tokens = int(entry_event.get("real_token_reserves_raw") or 0)
    if real_tokens and tokens >= real_tokens:
        return None, [], [], "entry_would_complete_curve"

    state = {
        "virtual_quote_reserves": vq + int(buy["net_quote_in"]),
        "virtual_token_reserves": vt - tokens,
    }
    if real_tokens:
        state["real_token_reserves"] = real_tokens - tokens
        if state["real_token_reserves"] <= 0:
            return None, [], [], "entry_would_complete_curve"

    initial_pnl = _pnl_usd(
        tokens, state, entry_event, sol_usd=sol_usd,
        stake_usd=stake_usd, gas_usd=gas_usd,
    )
    initial_vsol = vq / LAMPORTS_PER_SOL
    entry_t_plus = float(entry_event.get("t_plus") or 0.0)

    transaction_rows: list[dict] = []
    for group in _group_transactions(interval):
        before = dict(state)
        before_vq = int(before["virtual_quote_reserves"])
        before_vt = int(before["virtual_token_reserves"])
        before_spot = before_vq / before_vt
        buy_lamports = sum(int(row.get("sol_amount_raw") or 0)
                           for row in group if row.get("side") == "buy")
        sell_lamports = sum(int(row.get("sol_amount_raw") or 0)
                            for row in group if row.get("side") == "sell")
        buy_tokens = sum(int(row.get("token_amount_raw") or 0)
                         for row in group if row.get("side") == "buy")
        sell_tokens = sum(int(row.get("token_amount_raw") or 0)
                          for row in group if row.get("side") == "sell")
        for event in group:
            state = apply_external_trade(state, event)

        last = group[-1]
        after_vq = int(state["virtual_quote_reserves"])
        after_vt = int(state["virtual_token_reserves"])
        after_spot = after_vq / after_vt
        pnl = _pnl_usd(
            tokens, state, last, sol_usd=sol_usd,
            stake_usd=stake_usd, gas_usd=gas_usd,
        )
        traders = {str(row.get("trader")) for row in group if row.get("trader")}
        slot = int(last.get("slot") or 0)
        transaction_rows.append({
            "lane": lane,
            "mint": trade.get("mint"),
            "symbol": trade.get("symbol"),
            "exit_reason": trade.get("paired_exit_reason"),
            "signature": last.get("signature"),
            "slot": slot,
            "slot_from_entry": slot - entry_slot,
            "elapsed_t_plus_seconds": float(last.get("t_plus") or 0.0) - entry_t_plus,
            "event_count": len(group),
            "side_mix": "".join("B" if row.get("side") == "buy" else "S"
                                for row in group),
            "buy_sol": buy_lamports / LAMPORTS_PER_SOL,
            "sell_sol": sell_lamports / LAMPORTS_PER_SOL,
            "net_sol": (buy_lamports - sell_lamports) / LAMPORTS_PER_SOL,
            "buy_tokens_raw": buy_tokens,
            "sell_tokens_raw": sell_tokens,
            "distinct_traders": len(traders),
            "virtual_sol_reserve_before": before_vq / LAMPORTS_PER_SOL,
            "virtual_token_reserve_before": before_vt,
            "buy_pressure_initial_reserve": buy_lamports / vq if vq else 0.0,
            "sell_pressure_initial_reserve": sell_lamports / vq if vq else 0.0,
            "buy_pressure_local_reserve": buy_lamports / before_vq if before_vq else 0.0,
            "sell_pressure_local_reserve": sell_lamports / before_vq if before_vq else 0.0,
            "sell_token_fraction_local_reserve": sell_tokens / before_vt if before_vt else 0.0,
            "spot_return_pct": (after_spot / before_spot - 1.0) * 100.0,
            "spot_before": before_spot,
            "spot_after": after_spot,
            "curve_pnl_usd": pnl,
            "protocol_fee_bps": int(last.get("fee_bps") or 0),
            "creator_fee_bps": int(last.get("creator_fee_bps") or 0),
        })

    if not transaction_rows:
        return None, [], [], "no_transactions_between_entry_and_exit"

    # Mark whether each adverse sell transaction recovered its pre-sell spot
    # before the selected exit transaction. Recovery is measured only at complete
    # transaction boundaries, never between events inside one signature.
    sell_recovery_rows: list[dict] = []
    for index, row in enumerate(transaction_rows):
        if row["sell_sol"] <= 0 or row["spot_return_pct"] >= 0:
            continue
        recovery_index = next(
            (j for j in range(index + 1, len(transaction_rows))
             if transaction_rows[j]["spot_after"] >= row["spot_before"]),
            None,
        )
        recovered = recovery_index is not None
        future = transaction_rows[index + 1:recovery_index + 1] if recovered else transaction_rows[index + 1:]
        sell_recovery_rows.append({
            "lane": lane,
            "mint": trade.get("mint"),
            "symbol": trade.get("symbol"),
            "exit_reason": trade.get("paired_exit_reason"),
            "sell_signature": row["signature"],
            "sell_slot": row["slot"],
            "sell_pressure_local_pct": row["sell_pressure_local_reserve"] * 100.0,
            "sell_pressure_initial_pct": row["sell_pressure_initial_reserve"] * 100.0,
            "sell_sol": row["sell_sol"],
            "spot_drop_pct": row["spot_return_pct"],
            "recovered_before_exit": recovered,
            "recovery_transaction_count": (recovery_index - index) if recovered else None,
            "recovery_slot_delta": (
                transaction_rows[recovery_index]["slot"] - row["slot"]
            ) if recovered else None,
            "buy_sol_until_recovery": sum(x["buy_sol"] for x in future) if recovered else None,
        })

    pnl_values = [row["curve_pnl_usd"] for row in transaction_rows]
    positive = [row for row in transaction_rows if row["curve_pnl_usd"] > 0]
    last = transaction_rows[-1]
    expected_pnl = float(trade.get("curve_replay_pnl_usd"))
    parity_error = last["curve_pnl_usd"] - expected_pnl
    if abs(parity_error) > 1e-8:
        return None, transaction_rows, sell_recovery_rows, "curve_exit_pnl_reconciliation_failed"

    buy_sol = sum(row["buy_sol"] for row in transaction_rows)
    sell_sol = sum(row["sell_sol"] for row in transaction_rows)
    adverse_sells = [row for row in sell_recovery_rows]
    recovered_sells = [row for row in adverse_sells if row["recovered_before_exit"]]
    trade_row = {
        "lane": lane,
        "mint": trade.get("mint"),
        "symbol": trade.get("symbol"),
        "exit_reason": trade.get("paired_exit_reason"),
        "entry_signature": entry_signature,
        "exit_trigger_signature": trade.get("paired_exit_trigger_signature"),
        "exit_signature": exit_signature,
        "entry_slot": entry_slot,
        "exit_slot": exit_slot,
        "unique_mint_key": trade.get("mint"),
        "transactions_replayed": len(transaction_rows),
        "events_replayed": sum(row["event_count"] for row in transaction_rows),
        "curve_exit_pnl_usd": last["curve_pnl_usd"],
        "source_curve_pnl_usd": expected_pnl,
        "curve_pnl_reconciliation_error_usd": parity_error,
        "initial_post_entry_pnl_usd": initial_pnl,
        "mfe_usd": max(pnl_values + [initial_pnl]),
        "mae_usd": min(pnl_values + [initial_pnl]),
        "first_positive_slot_delta": (
            positive[0]["slot_from_entry"] if positive else None
        ),
        "positive_checkpoint_fraction": len(positive) / len(transaction_rows),
        "external_buy_sol": buy_sol,
        "external_sell_sol": sell_sol,
        "external_net_sol": buy_sol - sell_sol,
        "buy_flow_initial_reserve_pct": buy_sol / initial_vsol * 100.0 if initial_vsol else None,
        "sell_flow_initial_reserve_pct": sell_sol / initial_vsol * 100.0 if initial_vsol else None,
        "net_flow_initial_reserve_pct": (buy_sol - sell_sol) / initial_vsol * 100.0 if initial_vsol else None,
        "sell_transactions": sum(row["sell_sol"] > 0 for row in transaction_rows),
        "adverse_sell_transactions": len(adverse_sells),
        "adverse_sell_recovery_rate": (
            len(recovered_sells) / len(adverse_sells) if adverse_sells else None
        ),
        "median_recovery_transactions": median(
            [row["recovery_transaction_count"] for row in recovered_sells]
        ) if recovered_sells else None,
        "median_recovery_slot_delta": median(
            [row["recovery_slot_delta"] for row in recovered_sells]
        ) if recovered_sells else None,
        "median_buy_sol_until_recovery": median(
            [row["buy_sol_until_recovery"] for row in recovered_sells]
        ) if recovered_sells else None,
        "max_observed_formula_relative_error": max_error,
    }
    return trade_row, transaction_rows, sell_recovery_rows, None


def _median(values):
    clean = [float(value) for value in values if value is not None]
    return median(clean) if clean else None


def build_report(data_dir: str, out_dir: str, *,
                 curve_report: str = "./report/paired_latency_curve_replay.json",
                 tape_filename: str = "sniper_tape_block_ordered_sniper.jsonl",
                 lane_suffix: str = DEFAULT_LANE_SUFFIX) -> dict:
    with open(curve_report, encoding="utf-8") as handle:
        curve = json.load(handle)
    tape = read_jsonl(os.path.join(data_dir, tape_filename))
    if not tape:
        raise FileNotFoundError(os.path.join(data_dir, tape_filename))

    by_mint: dict[str, list[dict]] = defaultdict(list)
    signature_indices: dict[str, dict[str, int]] = defaultdict(dict)
    for row in tape:
        if row.get("quote_mint") != "SOL":
            continue
        if not row.get("mint") or not row.get("signature"):
            continue
        if not row.get("virtual_sol_reserves_raw") or not row.get("virtual_token_reserves_raw"):
            continue
        mint = str(row["mint"])
        signature_indices[mint][str(row["signature"])] = len(by_mint[mint])
        by_mint[mint].append(row)

    sol_usd = float(curve["sol_usd_assumption"])
    stake_usd = float(curve["stake_usd"])
    gas_usd = float(curve["gas_usd_round_trip"])
    max_formula_error = float(curve.get("max_formula_relative_error") or 1e-4)

    trade_rows: list[dict] = []
    transaction_rows: list[dict] = []
    recovery_rows: list[dict] = []
    skipped: Counter = Counter()
    lane_stats = {}
    selected_lanes = [
        name for name in (curve.get("trades") or {})
        if name.endswith(lane_suffix)
    ]
    for lane in sorted(selected_lanes):
        before = len(trade_rows)
        lane_skipped = Counter()
        for trade in curve["trades"][lane]:
            mint = str(trade.get("mint") or "")
            if mint not in by_mint:
                lane_skipped["mint_missing_from_tape"] += 1
                continue
            result, tx_rows, sell_rows, reason = _analyse_trade(
                trade, by_mint[mint], signature_indices[mint],
                sol_usd=sol_usd, stake_usd=stake_usd, gas_usd=gas_usd,
                max_formula_error=max_formula_error, lane=lane,
            )
            transaction_rows.extend(tx_rows)
            recovery_rows.extend(sell_rows)
            if reason:
                lane_skipped[reason] += 1
                continue
            trade_rows.append(result)
        skipped.update(lane_skipped)
        lane_trade_rows = [row for row in trade_rows if row["lane"] == lane]
        lane_stats[lane] = {
            "trades_in_curve_report": len(curve["trades"][lane]),
            "diagnostics_completed": len(lane_trade_rows),
            "unique_mints": len({row["mint"] for row in lane_trade_rows}),
            "skipped": dict(lane_skipped),
        }

    summaries = []
    for lane in sorted(selected_lanes):
        lane_trades = [row for row in trade_rows if row["lane"] == lane]
        reasons = sorted({row["exit_reason"] or "unknown" for row in lane_trades})
        for reason in reasons:
            group = [row for row in lane_trades if (row["exit_reason"] or "unknown") == reason]
            group_sells = [
                row for row in recovery_rows
                if row["lane"] == lane and (row["exit_reason"] or "unknown") == reason
            ]
            summaries.append({
                "lane": lane,
                "exit_reason": reason,
                "trades": len(group),
                "unique_mints": len({row["mint"] for row in group}),
                "median_exit_pnl_usd": _median([row["curve_exit_pnl_usd"] for row in group]),
                "median_mfe_usd": _median([row["mfe_usd"] for row in group]),
                "median_mae_usd": _median([row["mae_usd"] for row in group]),
                "median_first_positive_slot_delta": _median([row["first_positive_slot_delta"] for row in group]),
                "median_buy_sol": _median([row["external_buy_sol"] for row in group]),
                "median_sell_sol": _median([row["external_sell_sol"] for row in group]),
                "median_net_sol": _median([row["external_net_sol"] for row in group]),
                "median_buy_flow_initial_reserve_pct": _median([row["buy_flow_initial_reserve_pct"] for row in group]),
                "median_sell_flow_initial_reserve_pct": _median([row["sell_flow_initial_reserve_pct"] for row in group]),
                "median_adverse_sell_recovery_rate_per_trade": _median([row["adverse_sell_recovery_rate"] for row in group]),
                "adverse_sell_transactions": len(group_sells),
                "adverse_sell_recovery_rate_transaction_level": (
                    sum(bool(row["recovered_before_exit"]) for row in group_sells) / len(group_sells)
                    if group_sells else None
                ),
                "median_recovery_transaction_count": _median([
                    row["recovery_transaction_count"] for row in group_sells
                    if row["recovered_before_exit"]
                ]),
                "median_recovery_buy_sol": _median([
                    row["buy_sol_until_recovery"] for row in group_sells
                    if row["recovered_before_exit"]
                ]),
            })

    result = {
        "model": "transaction-boundary counterfactual Pump curve path diagnostics",
        "paper_only": True,
        "strategy_decisions_changed": False,
        "source_curve_report": os.path.basename(curve_report),
        "tape_filename": tape_filename,
        "sol_usd_assumption": sol_usd,
        "stake_usd": stake_usd,
        "gas_usd_round_trip": gas_usd,
        "lane_suffix": lane_suffix,
        "lanes": lane_stats,
        "summary_by_lane_and_exit_reason": summaries,
        "diagnostics_completed": len(trade_rows),
        "unique_mint_lane_paths": len({(row["lane"], row["mint"]) for row in trade_rows}),
        "transaction_checkpoints": len(transaction_rows),
        "adverse_sell_transactions": len(recovery_rows),
        "skipped": dict(skipped),
        "method": [
            "Use the paired curve replay's selected entry and exit signatures, not the exit trigger signature.",
            "Resolve each selected signature to its final event row in the canonical ordered tape; transaction fills occur only after the complete signature.",
            "Validate each observed reserve transition with the existing Pump curve transition checker.",
            "Simulate the fixed-notional buy once at the post-entry transaction reserves, then replay every intervening external trade against the counterfactual position.",
            "Calculate executable sell quotes only after each complete transaction, including protocol and creator fees plus the configured round-trip gas.",
            "Measure sell recovery only when a later complete transaction restores spot to at least its pre-sell level.",
            "Reconcile each final checkpoint against the source paired curve replay P&L to detect implementation drift.",
        ],
        "limitations": [
            "This is a counterfactual curve quote, not proof of transaction submission, inclusion, or actual fill.",
            "The paired latency lanes overlap in mints and tape paths; never add their P&Ls or count lane variants as independent mints.",
            "Exit-reason groups are retrospective labels. Features and path summaries must not be presented as prospective predictors without a separate time-of-decision test.",
            "Sell transactions within one mint path are dependent observations; transaction-level recovery rates are descriptive, not independent sample estimates.",
            "The tape's t_plus field may be coarse; slot and canonical transaction order are retained as the primary ordering evidence.",
            "Only the selected lane suffix is processed. Missing, invalid or unreconciled paths are reported as skipped rather than silently included.",
        ],
    }

    os.makedirs(out_dir, exist_ok=True)
    _write_csv(os.path.join(out_dir, "curve_path_trades.csv"), trade_rows)
    _write_csv(os.path.join(out_dir, "curve_path_transactions.csv"), transaction_rows)
    _write_csv(os.path.join(out_dir, "curve_path_sell_recovery.csv"), recovery_rows)
    with open(os.path.join(out_dir, "curve_path_diagnostics.json"), "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)

    lines = [
        "# Transaction-boundary curve path diagnostics",
        "",
        f"- SOL/USD: {sol_usd:.4f}",
        f"- Stake: ${stake_usd:.2f}",
        f"- Round-trip gas: ${gas_usd:.4f}",
        f"- Lane suffix: `{lane_suffix}`",
        f"- Completed trade paths: {len(trade_rows)}",
        f"- Transaction checkpoints: {len(transaction_rows)}",
        f"- Adverse sell transactions: {len(recovery_rows)}",
        "",
        "## Outcome summaries",
        "",
        "| Lane | Exit reason | Trades / unique mints | Median exit P&L | Median MFE | Median MAE | Median net SOL | Sell recovery rate (transactions) |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        rate = row["adverse_sell_recovery_rate_transaction_level"]
        lines.append(
            f"| {row['lane']} | {row['exit_reason']} | {row['trades']} / {row['unique_mints']} | "
            f"{row['median_exit_pnl_usd'] if row['median_exit_pnl_usd'] is not None else 'n/a'} | "
            f"{row['median_mfe_usd'] if row['median_mfe_usd'] is not None else 'n/a'} | "
            f"{row['median_mae_usd'] if row['median_mae_usd'] is not None else 'n/a'} | "
            f"{row['median_net_sol'] if row['median_net_sol'] is not None else 'n/a'} | "
            f"{rate if rate is not None else 'n/a'} |"
        )
    lines += ["", "## Skipped paths", ""]
    lines.extend([f"- {reason}: {count}" for reason, count in sorted(skipped.items())] or ["- None"])
    lines += ["", "## Method and limitations", ""]
    lines.extend([f"- {item}" for item in result["method"]])
    lines.extend(["", "### Limitations", ""])
    lines.extend([f"- {item}" for item in result["limitations"]])
    with open(os.path.join(out_dir, "curve_path_diagnostics.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--curve-report", default="./report/paired_latency_curve_replay.json")
    parser.add_argument("--out", default="./report")
    parser.add_argument("--tape-file", default="sniper_tape_block_ordered_sniper.jsonl")
    parser.add_argument("--lane-suffix", default=DEFAULT_LANE_SUFFIX)
    args = parser.parse_args()
    report = build_report(
        args.data_dir, args.out, curve_report=args.curve_report,
        tape_filename=args.tape_file, lane_suffix=args.lane_suffix,
    )
    print(json.dumps({
        "completed": report["diagnostics_completed"],
        "unique_mint_lane_paths": report["unique_mint_lane_paths"],
        "transaction_checkpoints": report["transaction_checkpoints"],
        "adverse_sell_transactions": report["adverse_sell_transactions"],
        "skipped": report["skipped"],
    }, indent=2))


if __name__ == "__main__":
    main()
