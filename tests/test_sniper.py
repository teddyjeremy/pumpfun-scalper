from pumpbot.config import Config
from pumpbot.sniper import Sniper


MINT = "2u2py6mMFuMs9GdxuWMB24WSDrKS9HAwuc3GSvrTpump"
CREATOR = "D9gQ6RhKEpnobPBUdWY5bPQt2p3zGk3iVz6ChpUi2ArA"
BUYER = "8h2Q6RhKEpnobPBUdWY5bPQt2p3zGk3iVz6ChpUi2ArB"
BUYER2 = "9h2Q6RhKEpnobPBUdWY5bPQt2p3zGk3iVz6ChpUi2ArC"


def make_sniper(tmp_path):
    cfg = Config(mode="paper", data_dir=str(tmp_path))
    return Sniper(cfg)


def create_event():
    return ("create", {
        "mint": MINT, "timestamp": 1000, "creator": CREATOR,
        "symbol": "TEST", "name": "Test token", "quote_mint": "SOL",
    })


def trade_event(ts, *, buy=True, price_mult=1.0, user=BUYER):
    return ("trade", {
        "mint": MINT, "timestamp": ts, "creator": CREATOR, "user": user,
        "is_buy": buy, "quote_mint": "SOL", "sol_amount": 100_000_000,
        "token_amount": 1_000_000, "virtual_sol_reserves": int(40e9 * price_mult),
        "virtual_token_reserves": 800_000_000_000_000, "fee_bps": 100,
    })


def test_spot_sol_uses_virtual_curve_reserves():
    assert Sniper.spot_sol({"virtual_sol_reserves": 40_000_000_000,
                            "virtual_token_reserves": 800_000_000_000_000}) == 0.00000005


def test_first_buy_signal_then_next_transaction_fills_paper_entry(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)
    bot.on_events([trade_event(1001)], "first-buy-sig", 2, bot.started + 1)
    assert bot.stats["first_buy_signals"] == 1
    assert bot.position is None
    bot.on_events([trade_event(1002, buy=False)], "next-tx-sig", 3, bot.started + 2)
    assert bot.stats["entries_filled"] == 1
    assert bot.position["entry_signature"] == "next-tx-sig"
    assert bot.position["signal_signature"] == "first-buy-sig"


def test_creator_first_buy_is_not_sniped(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)
    bot.on_events([trade_event(1001, user=CREATOR)], "creator-buy", 2, bot.started + 1)
    assert bot.stats["first_buy_signals"] == 0
    assert bot.position is None


def test_position_exits_on_take_profit_and_accounts_for_costs(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)
    bot.on_events([trade_event(1001)], "first-buy-sig", 2, bot.started + 1)
    bot.on_events([trade_event(1002, buy=False)], "entry-fill-sig", 3, bot.started + 2)
    bot.on_events([trade_event(1003, price_mult=1.21)], "exit-sig", 4, bot.started + 3)
    assert bot.position is None
    assert bot.stats["exits"] == 1
    assert bot.stats["wins"] == 1
    rows = bot.j.read_kind("sniper_trades")
    assert rows[0]["exit_reason"] == "take_profit"
    assert rows[0]["net_pnl_usd"] > 0
    assert rows[0]["round_trip_gas_usd"] == 0.0285


def test_non_sol_launch_is_recorded_but_not_traded(tmp_path):
    bot = make_sniper(tmp_path)
    ev = dict(create_event()[1], quote_mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGkGZwyTDt1v")
    bot.on_events([("create", ev)], "create-sig", 1, bot.started)
    bot.on_events([trade_event(1001)], "first-buy-sig", 2, bot.started + 1)
    assert bot.stats["creates"] == 1
    assert bot.stats["sol_launches"] == 0
    assert bot.stats["first_buy_signals"] == 0
    launches = bot.j.read_kind("sniper_launches")
    assert launches[0]["decision"] == "observe_only_non_sol_quote"



def test_paper_stake_remains_fixed_when_accounting_wallet_is_below_five_dollars(tmp_path):
    bot = make_sniper(tmp_path)
    bot.wallet = 4.50  # Research accounting balance only; no real funds are at risk.
    bot.on_events([create_event()], "create-sig", 1, bot.started)
    bot._enter(
        bot.launches[MINT],
        trade_event(1002)[1],
        "entry-fill-sig",
        bot.spot_sol(trade_event(1002)[1]),
        2,
        1,
    )
    assert bot.position["stake_usd"] == 1.00
    assert bot.wallet == 4.50



def test_max_hold_timer_closes_baseline_without_another_trade(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)
    bot.on_events([trade_event(1001)], "first-buy-sig", 2, bot.started + 1)
    bot.on_events([trade_event(1002, buy=False)], "entry-fill-sig", 3, bot.started + 2)
    assert bot.position is not None
    bot.timer_tick(1033)
    assert bot.position is None
    rows = bot.j.read_kind("sniper_trades")
    assert rows[0]["exit_reason"] == "time_cap_timer"
    assert rows[0]["exit_signature"] == "timer"
    assert rows[0]["exit_price_stale_seconds"] == 31


def test_independent_shadow_entry_is_not_blocked_by_baseline_position(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)
    bot.on_events([trade_event(1001)], "first-buy-sig", 2, bot.started + 1)
    bot.on_events([trade_event(1002, buy=False)], "entry-fill-sig", 3, bot.started + 2)
    assert bot.position is not None

    second_mint = "SecondMint111111111111111111111111111111111"
    second_create = dict(create_event()[1], mint=second_mint, symbol="SECOND")
    bot.on_events([("create", second_create)], "second-create", 4, bot.started + 3)
    second_buy = dict(trade_event(1003)[1], mint=second_mint)
    bot.on_events([("trade", second_buy)], "second-first-buy", 5, bot.started + 4)
    second_follow = dict(trade_event(1004, buy=False)[1], mint=second_mint)
    bot.on_events([("trade", second_follow)], "second-follow-up", 6, bot.started + 5)

    assert bot.stats["entries_rejected_busy"] == 1
    assert bot.position["mint"] == MINT
    shadow_entries = bot.j.read_kind("sniper_shadow_entries")
    assert any(row["mint"] == second_mint and row["signal_wallet"] == BUYER for row in shadow_entries)
    assert second_mint in bot.shadow_positions


def test_shadow_timer_exit_records_stale_quote_age(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)
    bot.on_events([trade_event(1001)], "first-buy-sig", 2, bot.started + 1)
    bot.on_events([trade_event(1002, buy=False)], "entry-fill-sig", 3, bot.started + 2)
    bot.timer_tick(1033)
    rows = bot.j.read_kind("sniper_shadow_trades")
    assert len(rows) == 1
    assert rows[0]["exit_reason"] == "time_cap_timer"
    assert rows[0]["exit_price_stale_seconds"] == 31


def test_multi_buyer_cluster_lane_requires_two_buyers_in_signal_transaction(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)

    # Two distinct non-creator buyers are emitted under the same transaction signature.
    bot.on_events([trade_event(1001, user=BUYER)], "signal-tx", 2, bot.started + 1)
    bot.on_events([trade_event(1001, user=BUYER2)], "signal-tx", 2, bot.started + 1)
    assert MINT in bot.cluster_pending
    assert len(bot.cluster_pending[MINT]["signal_buyers"]) == 2
    assert MINT not in bot.cluster_positions

    # The next transaction is the simulated entry, as in the existing shadow model.
    bot.on_events([trade_event(1002, buy=False, user=BUYER2)], "next-tx", 3, bot.started + 2)
    assert MINT in bot.cluster_positions
    entry = bot.j.read_kind("sniper_cluster_entries")[0]
    assert entry["signal_transaction_buyer_count"] == 2
    assert entry["signal_transaction_buy_count"] == 2
    assert entry["signal_transaction_sell_count"] == 0

    # Cluster lane has its own timer-driven exit and does not change baseline logic.
    bot.timer_tick(1033)
    trades = bot.j.read_kind("sniper_cluster_trades")
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "time_cap_timer"
    assert trades[0]["exit_price_stale_seconds"] == 31


def test_multi_buyer_cluster_lane_rejects_single_buyer_signal_transaction(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)
    bot.on_events([trade_event(1001, user=BUYER)], "signal-tx", 2, bot.started + 1)
    bot.on_events([trade_event(1002, buy=False)], "next-tx", 3, bot.started + 2)

    assert MINT not in bot.cluster_positions
    decisions = bot.j.read_kind("sniper_cluster_decisions")
    assert decisions[-1]["decision"] == "rejected_single_buyer_signal_transaction"
    assert decisions[-1]["signal_transaction_buyer_count"] == 1
