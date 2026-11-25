from pumpbot.exit_research import simulate_exit_variants


def test_parallel_exit_variants_use_confirmed_and_price_confirmed_rules():
    trade = {"entry_bt": 100, "ent_px": 1.0}
    tape = [
        {"bt": 106, "side": "sell", "price_usd": 0.95},
        {"bt": 107, "side": "sell", "price_usd": 0.94},
        {"bt": 108, "side": "sell", "price_usd": 0.93},
        {"bt": 109, "side": "sell", "price_usd": 0.92},
        {"bt": 110, "side": "sell", "price_usd": 0.91},
        {"bt": 111, "side": "sell", "price_usd": 0.90},
    ]

    results = {x["variant"]: x for x in simulate_exit_variants(trade, tape)}
    price_confirmed = results["price_confirmed_sell_surge"]
    confirmed = results["confirmed_sell_surge_2s"]

    assert price_confirmed["trigger_bt"] == 108
    assert price_confirmed["exit_bt"] == 109
    assert confirmed["trigger_bt"] == 110
    assert confirmed["exit_bt"] == 111
    assert price_confirmed["executable_exit"] is True
    assert confirmed["executable_exit"] is True
    assert price_confirmed["net_per_1usd"] < 0
    assert confirmed["net_per_1usd"] < 0


def test_no_trigger_is_mark_only_not_an_executable_exit():
    trade = {"entry_bt": 100, "ent_px": 1.0}
    tape = [
        {"bt": 106, "side": "buy", "price_usd": 1.01},
        {"bt": 107, "side": "sell", "price_usd": 1.02},
    ]

    results = simulate_exit_variants(trade, tape)

    assert len(results) == 2
    assert all(x["status"] == "no_trigger_observation_end_mark" for x in results)
    assert all(x["executable_exit"] is False for x in results)
