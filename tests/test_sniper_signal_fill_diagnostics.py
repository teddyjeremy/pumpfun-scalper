from scripts.sniper_signal_fill_diagnostics import build_diagnostics, summarize_transaction


def _event(signature, slot, side, trader, creator, price, sol):
    return {
        "mint": "mint-1", "signature": signature, "slot": slot,
        "quote_mint": "SOL", "side": side, "trader": trader,
        "creator": creator, "price_sol": price,
        "sol_amount_raw": int(sol * 1_000_000_000),
        "block_timestamp": 100 + slot,
    }


def test_summarize_transaction_aggregates_inner_events_and_excludes_creator_wallets():
    tx = summarize_transaction([
        _event("fill", 12, "buy", "creator", "creator", 2.0, 1.0),
        _event("fill", 12, "sell", "wallet-a", "creator", 2.1, 0.25),
        _event("fill", 12, "buy", "wallet-b", "creator", 2.2, 0.5),
    ])
    assert tx["side"] == "mixed"
    assert tx["buy_sol"] == 1.5
    assert tx["sell_sol"] == 0.25
    assert tx["net_flow_sol"] == 1.25
    assert tx["distinct_external_buyers"] == 1
    assert tx["distinct_external_sellers"] == 1
    assert tx["price_sol"] == 2.2


def test_build_diagnostics_maps_entry_and_exit_transitions():
    tape = [
        _event("entry-decision", 10, "buy", "wallet-a", "creator", 1.0, 1.0),
        _event("entry-fill", 12, "sell", "wallet-b", "creator", 0.9, 0.4),
        _event("exit-trigger", 20, "sell", "wallet-b", "creator", 0.8, 0.6),
        _event("exit-fill", 21, "buy", "wallet-c", "creator", 0.85, 0.2),
    ]
    replay = {"next_later_slot_trades": {"latency_500ms": [{
        "mint": "mint-1", "symbol": "TEST", "entry_signature": "entry-decision",
        "entry_fill_signature": "entry-fill", "exit_signature": "exit-trigger",
        "exit_fill_signature": "exit-fill", "curve_replay_pnl_usd": -0.1,
    }]}}
    result = build_diagnostics(replay, tape)["lanes"]["latency_500ms"]
    trade = result["trades"][0]
    assert trade["entry_slot_gap"] == 2
    assert round(trade["entry_price_move_pct"], 6) == -10.0
    assert trade["entry_fill_side"] == "sell"
    assert round(trade["entry_fill_net_flow_sol"], 6) == -0.4
    assert round(trade["exit_price_move_pct"], 6) == 6.25
    assert result["sell_fill_losses"] == 1
    assert result["mapped_entry_fills"] == 1


def test_missing_signatures_are_reported_without_failing():
    replay = {"next_later_slot_trades": {"latency_500ms": [{
        "mint": "mint-1", "symbol": "TEST", "entry_signature": "missing-a",
        "entry_fill_signature": "missing-b", "exit_signature": "missing-c",
        "exit_fill_signature": "missing-d", "curve_replay_pnl_usd": 0.0,
    }]}}
    result = build_diagnostics(replay, [])["lanes"]["latency_500ms"]
    assert result["mapped_entry_fills"] == 0
    assert result["missing_transaction_records"] == {
        "entry_decision": 1, "entry_fill": 1, "exit_trigger": 1, "exit_fill": 1,
    }
