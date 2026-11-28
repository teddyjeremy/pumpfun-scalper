from scripts import paired_latency_curve_replay as paired_curve


def test_curve_replay_uses_paired_delayed_exit_fill_without_adding_latency(monkeypatch):
    captured = {}

    def fake_replay_one(trade, mint_tape, signature_indices, complete_slots, **kwargs):
        captured["trade"] = trade
        captured["kwargs"] = kwargs
        return {"status": "ok", "entry_signature": trade["entry_signature"],
                "exit_signature": trade["exit_signature"]}

    monkeypatch.setattr(paired_curve, "_replay_one", fake_replay_one)
    paired_trade = {
        "mint": "MintA",
        "symbol": "A",
        "signal_signature": "signal",
        "entry_signature": "delayed-entry-fill",
        "exit_trigger_signature": "stop-trigger",
        "exit_signature": "delayed-exit-fill",
        "net_pnl_usd": -0.12,
        "market_move_at_exit": -0.15,
        "entry_slot": 10,
        "exit_trigger_slot": 12,
        "exit_slot": 13,
        "exit_reason": "stop_loss",
    }

    result = paired_curve.replay_paired_trade(
        paired_trade, [], {}, set(), sol_usd=70.0, stake_usd=1.0,
        gas_usd=0.0285, max_formula_error=1e-4,
    )

    assert captured["trade"]["entry_signature"] == "delayed-entry-fill"
    assert captured["trade"]["exit_signature"] == "delayed-exit-fill"
    assert captured["trade"]["exit_signature"] != paired_trade["exit_trigger_signature"]
    assert captured["kwargs"]["fill_policy"] == "after_observed"
    assert result["paired_exit_trigger_signature"] == "stop-trigger"
    assert result["paired_exit_signature"] == "delayed-exit-fill"
