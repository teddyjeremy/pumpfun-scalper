from pumpbot.tape import Tape


def test_research_features_are_available_without_changing_entry_api():
    tape = Tape("mint", 1000)
    for i in range(40):
        tape.append({
            "bt": 1000 + i,
            "t": i,
            "side": "buy" if i % 3 else "sell",
            "spot": 1.0 + i * 0.01,
            "usd": 10.0 + i,
            "trader": f"wallet-{i % 7}",
            "tok": 1.0,
            "slot": 100 + i,
        })

    features = tape.features(tape.n - 1)

    assert features["ret5"] is not None
    assert features["ret10"] is not None
    assert features["ret20"] is not None
    assert features["ret30"] is not None
    assert "buy_volume_accel_10s" in features
    assert "sell_volume_accel_10s" in features
    assert "large_sells_10s" in features
    # Existing strategy inputs remain present.
    assert "b10" in features and "s10" in features and "swing30" in features
