"""Counterfactual Pump.fun bonding-curve execution replay for launch-sniper lanes.

Research-only: this module never signs, submits, or simulates a live transaction.
It replays existing latency-aware sniper decisions and does not change signals.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from statistics import mean, median

LANES = {
    "latency_500ms": "sniper_latency_0500ms_trades_sniper.jsonl",
    "latency_1000ms": "sniper_latency_1000ms_trades_sniper.jsonl",
    "latency_1500ms": "sniper_latency_1500ms_trades_sniper.jsonl",
}
MAX_FORMULA_REL_ERROR = 1e-4


def ceil_div(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    return (numerator + denominator - 1) // denominator


def quote_buy_exact_quote_in(spendable_quote_in: int, virtual_quote_reserves: int,
                             virtual_token_reserves: int, protocol_fee_bps: int,
                             creator_fee_bps: int) -> dict:
    """Pump exact-quote-in integer quote: fees are included in spendable input."""
    if min(spendable_quote_in, virtual_quote_reserves, virtual_token_reserves) <= 0:
        raise ValueError("quote amount and reserves must be positive")
    if min(protocol_fee_bps, creator_fee_bps) < 0:
        raise ValueError("fee rates cannot be negative")
    total_bps = protocol_fee_bps + creator_fee_bps
    net_quote_in = spendable_quote_in * 10_000 // (10_000 + total_bps)
    protocol_fee = ceil_div(net_quote_in * protocol_fee_bps, 10_000)
    creator_fee = ceil_div(net_quote_in * creator_fee_bps, 10_000)
    fees = protocol_fee + creator_fee
    if net_quote_in + fees > spendable_quote_in:
        net_quote_in -= net_quote_in + fees - spendable_quote_in
        # The program charges fees on the adjusted net curve input.
        protocol_fee = ceil_div(net_quote_in * protocol_fee_bps, 10_000)
        creator_fee = ceil_div(net_quote_in * creator_fee_bps, 10_000)
        fees = protocol_fee + creator_fee
    if net_quote_in <= 1:
        raise ValueError("spend is too small after fees")
    quote_for_curve = net_quote_in - 1
    tokens_out = quote_for_curve * virtual_token_reserves // (
        virtual_quote_reserves + quote_for_curve
    )
    if tokens_out <= 0 or tokens_out >= virtual_token_reserves:
        raise ValueError("buy quote produced an invalid token amount")
    return {
        "spendable_quote_in": spendable_quote_in,
        "net_quote_in": net_quote_in,
        "protocol_fee": protocol_fee,
        "creator_fee": creator_fee,
        "total_fees": fees,
        "tokens_out": tokens_out,
    }


def quote_sell_exact_tokens(tokens_in: int, virtual_quote_reserves: int,
                             virtual_token_reserves: int, protocol_fee_bps: int,
                             creator_fee_bps: int) -> dict:
    """Quote a full curve sell and deduct separately rounded protocol/creator fees."""
    if min(tokens_in, virtual_quote_reserves, virtual_token_reserves) <= 0:
        raise ValueError("token amount and reserves must be positive")
    if tokens_in >= virtual_token_reserves:
        raise ValueError("sell amount must be smaller than virtual token reserves")
    if min(protocol_fee_bps, creator_fee_bps) < 0:
        raise ValueError("fee rates cannot be negative")
    gross_quote_out = virtual_quote_reserves * tokens_in // (
        virtual_token_reserves + tokens_in
    )
    protocol_fee = ceil_div(gross_quote_out * protocol_fee_bps, 10_000)
    creator_fee = ceil_div(gross_quote_out * creator_fee_bps, 10_000)
    return {
        "gross_quote_out": gross_quote_out,
        "protocol_fee": protocol_fee,
        "creator_fee": creator_fee,
        "total_fees": protocol_fee + creator_fee,
        "net_quote_out": max(0, gross_quote_out - protocol_fee - creator_fee),
    }


def apply_external_trade(state: dict, event: dict) -> dict:
    """Apply an observed trader's fixed input to the counterfactual curve state."""
    vq = int(state["virtual_quote_reserves"])
    vt = int(state["virtual_token_reserves"])
    quote_amount = int(event.get("sol_amount_raw") or 0)
    token_amount = int(event.get("token_amount_raw") or 0)
    next_state = dict(state)
    if event.get("side") == "buy":
        if quote_amount <= 1:
            raise ValueError("external buy quote amount is too small")
        tokens_out = (quote_amount - 1) * vt // (vq + quote_amount - 1)
        if tokens_out <= 0 or tokens_out >= vt:
            raise ValueError("external buy produced an invalid token amount")
        next_state["virtual_quote_reserves"] = vq + quote_amount
        next_state["virtual_token_reserves"] = vt - tokens_out
        if next_state.get("real_token_reserves") is not None:
            next_state["real_token_reserves"] -= tokens_out
            if next_state["real_token_reserves"] <= 0:
                raise ValueError("external buy would complete the curve")
        return next_state
    if event.get("side") == "sell":
        if token_amount <= 0 or token_amount >= vt:
            raise ValueError("external sell token amount is invalid")
        gross_quote_out = vq * token_amount // (vt + token_amount)
        if gross_quote_out <= 0 or gross_quote_out >= vq:
            raise ValueError("external sell produced an invalid quote amount")
        next_state["virtual_quote_reserves"] = vq - gross_quote_out
        next_state["virtual_token_reserves"] = vt + token_amount
        if next_state.get("real_token_reserves") is not None:
            next_state["real_token_reserves"] += token_amount
        return next_state
    raise ValueError(f"unknown trade side: {event.get('side')!r}")


def validate_observed_transition(previous: dict, event: dict,
                                 max_formula_error: float = MAX_FORMULA_REL_ERROR) -> dict:
    """Check reserve continuity and integer curve output against one observed event."""
    pvq = int(previous["virtual_sol_reserves_raw"])
    pvt = int(previous["virtual_token_reserves_raw"])
    vq = int(event["virtual_sol_reserves_raw"])
    vt = int(event["virtual_token_reserves_raw"])
    quote_amount = int(event.get("sol_amount_raw") or 0)
    token_amount = int(event.get("token_amount_raw") or 0)
    if event.get("side") == "buy":
        reserve_match = (vq - pvq == quote_amount and pvt - vt == token_amount)
        predicted = ((quote_amount - 1) * pvt // (pvq + quote_amount - 1)
                     if quote_amount > 1 else 0)
        actual = token_amount
    elif event.get("side") == "sell":
        reserve_match = (pvq - vq == quote_amount and vt - pvt == token_amount)
        predicted = pvq * token_amount // (pvt + token_amount) if token_amount else 0
        actual = quote_amount
    else:
        return {"ok": False, "reason": "unknown_trade_side", "relative_error": None}
    relative_error = abs(predicted - actual) / max(actual, 1)
    ok = reserve_match and relative_error <= max_formula_error
    return {
        "ok": ok,
        "reserve_match": reserve_match,
        "relative_error": relative_error,
        "predicted_output": predicted,
        "observed_output": actual,
        "reason": None if ok else ("reserve_gap" if not reserve_match else "curve_formula_mismatch"),
    }


def read_jsonl(path: str) -> list[dict]:
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


def _fee_bps(event: dict) -> tuple[int, int]:
    return int(event.get("fee_bps") or 0), int(event.get("creator_fee_bps") or 0)


def _first_later_slot_index(mint_tape: list[dict], after_index: int, slot: int) -> int | None:
    """First observed event strictly after the decision slot."""
    for index in range(after_index + 1, len(mint_tape)):
        if int(mint_tape[index].get("slot") or 0) > slot:
            return index
    return None


def _replay_one(trade: dict, mint_tape: list[dict], signature_indices: dict[str, int],
                complete_slots: set[int], *, sol_usd: float, stake_usd: float,
                gas_usd: float, max_formula_error: float,
                launch: dict | None = None, fill_policy: str = "after_observed") -> dict:
    mint = trade["mint"]
    entry_signature, exit_signature = trade.get("entry_signature"), trade.get("exit_signature")
    if not entry_signature or not exit_signature or exit_signature == "timer":
        return {"status": "missing_executable_signature"}
    if entry_signature not in signature_indices or exit_signature not in signature_indices:
        return {"status": "missing_tape_signature"}
    entry_decision_index = signature_indices[entry_signature]
    exit_trigger_index = signature_indices[exit_signature]
    if exit_trigger_index <= entry_decision_index:
        return {"status": "invalid_entry_exit_order"}
    entry_decision_event = mint_tape[entry_decision_index]
    exit_trigger_event = mint_tape[exit_trigger_index]
    entry_decision_slot = int(entry_decision_event.get("slot") or 0)
    exit_trigger_slot = int(exit_trigger_event.get("slot") or 0)
    if exit_trigger_slot <= entry_decision_slot:
        return {"status": "not_later_slot"}
    if launch and launch.get("is_cashback_enabled"):
        return {"status": "unsupported_cashback_launch"}

    if fill_policy == "after_observed":
        entry_index, exit_index = entry_decision_index, exit_trigger_index
    elif fill_policy == "next_later_slot":
        entry_index = _first_later_slot_index(mint_tape, entry_decision_index, entry_decision_slot)
        if entry_index is None:
            return {"status": "no_later_slot_entry_fill"}
        if entry_index >= exit_trigger_index:
            return {"status": "exit_trigger_before_entry_fill"}
        exit_index = _first_later_slot_index(mint_tape, exit_trigger_index, exit_trigger_slot)
        if exit_index is None:
            return {"status": "no_later_slot_exit_fill"}
    else:
        return {"status": "unknown_fill_policy"}

    entry_event, exit_event = mint_tape[entry_index], mint_tape[exit_index]
    entry_slot, exit_slot = int(entry_event.get("slot") or 0), int(exit_event.get("slot") or 0)
    if exit_slot <= entry_slot:
        return {"status": "not_later_slot"}

    interval = mint_tape[entry_index + 1:exit_index + 1]
    # Pump V3 supports Mayhem curves. Their special fee recipient does not
    # change the curve quote math; completion is still excluded below.
    if any(int(event.get("cashback_bps") or 0) > 0 or int(event.get("cashback") or 0) > 0
           for event in [entry_event, *interval, exit_event]):
        return {"status": "unsupported_cashback_mode"}
    if any(entry_decision_slot <= slot <= exit_slot for slot in complete_slots):
        return {"status": "curve_completed_before_exit"}

    max_error = 0.0
    if fill_policy == "next_later_slot":
        previous_pre_entry = entry_decision_event
        for event in mint_tape[entry_decision_index + 1:entry_index + 1]:
            check = validate_observed_transition(previous_pre_entry, event, max_formula_error)
            if not check["ok"]:
                return {
                    "status": check["reason"] or "invalid_pre_entry_transition",
                    "relative_error": check["relative_error"],
                }
            max_error = max(max_error, check["relative_error"] or 0.0)
            previous_pre_entry = event

    previous = entry_event
    for event in interval:
        check = validate_observed_transition(previous, event, max_formula_error)
        if not check["ok"]:
            return {
                "status": check["reason"] or "invalid_observed_transition",
                "relative_error": check["relative_error"],
            }
        max_error = max(max_error, check["relative_error"] or 0.0)
        previous = event

    vq = int(entry_event["virtual_sol_reserves_raw"])
    vt = int(entry_event["virtual_token_reserves_raw"])
    protocol_in, creator_in = _fee_bps(entry_event)
    spend_lamports = int(round(stake_usd / sol_usd * 1_000_000_000))
    buy = quote_buy_exact_quote_in(spend_lamports, vq, vt, protocol_in, creator_in)
    tokens = buy["tokens_out"]

    real_tokens = int(entry_event.get("real_token_reserves_raw") or 0)
    if real_tokens and tokens >= real_tokens:
        return {"status": "entry_would_complete_curve"}
    spot_entry = vq / vt
    entry_average_curve_price = buy["net_quote_in"] / tokens
    entry_impact = entry_average_curve_price / spot_entry - 1.0

    state = {
        "virtual_quote_reserves": vq + buy["net_quote_in"],
        "virtual_token_reserves": vt - tokens,
    }
    if real_tokens:
        state["real_token_reserves"] = real_tokens - tokens
        if state["real_token_reserves"] <= 0:
            return {"status": "entry_would_complete_curve"}
    try:
        for event in interval:
            state = apply_external_trade(state, event)
    except ValueError as exc:
        return {"status": "counterfactual_curve_invalid", "detail": str(exc)}

    protocol_out, creator_out = _fee_bps(exit_event)
    vq_exit, vt_exit = int(state["virtual_quote_reserves"]), int(state["virtual_token_reserves"])
    spot_exit = vq_exit / vt_exit
    sell = quote_sell_exact_tokens(tokens, vq_exit, vt_exit, protocol_out, creator_out)
    exit_average_curve_price = sell["gross_quote_out"] / tokens
    exit_impact = 1.0 - exit_average_curve_price / spot_exit
    proceeds_usd = sell["net_quote_out"] / 1_000_000_000 * sol_usd
    pnl_usd = proceeds_usd - stake_usd - gas_usd

    return {
        "status": "ok",
        "mint": mint,
        "symbol": trade.get("symbol"),
        "entry_signature": entry_signature,
        "exit_signature": exit_signature,
        "fill_policy": fill_policy,
        "entry_fill_signature": entry_event.get("signature"),
        "exit_fill_signature": exit_event.get("signature"),
        "entry_decision_slot": entry_decision_slot,
        "exit_trigger_slot": exit_trigger_slot,
        "entry_slot": entry_slot,
        "exit_slot": exit_slot,
        "external_events_replayed": len(interval),
        "max_observed_formula_relative_error": max_error,
        "entry_protocol_fee_bps": protocol_in,
        "entry_creator_fee_bps": creator_in,
        "exit_protocol_fee_bps": protocol_out,
        "exit_creator_fee_bps": creator_out,
        "stake_usd": stake_usd,
        "sol_usd_assumption": sol_usd,
        "spend_lamports": spend_lamports,
        "net_curve_input_lamports": buy["net_quote_in"],
        "entry_fee_lamports": buy["total_fees"],
        "tokens_received_raw": tokens,
        "gross_exit_lamports": sell["gross_quote_out"],
        "exit_fee_lamports": sell["total_fees"],
        "net_exit_lamports": sell["net_quote_out"],
        "proceeds_usd": proceeds_usd,
        "gas_usd": gas_usd,
        "curve_replay_pnl_usd": pnl_usd,
        "curve_replay_return_pct": pnl_usd / stake_usd * 100.0,
        "entry_curve_price_impact_pct": entry_impact * 100.0,
        "exit_curve_price_impact_pct": exit_impact * 100.0,
        "recorded_paper_pnl_usd": float(trade.get("net_pnl_usd") or 0.0),
        "recorded_market_move_pct": float(trade.get("market_move_at_exit") or 0.0) * 100.0,
    }


def _summarize(trades: list[dict], replayed: list[dict], skipped: Counter) -> dict:
    pnls = [float(row["curve_replay_pnl_usd"]) for row in replayed]
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p <= 0]
    gross_profit, gross_loss = sum(wins), abs(sum(losses))
    recorded = sum(float(row.get("recorded_paper_pnl_usd") or 0.0) for row in replayed)
    return {
        "trades_in_lane": len(trades),
        "replayed_trades": len(replayed),
        "skipped_trades": sum(skipped.values()),
        "skip_reasons": dict(skipped),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(pnls) if pnls else None,
        "curve_replay_net_pnl_usd": round(sum(pnls), 6),
        "curve_replay_average_pnl_usd": round(mean(pnls), 6) if pnls else None,
        "curve_replay_median_pnl_usd": round(median(pnls), 6) if pnls else None,
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss else None,
        "recorded_paper_pnl_same_replayed_trades_usd": round(recorded, 6),
        "curve_replay_delta_vs_recorded_usd": round(sum(pnls) - recorded, 6),
        "median_entry_curve_impact_pct": round(median(
            row["entry_curve_price_impact_pct"] for row in replayed
        ), 6) if replayed else None,
        "median_exit_curve_impact_pct": round(median(
            row["exit_curve_price_impact_pct"] for row in replayed
        ), 6) if replayed else None,
    }


def build_report(data_dir: str, out_dir: str, *, sol_usd: float,
                 stake_usd: float = 1.0, gas_usd: float = 0.0285,
                 max_formula_error: float = MAX_FORMULA_REL_ERROR) -> dict:
    if sol_usd <= 0 or stake_usd <= 0 or gas_usd < 0:
        raise ValueError("SOL/USD and stake must be positive; gas cannot be negative")
    tape = read_jsonl(os.path.join(data_dir, "sniper_tape_sniper.jsonl"))
    launches = {
        row["mint"]: row for row in read_jsonl(os.path.join(data_dir, "sniper_launches_sniper.jsonl"))
        if row.get("mint")
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

    lane_results, all_details = {}, {}
    next_slot_results, next_slot_details = {}, {}
    for lane_name, filename in LANES.items():
        trades = read_jsonl(os.path.join(data_dir, filename))
        for fill_policy, summaries, details in (
            ("after_observed", lane_results, all_details),
            ("next_later_slot", next_slot_results, next_slot_details),
        ):
            replayed, skipped = [], Counter()
            for trade in trades:
                mint = trade.get("mint")
                if mint not in by_mint:
                    skipped["mint_missing_from_sol_tape"] += 1
                    continue
                replay = _replay_one(
                    trade, by_mint[mint], signatures[mint], complete_by_mint[mint],
                    sol_usd=sol_usd, stake_usd=stake_usd, gas_usd=gas_usd,
                    max_formula_error=max_formula_error, launch=launches.get(mint),
                    fill_policy=fill_policy,
                )
                if replay["status"] == "ok":
                    replayed.append(replay)
                else:
                    skipped[replay["status"]] += 1
            summaries[lane_name] = _summarize(trades, replayed, skipped)
            details[lane_name] = replayed

    result = {
        "model": "counterfactual Pump bonding-curve replay; latency-aware sniper lanes only",
        "paper_only": True,
        "sol_usd_assumption": sol_usd,
        "stake_usd": stake_usd,
        "gas_usd_round_trip": gas_usd,
        "max_formula_relative_error": max_formula_error,
        "fill_policy_definitions": {
            "after_observed": "Optimistic lower bound: hypothetical buy/sell immediately after the selected entry/exit observation transaction.",
            "next_later_slot": "Conservative sensitivity: wait through the decision slot and all same-slot transactions, then fill after the first observed transaction in a later slot; repeat after the exit trigger.",
        },
        "lanes": lane_results,
        "next_later_slot_lanes": next_slot_results,
        "trades": all_details,
        "next_later_slot_trades": next_slot_details,
        "method": [
            "Use each lane's already-recorded entry and exit signatures; do not change signal or exit decisions.",
            "Report both an optimistic fill immediately after each selected observation and a next-later-slot sensitivity that waits through same-slot transactions before entry and exit.",
            "Buy size is stake_usd converted to SOL using the supplied SOL/USD reference price.",
            "Buy quote uses Pump's documented exact-quote-in fee rounding and net_quote_in minus one lamport formula.",
            "Each later observed buy keeps its SOL input fixed; each later observed sell keeps its token input fixed; outputs are recalculated against counterfactual reserves.",
            "Sell proceeds deduct protocol and creator fees separately using rates observed in the exit transaction.",
            "Gas uses the existing paper assumption. No fixed 1.5% slippage is added because curve quotes explicitly model price impact.",
            "Both models require continuous observed reserve transitions and no curve completion. The next-later-slot model also validates the full observed path before entry and requires a later-slot event after both decision points.",
        ],
        "limitations": [
            "This is not a confirmed on-chain fill or transaction-inclusion simulation. Actual order submission, leader inclusion, priority bidding, and failed transactions are not recorded.",
            "The immediate-after-observation fill is optimistic; the next-later-slot model is a conservative sensitivity, not a guaranteed fill. Actual submission, leader inclusion, priority bidding and failed transactions are not recorded.",
            "SOL/USD is a reference-price assumption used to convert the USD stake into lamports; it was not captured per transaction in the original tape.",
            "The original tape omitted real reserves, Mayhem/cashback flags, and raw fee amounts. Formula mismatches are rejected, but the historical tape cannot prove every unsupported curve feature was absent.",
            "PumpSwap trading after curve completion is not replayed; such positions are unsupported rather than silently valued.",
            "Mayhem V3 trades are supported when the captured window remains on-curve and does not complete; cashback coins remain excluded.",
            "The three latency lanes overlap and are counterfactual variants; never add their P&Ls.",
            "One-time user-volume-accumulator rent and token-account rent timing are not allocated to individual trades.",
        ],
    }

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "sniper_curve_execution_replay.json"), "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)

    lines = [
        "# Sniper curve-aware execution replay", "",
        f"- SOL/USD assumption: {sol_usd:.4f}",
        f"- Stake: {stake_usd:.2f} USD",
        f"- Round-trip gas assumption: {gas_usd:.4f} USD",
        "- Signals and exits: unchanged; only existing latency-aware sniper trades are re-quoted.",
        "",
        "| Lane | Trades | Immediate replay | Immediate W/L | Immediate P&L | Next-slot replay | Next-slot W/L | Next-slot P&L |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for lane, summary in lane_results.items():
        strict = next_slot_results[lane]
        lines.append(
            f"| {lane} | {summary['trades_in_lane']} | {summary['replayed_trades']} | "
            f"{summary['wins']}/{summary['losses']} | {summary['curve_replay_net_pnl_usd']:.4f} USD | "
            f"{strict['replayed_trades']} | {strict['wins']}/{strict['losses']} | "
            f"{strict['curve_replay_net_pnl_usd']:.4f} USD |"
        )
    lines += ["", "The immediate-after-observation model is optimistic. The next-later-slot column waits through all same-slot events before entering and again before exiting; it is still a sensitivity model, not a confirmed transaction fill.", "", "## Method and limitations", ""]
    lines.extend(f"- {item}" for item in result["method"])
    lines.extend(["", "### Limitations", ""])
    lines.extend(f"- {item}" for item in result["limitations"])
    with open(os.path.join(out_dir, "sniper_curve_execution_replay.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return result
