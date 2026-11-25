import os

from pumpbot.config import Config, redact


def test_default_is_paper_and_high_frequency_lanes(monkeypatch):
    monkeypatch.delenv("MODE", raising=False)
    monkeypatch.delenv("LANES", raising=False)
    c = Config.from_env()
    assert c.mode == "paper" and c.lanes == ["K", "B"] and c.validate() == []


def test_lane_k_thresholds_are_configurable(monkeypatch):
    monkeypatch.setenv("K_BUY_SELL_RATIO", "1.25")
    monkeypatch.setenv("K_MIN_TRADES_10", "5")
    monkeypatch.setenv("K_MIN_BUY_USD_10", "40")
    c = Config.from_env()
    assert (c.k_buy_sell_ratio, c.k_min_trades_10, c.k_min_buy_usd_10) == (1.25, 5, 40.0)


def test_live_needs_all_gates(monkeypatch):
    monkeypatch.setenv("MODE", "live")
    monkeypatch.delenv("I_UNDERSTAND_RISK", raising=False)
    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    errs = Config.from_env().validate()
    assert any("I_UNDERSTAND_RISK" in e for e in errs) and any("WALLET_PRIVATE_KEY" in e for e in errs)


def test_hard_clamps(monkeypatch):
    monkeypatch.setenv("MAX_HOLD_S", "999"); monkeypatch.setenv("TP_MULT", "1.1")
    monkeypatch.setenv("MAX_STAKE_USD", "500"); monkeypatch.setenv("MAX_TRADES_PER_DAY", "400")
    c = Config.from_env()
    assert c.max_hold_s == 120 and c.tp_mult == 1.5 and c.max_stake_usd == 20 and c.max_trades_per_day == 50


def test_redact_hides_keys(monkeypatch):
    monkeypatch.setenv("RPC_HTTP_URL", "https://example-rpc.invalid/abcdefghijklmnop")
    fake = "wss://rpc.invalid/?api-" + "key=" + "SECRETSECRET"
    s = redact(fake + " and https://example-rpc.invalid/abcdefghijklmnop")
    assert "SECRETSECRET" not in s and "abcdefghijklmnop" not in s
