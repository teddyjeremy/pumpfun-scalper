import pytest

from pumpbot.curve_replay import (
    apply_external_trade,
    quote_buy_exact_quote_in,
    quote_sell_exact_tokens,
)
from scripts.sniper_curve_path_diagnostics import (
    LAMPORTS_PER_SOL,
    _analyse_trade,
    _group_transactions,
)


def _buy_event(signature, slot, quote_in, vq, vt, *, t_plus=1.0):
    tokens_out = (quote_in - 1) * vt // (vq + quote_in - 1)
    next_vq = vq + quote_in
    next_vt = vt - tokens_out
    return {
        "mint": "MintA",
        "quote_mint": "SOL",
        "signature": signature,
        "slot": slot,
        "t_plus": t_plus,
        "side": "buy",
        "trader": f"wallet-{signature}",
        "sol_amount_raw": quote_in,
        "token_amount_raw": tokens_out,
        "virtual_sol_reserves_raw": next_vq,
        "virtual_token_reserves_raw": next_vt,
        "real_token_reserves_raw": 700_000_000_000_000 - tokens_out,
        "fee_bps": 95,
        "creator_fee_bps": 30,
        "cashback_bps": 0,
        "cashback_fee_raw": 0,
    }


def _sell_event(signature, slot, tokens_in, vq, vt, *, t_plus=2.0):
    quote_out = vq * tokens_in // (vt + tokens_in)
    return {
        "mint": "MintA",
        "quote_mint": "SOL",
        "signature": signature,
        "slot": slot,
        "t_plus": t_plus,
        "side": "sell",
        "trader": f"wallet-{signature}",
        "sol_amount_raw": quote_out,
        "token_amount_raw": tokens_in,
        "virtual_sol_reserves_raw": vq - quote_out,
        "virtual_token_reserves_raw": vt + tokens_in,
        "real_token_reserves_raw": 700_000_000_000_000,
        "fee_bps": 95,
        "creator_fee_bps": 30,
        "cashback_bps": 0,
        "cashback_fee_raw": 0,
    }


def test_group_transactions_keeps_inner_events_atomic():
    rows = [
        {"signature": "tx-a", "side": "buy"},
        {"signature": "tx-a", "side": "sell"},
        {"signature": "tx-b", "side": "buy"},
    ]
    groups = _group_transactions(rows)
    assert [len(group) for group in groups] == [2, 1]
    assert [group[0]["signature"] for group in groups] == ["tx-a", "tx-b"]


def test_analyse_trade_quotes_only_after_complete_transactions():
    entry = {
        "mint": "MintA",
        "quote_mint": "SOL",
        "signature": "entry",
        "slot": 10,
        "t_plus": 0.0,
        "side": "buy",
        "trader": "signal-wallet",
        "sol_amount_raw": 1_000_000_000,
        "token_amount_raw": 30_000_000_000_000,
        "virtual_sol_reserves_raw": 31_000_000_000,
        "virtual_token_reserves_raw": 970_000_000_000_000,
        "real_token_reserves_raw": 670_000_000_000_000,
        "fee_bps": 95,
        "creator_fee_bps": 30,
        "cashback_bps": 0,
        "cashback_fee_raw": 0,
    }
    # The selected entry mark is the final row of its signature.
    vq, vt = entry["virtual_sol_reserves_raw"], entry["virtual_token_reserves_raw"]
    buy_one = _buy_event("buy-bundle", 11, 400_000_000, vq, vt, t_plus=1.0)
    buy_two = _buy_event(
        "buy-bundle", 11, 300_000_000,
        buy_one["virtual_sol_reserves_raw"],
        buy_one["virtual_token_reserves_raw"], t_plus=1.0,
    )
    sell = _sell_event(
        "exit", 12, 5_000_000_000_000,
        buy_two["virtual_sol_reserves_raw"],
        buy_two["virtual_token_reserves_raw"], t_plus=2.0,
    )
    tape = [entry, buy_one, buy_two, sell]
    indices = {"entry": 0, "buy-bundle": 2, "exit": 3}

    sol_usd, stake_usd, gas_usd = 70.0, 1.0, 0.0285
    spend = round(stake_usd / sol_usd * LAMPORTS_PER_SOL)
    buy = quote_buy_exact_quote_in(
        spend, entry["virtual_sol_reserves_raw"],
        entry["virtual_token_reserves_raw"], 95, 30,
    )
    state = {
        "virtual_quote_reserves": entry["virtual_sol_reserves_raw"] + buy["net_quote_in"],
        "virtual_token_reserves": entry["virtual_token_reserves_raw"] - buy["tokens_out"],
        "real_token_reserves": entry["real_token_reserves_raw"] - buy["tokens_out"],
    }
    state = apply_external_trade(state, buy_one)
    state = apply_external_trade(state, buy_two)
    state = apply_external_trade(state, sell)
    quote = quote_sell_exact_tokens(
        buy["tokens_out"], state["virtual_quote_reserves"],
        state["virtual_token_reserves"], 95, 30,
    )
    expected_pnl = quote["net_quote_out"] / LAMPORTS_PER_SOL * sol_usd - stake_usd - gas_usd

    trade = {
        "mint": "MintA",
        "symbol": "A",
        "paired_entry_signature": "entry",
        "paired_exit_signature": "exit",
        "paired_exit_trigger_signature": "exit-trigger",
        "paired_exit_reason": "stop_loss",
        "curve_replay_pnl_usd": expected_pnl,
    }
    result, transactions, sell_recovery, skipped = _analyse_trade(
        trade, tape, indices, sol_usd=sol_usd, stake_usd=stake_usd,
        gas_usd=gas_usd, max_formula_error=1e-4, lane="latency_500ms_wallet_lock_flow_gate",
    )

    assert skipped is None
    assert result is not None
    assert result["transactions_replayed"] == 2
    assert result["events_replayed"] == 3
    assert transactions[0]["event_count"] == 2
    assert transactions[0]["signature"] == "buy-bundle"
    assert transactions[1]["signature"] == "exit"
    assert result["curve_pnl_reconciliation_error_usd"] == pytest.approx(0.0, abs=1e-8)
    assert result["curve_exit_pnl_usd"] == pytest.approx(expected_pnl, abs=1e-8)
    assert len(sell_recovery) == 1
    assert sell_recovery[0]["sell_signature"] == "exit"
