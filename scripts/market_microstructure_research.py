#!/usr/bin/env python3
"""Offline market-microstructure and execution-integrity research for Pump.fun tapes.

This report is descriptive research only. It does not change or execute any strategy.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from collections import defaultdict
from statistics import median

DELAYS = (0.5, 1.0, 1.5)
HORIZONS = (5, 10, 30)
FEATURES = (
    "buy_acceleration_sol",
    "buy_acceleration_reserve_fraction",
    "external_buy_sol_5s",
    "external_buy_reserve_fraction_5s",
    "net_flow_reserve_fraction_5s",
    "distinct_buyers_5s",
    "top1_buyer_share_5s",
    "buyer_hhi_5s",
    "reserve_sol_5s",
    "price_return_1s",
    "price_return_3s",
)


def read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
            except (json.JSONDecodeError, TypeError):
                continue
    return rows


def _num(value, default=None):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _wallet_buys(row):
    values = row.get("wallet_buy_sol") or {}
    if not isinstance(values, dict):
        return {}
    return {str(wallet): amount for wallet, raw in values.items()
            if (amount := _num(raw, 0.0)) > 0}


def _sum_wallet_buys(rows):
    totals = defaultdict(float)
    for row in rows:
        for wallet, amount in _wallet_buys(row).items():
            totals[wallet] += amount
    return dict(totals)


def _price_at_or_before(rows, when):
    found = None
    for row in rows:
        observed = _num(row.get("observed_at_epoch"))
        price = _num(row.get("final_price_sol"))
        if observed is not None and observed <= when and price is not None and price > 0:
            found = price
    return found


def _first_forward_mark(rows, when):
    for row in rows:
        observed = _num(row.get("observed_at_epoch"))
        price = _num(row.get("final_price_sol"))
        if observed is not None and observed >= when and price is not None and price > 0:
            return row
    return None


def cost_hurdle(entry_slippage=0.015, exit_slippage=0.015,
                fee_rate=0.0125, gas_usd=0.0285, stake_usd=1.0):
    """Gross mark-to-mark move required to cover configured costs."""
    if stake_usd <= 0 or exit_slippage >= 1 or fee_rate >= 1:
        raise ValueError("Invalid cost assumptions")
    multiplier = ((1.0 + gas_usd / stake_usd) *
                  (1.0 + entry_slippage) / (1.0 - exit_slippage) /
                  ((1.0 - fee_rate) ** 2))
    return multiplier - 1.0


def build_mint_snapshots(transactions, delays=DELAYS, horizons=HORIZONS,
                         snapshot_seconds=5.0, min_snapshot_age=3.0):
    """Create one causal first-five-second snapshot per mint, plus delayed marks."""
    by_mint = defaultdict(list)
    for row in transactions:
        mint = row.get("mint")
        observed = _num(row.get("observed_at_epoch"))
        sequence = _num(row.get("observation_sequence"), 0)
        if mint and observed is not None:
            by_mint[str(mint)].append((sequence, row))

    snapshots = []
    for mint, pairs in by_mint.items():
        pairs.sort(key=lambda item: (item[0], _num(item[1].get("observed_at_epoch"), 0)))
        rows = [row for _, row in pairs]
        first_time = _num(rows[0].get("observed_at_epoch"))
        if first_time is None:
            continue
        cutoff = first_time + snapshot_seconds
        snap_index = None
        for index, row in enumerate(rows):
            observed = _num(row.get("observed_at_epoch"))
            price = _num(row.get("final_price_sol"))
            if observed is not None and observed <= cutoff and price is not None and price > 0:
                snap_index = index
        if snap_index is None:
            continue
        snapshot = rows[snap_index]
        signal_time = _num(snapshot.get("observed_at_epoch"))
        if signal_time is None or signal_time - first_time < min_snapshot_age:
            continue
        signal_slot = int(_num(snapshot.get("slot"), 0))
        signal_price = _num(snapshot.get("final_price_sol"))
        if not signal_price or signal_price <= 0:
            continue

        pre = rows[:snap_index + 1]
        wallet_totals = _sum_wallet_buys(pre)
        amounts = list(wallet_totals.values())
        total_external_buy = sum(amounts)
        top1_share = max(amounts) / total_external_buy if total_external_buy > 0 else None
        hhi = sum((amount / total_external_buy) ** 2 for amount in amounts) if total_external_buy > 0 else None

        latest_1s = [r for r in pre if _num(r.get("observed_at_epoch"), -1) > signal_time - 1.0]
        previous_1s = [r for r in pre if signal_time - 2.0 < _num(r.get("observed_at_epoch"), -1) <= signal_time - 1.0]
        buy_last = sum(sum(_wallet_buys(r).values()) for r in latest_1s)
        buy_previous = sum(sum(_wallet_buys(r).values()) for r in previous_1s)
        price_1s = _price_at_or_before(pre, signal_time - 1.0)
        price_3s = _price_at_or_before(pre, signal_time - 3.0)
        reserve_raw = _num(snapshot.get("final_virtual_sol_reserves_raw"))
        reserve_sol = reserve_raw / 1e9 if reserve_raw is not None and reserve_raw > 0 else None
        sell_sol = sum(_num(r.get("sell_sol"), 0.0) or 0.0 for r in pre)

        item = {
            "mint": mint,
            "signal_time": signal_time,
            "signal_slot": signal_slot,
            "signal_sequence": _num(snapshot.get("observation_sequence"), 0),
            "signal_price_sol": signal_price,
            "snapshot_age_seconds": signal_time - first_time,
            "external_buy_sol_5s": total_external_buy,
            "distinct_buyers_5s": len(wallet_totals),
            "top1_buyer_share_5s": top1_share,
            "buyer_hhi_5s": hhi,
            "buy_flow_last_1s_sol": buy_last,
            "buy_flow_previous_1s_sol": buy_previous,
            "buy_acceleration_sol": buy_last - buy_previous,
            "sell_sol_5s": sell_sol,
            "creator_sell_5s": any(bool(r.get("creator_sell")) for r in pre),
            "reserve_sol_5s": reserve_sol,
            "buy_acceleration_reserve_fraction": (buy_last - buy_previous) / reserve_sol if reserve_sol else None,
            "external_buy_reserve_fraction_5s": total_external_buy / reserve_sol if reserve_sol else None,
            "net_flow_reserve_fraction_5s": (total_external_buy - sell_sol) / reserve_sol if reserve_sol else None,
            "price_return_1s": signal_price / price_1s - 1.0 if price_1s else None,
            "price_return_3s": signal_price / price_3s - 1.0 if price_3s else None,
        }

        after_snapshot = rows[snap_index + 1:]
        for delay in delays:
            entry = None
            for row in after_snapshot:
                observed = _num(row.get("observed_at_epoch"))
                slot = int(_num(row.get("slot"), 0))
                price = _num(row.get("final_price_sol"))
                if (observed is not None and observed >= signal_time + delay and
                        slot > signal_slot and price is not None and price > 0):
                    entry = row
                    break
            prefix = f"delay_{delay:.1f}s"
            if entry is None:
                item[f"{prefix}_entry_lag_seconds"] = None
                for horizon in horizons:
                    item[f"{prefix}_return_{horizon}s"] = None
                    item[f"{prefix}_exit_mark_lag_seconds_{horizon}s"] = None
                continue
            entry_time = _num(entry.get("observed_at_epoch"))
            entry_price = _num(entry.get("final_price_sol"))
            item[f"{prefix}_entry_lag_seconds"] = entry_time - signal_time
            item[f"{prefix}_entry_move_from_signal"] = entry_price / signal_price - 1.0
            for horizon in horizons:
                mark = _first_forward_mark(after_snapshot, entry_time + horizon)
                if mark is None:
                    item[f"{prefix}_return_{horizon}s"] = None
                    item[f"{prefix}_exit_mark_lag_seconds_{horizon}s"] = None
                else:
                    mark_time = _num(mark.get("observed_at_epoch"))
                    mark_price = _num(mark.get("final_price_sol"))
                    item[f"{prefix}_return_{horizon}s"] = mark_price / entry_price - 1.0
                    item[f"{prefix}_exit_mark_lag_seconds_{horizon}s"] = mark_time - (entry_time + horizon)
        snapshots.append(item)
    return snapshots


def _quantile_groups(rows, feature):
    """Build within-session quantile bands without splitting tied feature values."""
    valid = [r for r in rows if _num(r.get(feature)) is not None]
    values = sorted(_num(row.get(feature)) for row in valid)
    groups = {f"Q{i}": [] for i in range(1, 5)}
    if not values:
        return groups
    cuts = [values[int((len(values) - 1) * fraction)] for fraction in (0.25, 0.50, 0.75)]
    for row in valid:
        value = _num(row.get(feature))
        q = 1 if value <= cuts[0] else 2 if value <= cuts[1] else 3 if value <= cuts[2] else 4
        groups[f"Q{q}"].append(row)
    return groups


def _metric(rows, field, hurdle):
    values = [_num(row.get(field)) for row in rows]
    values = [value for value in values if value is not None]
    if not values:
        return {"n": 0, "median_return": None, "mean_return": None,
                "positive_rate": None, "cost_hurdle_rate": None}
    return {
        "n": len(values),
        "median_return": round(median(values), 6),
        "mean_return": round(sum(values) / len(values), 6),
        "positive_rate": round(sum(value > 0 for value in values) / len(values), 4),
        "cost_hurdle_rate": round(sum(value > hurdle for value in values) / len(values), 4),
    }


def summarize_snapshots(snapshots, hurdle):
    summaries = {}
    for delay in DELAYS:
        for horizon in HORIZONS:
            field = f"delay_{delay:.1f}s_return_{horizon}s"
            summaries[f"{delay:.1f}s_{horizon}s"] = _metric(snapshots, field, hurdle)
    quartiles = {}
    for feature in FEATURES:
        quartiles[feature] = {}
        for q, members in _quantile_groups(snapshots, feature).items():
            quartiles[feature][q] = _metric(members, "delay_1.0s_return_10s", hurdle)
            values = [_num(row.get(feature)) for row in members]
            values = [v for v in values if v is not None]
            quartiles[feature][q]["feature_min"] = min(values) if values else None
            quartiles[feature][q]["feature_max"] = max(values) if values else None
    return {"forward_marks": summaries, "one_second_delay_ten_second_quartiles": quartiles}


def _execution_audit(transactions, data_dir):
    tx_by_sig_mint = {}
    for row in transactions:
        signature, mint = row.get("signature"), row.get("mint")
        if signature and mint:
            tx_by_sig_mint[(signature, mint)] = row

    files = {
        "transaction_complete": "sniper_tx_trades_sniper.jsonl",
        "multi_buyer_one_position": "sniper_cluster_portfolio_trades_sniper.jsonl",
        "wallet_guard": "sniper_cluster_wallet_guard_trades_sniper.jsonl",
    }
    result = {}
    for lane, filename in files.items():
        trades = read_jsonl(os.path.join(data_dir, filename))
        rows = []
        for trade in trades:
            mint = trade.get("mint")
            signal = tx_by_sig_mint.get((trade.get("signal_signature"), mint))
            entry = tx_by_sig_mint.get((trade.get("entry_signature"), mint))
            exit_row = tx_by_sig_mint.get((trade.get("exit_signature"), mint))
            if not signal or not entry:
                continue
            same_entry_slot = int(_num(entry.get("slot"), 0)) == int(_num(signal.get("slot"), 0))
            same_exit_slot = bool(exit_row) and int(_num(exit_row.get("slot"), 0)) == int(_num(entry.get("slot"), 0))
            signal_time = _num(signal.get("observed_at_epoch"))
            entry_time = _num(entry.get("observed_at_epoch"))
            exit_time = _num(exit_row.get("observed_at_epoch")) if exit_row else None
            pnl = _num(trade.get("net_pnl_usd"), 0.0) or 0.0
            rows.append({
                "same_entry_slot": same_entry_slot, "same_exit_slot": same_exit_slot,
                "entry_callback_gap": entry_time - signal_time if signal_time is not None and entry_time is not None else None,
                "exit_callback_gap": exit_time - entry_time if exit_time is not None and entry_time is not None else None,
                "pnl": pnl,
            })
        same_entry = [r for r in rows if r["same_entry_slot"]]
        later_entry = [r for r in rows if not r["same_entry_slot"]]
        same_exit = [r for r in rows if r["same_exit_slot"]]
        entry_gaps = sorted(r["entry_callback_gap"] for r in rows if r["entry_callback_gap"] is not None)
        exit_gaps = sorted(r["exit_callback_gap"] for r in rows if r["exit_callback_gap"] is not None)
        same_entry_gaps = [r["entry_callback_gap"] for r in same_entry if r["entry_callback_gap"] is not None]
        same_exit_gaps = [r["exit_callback_gap"] for r in same_exit if r["exit_callback_gap"] is not None]
        result[lane] = {
            "trades_with_mapped_signal_and_entry": len(rows),
            "entries_same_signal_slot": len(same_entry),
            "entries_later_signal_slot": len(later_entry),
            "same_slot_entry_callback_gap_median_seconds": median(same_entry_gaps) if same_entry_gaps else None,
            "same_slot_entry_pnl_usd": round(sum(r["pnl"] for r in same_entry), 6),
            "later_slot_entry_pnl_usd": round(sum(r["pnl"] for r in later_entry), 6),
            "exits_same_entry_slot": len(same_exit),
            "same_slot_exit_wins": sum(r["pnl"] > 0 for r in same_exit),
            "same_slot_exit_pnl_usd": round(sum(r["pnl"] for r in same_exit), 6),
            "same_slot_exit_callback_gap_median_seconds": median(same_exit_gaps) if same_exit_gaps else None,
            "entry_callback_gap_median_seconds": median(entry_gaps) if entry_gaps else None,
            "exit_callback_gap_median_seconds": median(exit_gaps) if exit_gaps else None,
            "exit_signatures_mapped": sum(r["exit_callback_gap"] is not None for r in rows),
        }
    return result


def build_report(data_dir, out_dir):
    transactions = read_jsonl(os.path.join(data_dir, "sniper_transactions_sniper.jsonl"))
    transactions.sort(key=lambda row: int(_num(row.get("observation_sequence"), 0)))
    snapshots = build_mint_snapshots(transactions)
    hurdle = cost_hurdle()
    summary = summarize_snapshots(snapshots, hurdle)
    audit = _execution_audit(transactions, data_dir)
    result = {
        "input_transaction_summaries": len(transactions),
        "unique_mints": len({row.get("mint") for row in transactions if row.get("mint")}),
        "usable_five_second_snapshots": len(snapshots),
        "estimated_gross_cost_hurdle": round(hurdle, 6),
        "snapshot_method": "last valid reserve-derived transaction mark observed within five seconds of each mint's first captured transaction; requires at least three seconds of observed tape",
        "delayed_entry_method": "first later transaction for the same mint with a strictly later Solana slot and callback time at least the assumed delay after the snapshot",
        "forward_mark_method": "first valid observed transaction mark at or after the requested horizon after delayed entry; not a simulated TP/SL exit",
        **summary,
        "execution_integrity": audit,
        "limitations": [
            "This is an offline descriptive study. It does not modify or execute any strategy.",
            "Five-second windows start at the first captured transaction, not a guaranteed exact on-chain create timestamp.",
            "Wallet buy totals exclude the creator because wallet_buy_sol is built only for non-creator buyers; sell_sol includes decoded sell flow and creator_sell is separately flagged.",
            "Quartiles are recomputed within each session and are research bins, not deployable fixed thresholds. Tied feature values remain together, so quartile sizes may differ.",
            "Forward reserve-derived marks are not executable bonding-curve quotes or confirmed fills.",
            "The gross cost hurdle applies the configured 1.5% entry/exit slippage, 1.25% fee each side, and $0.0285 gas to a $1 stake; it is a mark-to-mark threshold, not a fill simulator.",
            "No observation after a requested horizon is recorded as missing, not as a loss.",
            "Different strategy lanes overlap in mints and market events; their P&Ls must not be added.",
            "Same-slot transactions and callback gaps are execution diagnostics, not proof that a particular transaction could never be filled; actual order submission and inclusion are not recorded.",
        ],
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "market_microstructure_research.json"), "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)

    lines = [
        "# Market microstructure and execution-integrity research", "",
        f"- Transaction summaries: **{result['input_transaction_summaries']}**",
        f"- Unique mints: **{result['unique_mints']}**",
        f"- Usable first-five-second snapshots: **{result['usable_five_second_snapshots']}**",
        f"- Estimated gross mark-to-mark cost hurdle: **{hurdle:.2%}**", "",
        "## Delayed-entry forward mark study", "",
        "Entries require a later Solana slot and the stated callback delay. Returns are forward reserve-derived marks, not TP/SL exits.", "",
        "| Entry delay | Horizon | Marks | Median return | Positive | Clears cost hurdle |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for key, row in summary["forward_marks"].items():
        delay, horizon = key.split("_")
        if row["median_return"] is None:
            lines.append(f"| {delay} | {horizon} | 0 | n/a | n/a | n/a |")
        else:
            lines.append(
                f"| {delay} | {horizon} | {row['n']} | {row['median_return']:.2%} | "
                f"{row['positive_rate']:.1%} | {row['cost_hurdle_rate']:.1%} |"
            )

    lines += [
        "", "## First-five-second feature quartiles", "",
        "These are within-session quantile bands; tied values remain together. Q4 is the highest feature-value group. The outcome is a 10-second forward mark after a 1.0-second delayed, later-slot entry.", "",
        "| Feature | Quartile | N | Median return | Positive | Clears cost hurdle | Feature range |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for feature, groups in summary["one_second_delay_ten_second_quartiles"].items():
        for q, row in groups.items():
            rng = "n/a" if row["feature_min"] is None else f"{row['feature_min']:.5g} to {row['feature_max']:.5g}"
            med = "n/a" if row["median_return"] is None else f"{row['median_return']:.2%}"
            pos = "n/a" if row["positive_rate"] is None else f"{row['positive_rate']:.1%}"
            hurdle_rate = "n/a" if row["cost_hurdle_rate"] is None else f"{row['cost_hurdle_rate']:.1%}"
            lines.append(f"| {feature} | {q} | {row['n']} | {med} | {pos} | {hurdle_rate} | {rng} |")

    lines += [
        "", "## Existing-lane execution integrity", "",
        "| Lane | Mapped trades | Same-slot entries | Later-slot entries | Same-slot exits | Same-slot exit wins | Same-slot exit P&L |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for lane, row in audit.items():
        lines.append(
            f"| {lane} | {row['trades_with_mapped_signal_and_entry']} | "
            f"{row['entries_same_signal_slot']} | {row['entries_later_signal_slot']} | "
            f"{row['exits_same_entry_slot']} | {row['same_slot_exit_wins']} | "
            f"$" + f"{row['same_slot_exit_pnl_usd']:.4f} |"
        )
    lines += ["", "## Limitations"]
    lines += [f"- {item}" for item in result["limitations"]]
    with open(os.path.join(out_dir, "market_microstructure_research.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--out", default="./report")
    args = parser.parse_args()
    print(json.dumps(build_report(args.data_dir, args.out), indent=2))


if __name__ == "__main__":
    main()
