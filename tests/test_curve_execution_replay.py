from pumpbot.curve_replay import (
    apply_external_trade,
    quote_buy_exact_quote_in,
    quote_sell_exact_tokens,
    validate_observed_transition,
)


def test_buy_quote_uses_documented_fee_rounding_and_minus_one_lamport():
    spend = 100_000_000
    vq = 40_000_000_000
    vt = 800_000_000_000_000
    result = quote_buy_exact_quote_in(spend, vq, vt, 95, 30)
    net = spend * 10_000 // 10_125
    protocol_fee = (net * 95 + 9_999) // 10_000
    creator_fee = (net * 30 + 9_999) // 10_000
    if net + protocol_fee + creator_fee > spend:
        net -= net + protocol_fee + creator_fee - spend
    expected = (net - 1) * vt // (vq + net - 1)
    assert result["net_quote_in"] == net
    assert result["tokens_out"] == expected
    assert result["total_fees"] == protocol_fee + creator_fee


def test_sell_quote_deducts_protocol_and_creator_fees_separately():
    result = quote_sell_exact_tokens(10**12, 40_000_000_000, 800_000_000_000_000, 95, 30)
    gross = 40_000_000_000 * 10**12 // (800_000_000_000_000 + 10**12)
    protocol = (gross * 95 + 9_999) // 10_000
    creator = (gross * 30 + 9_999) // 10_000
    assert result["gross_quote_out"] == gross
    assert result["net_quote_out"] == gross - protocol - creator
    assert result["total_fees"] == protocol + creator


def test_external_buy_recalculates_tokens_against_counterfactual_reserves():
    state = {"virtual_quote_reserves": 30_000_000_000, "virtual_token_reserves": 1_000_000_000_000_000}
    event = {"side": "buy", "sol_amount_raw": 100_000_000, "token_amount_raw": 0}
    result = apply_external_trade(state, event)
    expected = (100_000_000 - 1) * state["virtual_token_reserves"] // (
        state["virtual_quote_reserves"] + 100_000_000 - 1
    )
    assert result["virtual_quote_reserves"] == state["virtual_quote_reserves"] + 100_000_000
    assert result["virtual_token_reserves"] == state["virtual_token_reserves"] - expected


def test_external_sell_recalculates_quote_against_counterfactual_reserves():
    state = {"virtual_quote_reserves": 30_000_000_000, "virtual_token_reserves": 1_000_000_000_000_000}
    event = {"side": "sell", "sol_amount_raw": 0, "token_amount_raw": 10**12}
    result = apply_external_trade(state, event)
    expected = state["virtual_quote_reserves"] * 10**12 // (
        state["virtual_token_reserves"] + 10**12
    )
    assert result["virtual_quote_reserves"] == state["virtual_quote_reserves"] - expected
    assert result["virtual_token_reserves"] == state["virtual_token_reserves"] + 10**12


def test_observed_buy_transition_checks_reserves_and_curve_output():
    previous = {"virtual_sol_reserves_raw": 30_000_000_000, "virtual_token_reserves_raw": 1_000_000_000_000_000}
    quote = 100_000_000
    tokens = (quote - 1) * previous["virtual_token_reserves_raw"] // (
        previous["virtual_sol_reserves_raw"] + quote - 1
    )
    event = {
        "side": "buy",
        "sol_amount_raw": quote,
        "token_amount_raw": tokens,
        "virtual_sol_reserves_raw": previous["virtual_sol_reserves_raw"] + quote,
        "virtual_token_reserves_raw": previous["virtual_token_reserves_raw"] - tokens,
    }
    assert validate_observed_transition(previous, event)["ok"]


def test_observed_transition_rejects_a_reserve_gap():
    previous = {"virtual_sol_reserves_raw": 30_000_000_000, "virtual_token_reserves_raw": 1_000_000_000_000_000}
    event = {
        "side": "buy", "sol_amount_raw": 100_000_000, "token_amount_raw": 1,
        "virtual_sol_reserves_raw": 30_200_000_000,
        "virtual_token_reserves_raw": 999_000_000_000_000,
    }
    result = validate_observed_transition(previous, event)
    assert not result["ok"] and result["reason"] == "reserve_gap"



def test_mayhem_v3_curve_is_supported_for_non_completing_window():
    from pumpbot.curve_replay import _replay_one

    mint = "MayhemMint"
    vq, vt = 30_000_000_000, 1_000_000_000_000_000
    quote = 100_000_000
    tokens = (quote - 1) * vt // (vq + quote - 1)
    entry = {
        "mint": mint, "signature": "entry", "slot": 10, "side": "buy",
        "sol_amount_raw": quote, "token_amount_raw": tokens,
        "virtual_sol_reserves_raw": vq + quote,
        "virtual_token_reserves_raw": vt - tokens,
        "real_token_reserves_raw": 700_000_000_000_000,
        "fee_bps": 95, "creator_fee_bps": 30,
        "mayhem_mode": True, "cashback_bps": 0, "cashback": 0,
    }
    next_quote = 10_000_000
    next_tokens = (next_quote - 1) * (vt - tokens) // (vq + quote + next_quote - 1)
    exit_event = {
        "mint": mint, "signature": "exit", "slot": 11, "side": "buy",
        "sol_amount_raw": next_quote, "token_amount_raw": next_tokens,
        "virtual_sol_reserves_raw": vq + quote + next_quote,
        "virtual_token_reserves_raw": vt - tokens - next_tokens,
        "real_token_reserves_raw": 699_000_000_000_000,
        "fee_bps": 95, "creator_fee_bps": 30,
        "mayhem_mode": True, "cashback_bps": 0, "cashback": 0,
    }
    result = _replay_one(
        {"mint": mint, "entry_signature": "entry", "exit_signature": "exit",
         "net_pnl_usd": -0.1, "market_move_at_exit": 0.02},
        [entry, exit_event], {"entry": 0, "exit": 1}, set(),
        sol_usd=73.35, stake_usd=1.0, gas_usd=0.0285,
        max_formula_error=1e-4,
        launch={"is_mayhem_mode": True, "is_cashback_enabled": False},
    )
    assert result["status"] == "ok"
