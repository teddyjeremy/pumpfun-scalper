from scripts.wallet_intelligence import build_outcomes, wallet_profile


def test_outcomes_require_complete_tape_and_matured_horizon():
    tape = [
        {"mint": "A", "trader": "w1", "side": "buy", "bt": 100, "price_usd": 1.0},
        {"mint": "A", "trader": "w2", "side": "buy", "bt": 100, "price_usd": 2.0},
        {"mint": "A", "trader": "w1", "side": "sell", "bt": 160, "price_usd": 1.1},
        {"mint": "A", "trader": "w1", "side": "sell", "bt": 220, "price_usd": 1.2},
        {"mint": "B", "trader": "w3", "side": "buy", "bt": 100, "price_usd": 1.0},
        {"mint": "B", "trader": "w3", "side": "sell", "bt": 220, "price_usd": 2.0},
    ]
    outcomes = build_outcomes(tape, [
        {"mint": "A", "observation_complete": True},
        {"mint": "B", "observation_complete": False},
    ])
    w1 = [x for x in outcomes if x["wallet"] == "w1"]
    assert [(x["horizon_s"], x["matured_bt"]) for x in w1] == [(60, 160), (120, 220)]
    assert not any(x["wallet"] == "w3" for x in outcomes)


def test_wallet_classification_requires_five_distinct_mints_and_shrinks():
    outcomes = [
        {"wallet": "w", "mint": f"m{i}", "horizon_s": 60, "return": 0.10}
        for i in range(5)
    ]
    assert wallet_profile("w", outcomes[:4], {})["category"] == "unknown"
    profile = wallet_profile("w", outcomes, {})
    assert profile["category"] == "positive_history"
    assert profile["shrunk_mean_return_60s"] == 0.05


def test_high_frequency_uses_prior_run_activity_only():
    outcomes = [
        {"wallet": "w", "mint": f"m{i}", "horizon_s": 60, "return": 0.10}
        for i in range(5)
    ]
    assert wallet_profile("w", outcomes, {})["category"] == "positive_history"
    assert wallet_profile("w", outcomes, {"w": {"events": 100, "mints": []}})["category"] == "high_frequency"
