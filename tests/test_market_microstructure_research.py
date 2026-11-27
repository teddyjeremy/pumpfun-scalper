import json

from scripts.market_microstructure_research import (
    _execution_audit,
    _quantile_groups,
    build_mint_snapshots,
    cost_hurdle,
)


def _tx(seq, observed, slot, price, buys=None, sell=0.0, mint="mint-a", signature=None):
    return {
        "mint": mint,
        "signature": signature or f"sig-{seq}",
        "observation_sequence": seq,
        "observed_at_epoch": observed,
        "slot": slot,
        "final_price_sol": price,
        "wallet_buy_sol": buys or {},
        "sell_sol": sell,
        "creator_sell": False,
        "final_virtual_sol_reserves_raw": 30_000_000_000,
    }


def test_cost_hurdle_matches_configured_costs():
    assert round(cost_hurdle(), 4) == 0.0868


def test_snapshot_uses_later_slot_and_forward_mark_after_delay():
    rows = [
        _tx(1, 100.0, 10, 1.00, {"buyer-a": 0.01}),
        _tx(2, 101.0, 10, 1.01, {"buyer-a": 0.02}),
        _tx(3, 103.5, 11, 1.02, {"buyer-b": 0.03}),
        _tx(4, 104.1, 11, 1.03, {"buyer-c": 0.04}),
        _tx(5, 105.1, 12, 1.04, {"buyer-d": 0.05}),
        _tx(6, 110.2, 13, 1.15, {"buyer-e": 0.06}),
    ]
    result = build_mint_snapshots(rows, delays=(0.5,), horizons=(5,))
    assert len(result) == 1
    row = result[0]
    assert row["distinct_buyers_5s"] == 3
    assert row["external_buy_sol_5s"] == 0.10
    assert round(row["delay_0.5s_entry_lag_seconds"], 4) == 1.0
    assert round(row["delay_0.5s_return_5s"], 4) == round(1.15 / 1.04 - 1, 4)


def test_no_later_slot_entry_is_not_counted_as_a_fill():
    rows = [
        _tx(1, 100.0, 10, 1.00),
        _tx(2, 103.5, 11, 1.02),
        _tx(3, 104.5, 11, 1.04),
        _tx(4, 110.0, 11, 1.10),
    ]
    result = build_mint_snapshots(rows, delays=(1.0,), horizons=(5,))
    assert len(result) == 1
    assert result[0]["delay_1.0s_entry_lag_seconds"] is None
    assert result[0]["delay_1.0s_return_5s"] is None


def test_missing_forward_mark_remains_missing_not_a_loss():
    rows = [
        _tx(1, 100.0, 10, 1.00),
        _tx(2, 103.5, 11, 1.02),
        _tx(3, 104.5, 11, 1.04),
        _tx(4, 105.1, 12, 1.05),
    ]
    result = build_mint_snapshots(rows, delays=(0.5,), horizons=(5,))
    assert round(result[0]["delay_0.5s_entry_lag_seconds"], 4) == 0.6
    assert result[0]["delay_0.5s_return_5s"] is None


def test_quantile_groups_never_split_equal_feature_values():
    rows = [{"mint": f"m{i}", "feature": 1.0} for i in range(8)]
    groups = _quantile_groups(rows, "feature")
    assert len(groups["Q1"]) == 8
    assert not groups["Q2"] and not groups["Q3"] and not groups["Q4"]


def test_execution_audit_maps_signatures_within_mint(tmp_path):
    transactions = [
        _tx(1, 100.0, 10, 1.0, mint="mint-a", signature="shared-signal"),
        _tx(2, 100.1, 11, 1.1, mint="mint-a", signature="entry-a"),
        _tx(3, 100.0, 20, 1.0, mint="mint-b", signature="shared-signal"),
    ]
    trade_path = tmp_path / "sniper_tx_trades_sniper.jsonl"
    trade_path.write_text(json.dumps({
        "mint": "mint-a", "signal_signature": "shared-signal",
        "entry_signature": "entry-a", "exit_signature": "timer",
        "net_pnl_usd": 0.1,
    }) + "\n", encoding="utf-8")
    result = _execution_audit(transactions, str(tmp_path))["transaction_complete"]
    assert result["trades_with_mapped_signal_and_entry"] == 1
    assert result["entries_later_signal_slot"] == 1
    assert result["entries_same_signal_slot"] == 0
