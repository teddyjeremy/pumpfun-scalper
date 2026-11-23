from pumpbot.config import Config
from pumpbot.risk import RiskManager, pnl_for_stake


def rm(**kw):
    c = Config()
    for k, v in kw.items():
        setattr(c, k, v)
    return RiskManager(c)


def test_first_stake_is_1_and_capped_by_wallet_share():
    r = rm()
    ok, why, s = r.allow("B", "d1")
    assert ok and s == 1.0          # 35% of $2 = 0.70 < $1 floor -> $1


def test_one_position_at_a_time():
    r = rm(); r.allow("B", "d"); r.on_open()
    ok, why, _ = r.allow("B", "d")
    assert not ok and why == "one_position_at_a_time"


def test_ladder_grows_20pct_and_caps_at_20():
    r = rm()
    r.wallet = 2.0 * 1.2 ** 2 + 1e-9                  # two ladder steps up
    s = r.stake_for_next()
    assert abs(r.stake - 1.44) < 1e-6                 # ladder stake
    assert abs(s - max(1.0, 0.35 * r.wallet)) < 1e-6  # but never more than 35% of the wallet (floor $1)
    r.wallet = 10_000
    assert r.stake_for_next() == 20.0


def test_daily_loss_cap():
    r = rm(start_wallet_usd=10.0)
    r.new_day("d"); r.on_open(); r.on_close("B", -2.6, -0.26)
    ok, why, _ = r.allow("B", "d")
    assert not ok and why == "daily_loss_cap"
    ok, _, _ = r.allow("B", "d2")   # new day resets
    assert ok


def test_three_losses_skip_five():
    r = rm(start_wallet_usd=100.0, daily_loss_cap=0.9)
    r.new_day("d")
    for _ in range(3):
        r.on_open(); r.on_close("B", -0.05, -0.05)
    reasons = [r.allow("B", "d")[1] for _ in range(6)]
    assert reasons[:5] == ["cooldown_after_loss_streak"] * 5 and reasons[5] == "ok"


def test_kill_switch_below_1_20():
    r = rm()
    r.on_open(); r.on_close("B", -0.85, -0.85)
    ok, why, _ = r.allow("B", "d")
    assert not ok and why.startswith("kill_switch") and r.killed


def test_max_trades_per_day():
    r = rm(start_wallet_usd=1000.0)
    r.new_day("d")
    for _ in range(50):
        ok, _, _ = r.allow("B", "d"); assert ok
        r.on_open(); r.on_close("B", 0.01, 0.01)
    ok, why, _ = r.allow("B", "d")
    assert not ok and why == "daily_trade_cap"


def test_lane_disabled_when_last_100_negative():
    r = rm(start_wallet_usd=1e6, daily_loss_cap=0.99)
    for _ in range(100):
        r.lane_hist["B"].append(-0.01)
    ok, why, _ = r.allow("B", "d")
    assert not ok and "lane_B_disabled" in why


def test_pnl_scaling_gas_flat():
    assert abs(pnl_for_stake(-0.0285, 5.0) - (-0.0285)) < 1e-12
