from pumpbot.config import Config
from pumpbot.engine import CoinEngine
from pumpbot.tape import Tape


def append_trade(tape, bt, t, spot):
    return tape.append({
        "bt": bt, "t": t, "spot": spot, "side": "buy", "usd": 5.0,
        "trader": "test-wallet", "tok": 1.0, "sol": 0.01, "slot": 1,
    })


def test_diagnostics_explain_market_cap_rejection():
    tape = Tape("mint", 1_000)
    append_trade(tape, 1_035, 35, 0.000001)  # $1,000 market cap
    engine = CoinEngine(tape, Config())
    result = engine._diagnose(0)
    assert result["reasons"] == ["market_cap_below_min"]
    assert result["t_plus"] == 35


def test_diagnostics_stop_after_entry_window():
    tape = Tape("mint", 1_000)
    append_trade(tape, 1_301, 301, 0.00001)
    engine = CoinEngine(tape, Config())
    assert engine._diagnose(0) is None


def test_diagnostics_explain_open_position_block():
    tape = Tape("mint", 1_000)
    append_trade(tape, 1_035, 35, 0.00001)
    engine = CoinEngine(tape, Config())
    engine.pos = object()
    result = engine._diagnose(0)
    assert result["reasons"] == ["position_already_open"]
