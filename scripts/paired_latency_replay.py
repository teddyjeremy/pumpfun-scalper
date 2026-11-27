#!/usr/bin/env python3
"""Paired offline replay for latency, wallet-loss lock, and post-signal flow.

Paper research only. Uses captured reserve-derived transaction marks; it does not
simulate a signed order, Pump.fun quote, RPC inclusion, or guaranteed fill.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from statistics import median


DELAYS_MS = (500, 1000, 1500)


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


def _positive_entry_flow(tx):
    return (
        int(tx.get("buy_count") or 0) > 0
        and float(tx.get("net_buy_sol") or 0) > 0
        and not bool(tx.get("creator_sell"))
    )


def replay_lane(rows, *, latency_seconds, wallet_loss_guard, positive_entry_flow,
                stake_usd=1.0, take_profit=0.20, stop_loss=0.12,
                max_hold_seconds=30, confirm_seconds=3.0,
                entry_slippage=0.015, exit_slippage=0.015,
                fee_rate=0.0125, gas_usd=0.0285):
    """Replay one isolated one-position portfolio over ordered tx summaries."""
    pending = {}
    position = None
    locked_wallets = set()
    entries, trades = [], []
    decisions = Counter()

    for tx in rows:
        mint = tx.get("mint")
        price = tx.get("final_price_sol")
        observed = tx.get("observed_at_epoch")
        if not mint or not price or float(price) <= 0 or observed is None:
            continue
        price, observed = float(price), float(observed)
        slot = int(tx.get("slot") or 0)
        signature = tx.get("signature")

        # Reproduce the runtime's timer_tick behavior before handling this event.
        # Expired candidates are removed before this transaction is processed, so
        # the transaction can become a fresh signal rather than being discarded.
        for pending_mint, candidate in list(pending.items()):
            if observed - candidate["signal_observed_at_epoch"] > confirm_seconds:
                pending.pop(pending_mint, None)
                decisions["entry_expired"] += 1

        # The live lane starts its time-cap exit from a timer tick, not from the
        # next same-mint transaction. Replay that deadline using the captured
        # event clock and the last observed reserve mark.
        if (position and not position.get("pending_exit")
                and observed >= position["entry_observed_at_epoch"] + max_hold_seconds):
            due = position["entry_observed_at_epoch"] + max_hold_seconds
            position["pending_exit"] = {
                "reason": "time_cap", "signature": position["last_signature"],
                "slot": position["last_slot"], "observed_at_epoch": due,
                "price": position["last_price_sol"],
            }
            decisions["exit_trigger_time_cap"] += 1

        if position and position["mint"] == mint:
            if position.get("pending_exit"):
                trigger = position["pending_exit"]
                if (signature != trigger["signature"] and slot > trigger["slot"]
                        and observed >= trigger["observed_at_epoch"] + latency_seconds):
                    exit_fill = price * (1.0 - exit_slippage)
                    net_mult = (exit_fill / position["entry_fill_price_sol"]) * ((1.0 - fee_rate) ** 2)
                    pnl = stake_usd * (net_mult - 1.0) - gas_usd
                    trade = {
                        **position,
                        "exit_signature": signature, "exit_slot": slot,
                        "exit_observed_at_epoch": observed, "exit_price_sol": price,
                        "exit_fill_price_sol": exit_fill, "exit_reason": trigger["reason"],
                        "exit_trigger_signature": trigger["signature"],
                        "exit_trigger_slot": trigger["slot"],
                        "exit_trigger_price_sol": trigger["price"],
                        "exit_latency_seconds": round(observed - trigger["observed_at_epoch"], 6),
                        "exit_latency_slots": slot - trigger["slot"],
                        "hold_seconds": round(observed - position["entry_observed_at_epoch"], 6),
                        "market_move_at_exit": round(price / position["entry_price_sol"] - 1, 6),
                        "net_pnl_usd": round(pnl, 6),
                    }
                    trades.append(trade)
                    decisions["closed_delayed_exit"] += 1
                    if wallet_loss_guard and pnl <= 0 and position.get("signal_wallet"):
                        locked_wallets.add(position["signal_wallet"])
                        decisions["wallet_locked_after_loss"] += 1
                    position = None
                continue

            if signature == position["entry_signature"] or slot <= position["entry_slot"]:
                continue
            move = price / position["entry_price_sol"] - 1.0
            reason = ("take_profit" if move >= take_profit else
                      "stop_loss" if move <= -stop_loss else None)
            if reason:
                position["pending_exit"] = {
                    "reason": reason, "signature": signature, "slot": slot,
                    "observed_at_epoch": observed, "price": price,
                }
                decisions[f"exit_trigger_{reason}"] += 1
            else:
                position["last_signature"] = signature
                position["last_slot"] = slot
                position["last_observed_at_epoch"] = observed
                position["last_price_sol"] = price
            continue

        candidate = pending.get(mint)
        if candidate:
            elapsed = observed - candidate["signal_observed_at_epoch"]
            if (signature == candidate["signal_signature"] or slot <= candidate["signal_slot"]
                    or elapsed < latency_seconds):
                continue
            if len(candidate["signal_buyer_wallets"]) < 2:
                pending.pop(mint, None)
                decisions["rejected_single_buyer_signal"] += 1
                continue
            if wallet_loss_guard and candidate["signal_wallet"] in locked_wallets:
                pending.pop(mint, None)
                decisions["rejected_locked_wallet"] += 1
                continue
            if position is not None:
                pending.pop(mint, None)
                decisions["rejected_portfolio_busy"] += 1
                continue
            if positive_entry_flow and not _positive_entry_flow(tx):
                decisions["rejected_entry_without_positive_flow"] += 1
                continue

            entry = {
                "mint": mint, "symbol": tx.get("symbol"),
                "latency_seconds": latency_seconds,
                "signal_wallet": candidate["signal_wallet"],
                "signal_buyer_wallets": candidate["signal_buyer_wallets"],
                "signal_buyer_count": len(candidate["signal_buyer_wallets"]),
                "signal_signature": candidate["signal_signature"],
                "signal_slot": candidate["signal_slot"],
                "signal_observed_at_epoch": candidate["signal_observed_at_epoch"],
                "signal_net_buy_sol": candidate.get("signal_net_buy_sol"),
                "signal_creator_sell": candidate.get("signal_creator_sell"),
                "entry_signature": signature, "entry_slot": slot,
                "entry_observed_at_epoch": observed,
                "entry_latency_seconds": round(elapsed, 6),
                "entry_buyer_count": int(tx.get("buyer_count") or 0),
                "entry_buy_sol": float(tx.get("buy_sol") or 0),
                "entry_sell_sol": float(tx.get("sell_sol") or 0),
                "entry_net_buy_sol": float(tx.get("net_buy_sol") or 0),
                "entry_creator_sell": bool(tx.get("creator_sell")),
                "entry_price_sol": price,
                "entry_fill_price_sol": price * (1.0 + entry_slippage),
                "last_signature": signature, "last_slot": slot,
                "last_observed_at_epoch": observed, "last_price_sol": price,
            }
            pending.pop(mint, None)
            entries.append(entry)
            position = entry
            decisions["accepted_entry"] += 1
            continue

        buyers = list(tx.get("buyer_wallets") or [])
        if len(set(buyers)) < 1:
            continue
        signal_wallet = tx.get("first_buyer_wallet") or buyers[0]
        if wallet_loss_guard and signal_wallet in locked_wallets:
            decisions["rejected_locked_signal"] += 1
            continue
        pending[mint] = {
            "signal_signature": signature, "signal_slot": slot,
            "signal_observed_at_epoch": observed,
            "signal_wallet": signal_wallet,
            "signal_buyer_wallets": sorted(set(buyers)),
            "signal_net_buy_sol": float(tx.get("net_buy_sol") or 0),
            "signal_creator_sell": bool(tx.get("creator_sell")),
        }
        decisions["signal_candidate_observed"] += 1

    return {
        "latency_seconds": latency_seconds,
        "wallet_loss_guard": wallet_loss_guard,
        "positive_entry_flow_gate": positive_entry_flow,
        "entries": entries, "trades": trades,
        "open_position": position,
        "pending_signals": len(pending),
        "locked_wallets": sorted(locked_wallets),
        "decisions": dict(decisions),
    }


def summarize(result):
    trades = result["trades"]
    wins = [x for x in trades if x["net_pnl_usd"] > 0]
    losses = [x for x in trades if x["net_pnl_usd"] <= 0]
    gross_win = sum(x["net_pnl_usd"] for x in wins)
    gross_loss = abs(sum(x["net_pnl_usd"] for x in losses))
    ordered = sorted(trades, key=lambda x: x["exit_observed_at_epoch"])
    streak = maximum = 0
    for trade in ordered:
        streak = 0 if trade["net_pnl_usd"] > 0 else streak + 1
        maximum = max(maximum, streak)
    return {
        "latency_seconds": result["latency_seconds"],
        "wallet_loss_guard": result["wallet_loss_guard"],
        "positive_entry_flow_gate": result["positive_entry_flow_gate"],
        "entries": len(result["entries"]), "closed_trades": len(trades),
        "open_position": bool(result["open_position"]),
        "open_mint": result["open_position"]["mint"] if result["open_position"] else None,
        "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / len(trades) if trades else None,
        "net_pnl_usd": round(sum(x["net_pnl_usd"] for x in trades), 6),
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss else None,
        "maximum_loss_streak": maximum,
        "unique_traded_mints": len({x["mint"] for x in trades}),
        "median_entry_delay_seconds": round(median(
            x["entry_latency_seconds"] for x in result["entries"]), 6) if result["entries"] else None,
        "median_exit_delay_seconds": round(median(
            x["exit_latency_seconds"] for x in trades), 6) if trades else None,
        "same_or_earlier_slot_entries": sum(
            x["entry_slot"] <= x["signal_slot"] for x in result["entries"]),
        "same_or_earlier_slot_exits": sum(
            x["exit_slot"] <= x["exit_trigger_slot"] for x in trades),
        "exit_reasons": dict(Counter(x["exit_reason"] for x in trades)),
        "decisions": result["decisions"],
        "locked_wallet_count": len(result["locked_wallets"]),
    }


def build_report(data_dir, out_dir):
    rows = read_jsonl(os.path.join(data_dir, "sniper_transactions_sniper.jsonl"))
    rows.sort(key=lambda x: int(x.get("observation_sequence") or 0))
    variants = []
    for delay_ms in DELAYS_MS:
        delay = delay_ms / 1000
        variants.extend([
            replay_lane(rows, latency_seconds=delay, wallet_loss_guard=False,
                        positive_entry_flow=False),
            replay_lane(rows, latency_seconds=delay, wallet_loss_guard=True,
                        positive_entry_flow=False),
            replay_lane(rows, latency_seconds=delay, wallet_loss_guard=True,
                        positive_entry_flow=True),
        ])
    summaries = [summarize(x) for x in variants]
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "paired_latency_replay.json"), "w", encoding="utf-8") as f:
        json.dump({"input_transactions": len(rows), "lanes": summaries}, f, indent=2)

    lines = [
        "# Paired latency, wallet-lock, and post-signal flow replay", "",
        f"Input transaction summaries: {len(rows)}", "",
        "All variants use the same captured transaction stream, one-position capacity, stake, TP/SL, fee, gas, and delay. The three variants at each delay isolate: no wallet lock; wallet lock after first losing trade; and wallet lock plus a positive-flow/no-creator-sell execution gate.", "",
        "| Delay | Variant | Entries | Closed | W/L | Win rate | Net P&L | PF | Max loss streak | Unique mints | Open |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for x in summaries:
        variant = ("no wallet lock" if not x["wallet_loss_guard"] else
                   "wallet lock + flow gate" if x["positive_entry_flow_gate"] else "wallet lock")
        wr = f'{x["win_rate"]:.1%}' if x["win_rate"] is not None else "n/a"
        pf = f'{x["profit_factor"]:.2f}' if x["profit_factor"] is not None else "n/a"
        lines.append(
            f'| {x["latency_seconds"]:.1f}s | {variant} | {x["entries"]} | {x["closed_trades"]} | '
            f'{x["wins"]}/{x["losses"]} | {wr} | ${x["net_pnl_usd"]:.4f} | {pf} | '
            f'{x["maximum_loss_streak"]} | {x["unique_traded_mints"]} | '
            f'{"yes" if x["open_position"] else "no"} |'
        )
    lines += [
        "", "## Interpretation limits",
        "- This is a paired offline replay, not a new live or paper execution lane. Existing strategy code and decisions are untouched.",
        "- Entry/exit prices are captured reserve-derived transaction marks with configured slippage, fees, and gas. They are not executable Pump.fun sell quotes or confirmed fills.",
        "- An exit trigger waits for a later transaction in a later slot after the assumed delay; positions without an eligible exit remain open and are not force-closed.",
        "- Pending-signal expiry and max-hold timer ticks are replayed before each captured transaction. A time-cap trigger uses the last observed reserve mark and its timer deadline; exit still requires a later-slot transaction after the configured delay.",
        "- Timer behavior is reconstructed from captured event timestamps, so it is close to but not a byte-for-byte replay of the runtime wall-clock scheduler.",
        "- Variants share the same market stream and may trade overlapping mints. Their P&Ls are paired comparisons, not independent or additive.",
        "- The positive-flow variant requires the execution transaction to contain at least one buy, positive net SOL flow, and no creator sell. It does not require overlap with signal buyers.",
        "- No lane should be promoted from one session. Evaluate across independent sessions and then validate with actual curve quotes and order simulation.",
    ]
    with open(os.path.join(out_dir, "paired_latency_replay.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return {"input_transactions": len(rows), "lanes": summaries}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--out", default="./report")
    args = parser.parse_args()
    print(json.dumps(build_report(args.data_dir, args.out), indent=2))


if __name__ == "__main__":
    main()
