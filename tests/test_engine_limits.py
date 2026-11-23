"""Hard limits: no entry before t+30s, positions always closed by 120s, TP/inval/sell-surge exits."""
from conftest import synth_trades
from pumpbot.config import Config
from pumpbot.engine import CoinEngine, HARD_MAX_HOLD_S
from pumpbot.tape import Tape


def run(trades, cfg, lanes=("B",)):
    tp = Tape("M", trades[0]["bt"], "CRE")
    eng = CoinEngine(tp, cfg, lanes=list(lanes))
    for x in trades:
        eng.on_trade(x)
    eng.finish()
    return eng.closed


def cfg_loose(**kw):
    c = Config()
    c.min_cum_trades = 0
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_never_enters_before_t30_even_if_configured():
    c = cfg_loose(entry_min_t=0)
    closed = run(synth_trades(n_sec=400), c)
    assert closed, "synthetic steady tape should trigger Lane B"
    assert all(r["entry_t"] > 30 for r in closed)
    assert all(r["entry_t"] <= 300 for r in closed)


def test_hold_never_exceeds_120_even_if_configured_longer():
    c = cfg_loose(max_hold_s=10_000, tp_mult=50.0)   # flat-ish tape, TP unreachable
    closed = run(synth_trades(n_sec=600), c)
    assert closed
    assert all(r["hold_s"] <= HARD_MAX_HOLD_S + 3 for r in closed)   # +fill delay window
    assert any(r["exit"] == "time_cap" for r in closed)


def test_take_profit_hits():
    c = cfg_loose()
    trades = synth_trades(n_sec=300, pattern=lambda s: 1.0 + (0.0 if s < 60 else 0.02 * (s - 60)))
    closed = run(trades, c)
    assert closed and closed[0]["exit"] == "tp" and closed[0]["mult"] >= 1.5


def test_invalidation_on_swing_low_break():
    c = cfg_loose()
    trades = synth_trades(n_sec=300, pattern=lambda s: 1.0 if s < 45 else 0.7)
    closed = run(trades, c)
    assert closed and closed[0]["exit"] in ("inval_low", "sell_surge")
    assert closed[0]["net"] < 0


def test_mcap_band_respected():
    c = cfg_loose()
    trades = synth_trades(n_sec=300, start_spot=2e-5)   # $20k mcap
    assert run(trades, c) == []


def test_max_two_entries_per_coin():
    c = cfg_loose(max_hold_s=10, tp_mult=50.0)
    closed = run(synth_trades(n_sec=300), c)
    assert len(closed) <= 2
