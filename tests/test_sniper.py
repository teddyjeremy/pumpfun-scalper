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


def test_multi_buyer_one_position_portfolio_enforces_shared_slot_and_timer_exit(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)
    bot.on_events([trade_event(1001, user=BUYER)], "signal-tx", 2, bot.started + 1)
    bot.on_events([trade_event(1001, user=BUYER2)], "signal-tx", 2, bot.started + 1)
    bot.on_events([trade_event(1002, buy=False)], "entry-tx", 3, bot.started + 2)

    assert bot.cluster_portfolio_position is not None
    assert bot.cluster_portfolio_position["mint"] == MINT
    assert len(bot.j.read_kind("sniper_cluster_portfolio_entries")) == 1

    second_mint = "SecondMint111111111111111111111111111111111"
    second_create = dict(create_event()[1], mint=second_mint, symbol="SECOND")
    bot.on_events([("create", second_create)], "second-create", 4, bot.started + 3)
    bot.on_events([("trade", dict(trade_event(1003, user=BUYER)[1], mint=second_mint))],
                  "second-signal", 5, bot.started + 4)
    bot.on_events([("trade", dict(trade_event(1003, user=BUYER2)[1], mint=second_mint))],
                  "second-signal", 5, bot.started + 4)
    bot.on_events([("trade", dict(trade_event(1004, buy=False)[1], mint=second_mint))],
                  "second-entry", 6, bot.started + 5)

    decisions = bot.j.read_kind("sniper_cluster_portfolio_decisions")
    assert any(x["decision"] == "rejected_portfolio_busy" and x["mint"] == second_mint for x in decisions)
    assert bot.cluster_portfolio_position["mint"] == MINT

    bot.timer_tick(1033)
    assert bot.cluster_portfolio_position is None
    trades = bot.j.read_kind("sniper_cluster_portfolio_trades")
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "time_cap_timer"
    assert trades[0]["exit_price_stale_seconds"] == 31


def test_multi_buyer_one_position_portfolio_rejects_single_buyer_signal(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)
    bot.on_events([trade_event(1001, user=BUYER)], "signal-tx", 2, bot.started + 1)
    bot.on_events([trade_event(1002, buy=False)], "entry-tx", 3, bot.started + 2)

    assert bot.cluster_portfolio_position is None
    decisions = bot.j.read_kind("sniper_cluster_portfolio_decisions")
    assert decisions[-1]["decision"] == "rejected_single_buyer_signal_transaction"


def test_transaction_summary_uses_final_event_price_and_distinct_wallets(tmp_path):
    from pumpbot.transaction_intel import build_transaction_summaries

    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)
    events = [
        trade_event(1001, user=BUYER),
        trade_event(1001, user=BUYER2, price_mult=1.25),
    ]
    rows = build_transaction_summaries(events, bot.launches, "multi-buy-sig", 2)
    assert len(rows) == 1
    row = rows[0]
    assert row["buyer_count"] == 2
    assert row["buy_count"] == 2
    assert row["final_price_sol"] == bot.spot_sol(events[-1][1])
    assert row["intra_transaction_price_move"] == 0.25
    assert row["first_virtual_sol_reserves_raw"] == events[0][1]["virtual_sol_reserves"]
    assert row["final_virtual_sol_reserves_raw"] == events[-1][1]["virtual_sol_reserves"]


def test_transaction_complete_lane_fills_at_final_price_of_followup_transaction(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)

    signal_events = [trade_event(1001, user=BUYER), trade_event(1001, user=BUYER2, price_mult=1.10)]
    bot.on_events(signal_events, "signal-transaction", 2, bot.started + 1)
    assert bot.tx_lane.position is None
    pending = bot.tx_lane.pending[MINT]
    assert pending["signal_buyer_count"] == 2
    assert pending["signal_buy_sol"] == 0.2

    followup_events = [
        trade_event(1002, user=BUYER, price_mult=1.20),
        trade_event(1002, user=BUYER2, price_mult=1.30),
    ]
    bot.on_events(followup_events, "entry-transaction", 3, bot.started + 2)
    pos = bot.tx_lane.position
    assert pos is not None
    assert pos["entry_signature"] == "entry-transaction"
    assert pos["entry_price_sol"] == bot.spot_sol(followup_events[-1][1])
    assert pos["entry_event_count"] == 2
    assert pos["cohort_id"] == "|".join(sorted([BUYER, BUYER2]))


def test_transaction_complete_lane_rejects_creator_sell_signal(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)
    events = [
        trade_event(1001, user=BUYER),
        trade_event(1001, user=BUYER2),
        trade_event(1001, buy=False, user=CREATOR),
    ]
    bot.on_events(events, "creator-dump-signal", 2, bot.started + 1)
    assert MINT not in bot.tx_lane.pending
    decisions = bot.j.read_kind("sniper_tx_decisions")
    assert any(x["decision"] == "rejected_creator_selling_in_signal_transaction" for x in decisions)


def test_transaction_complete_lane_timer_exit_records_stale_price_age(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)
    bot.on_events([trade_event(1001, user=BUYER), trade_event(1001, user=BUYER2)],
                  "signal-transaction", 2, bot.started + 1)
    bot.on_events([trade_event(1002, user=BUYER), trade_event(1002, user=BUYER2, price_mult=1.05)],
                  "entry-transaction", 3, bot.started + 2)
    bot.tx_lane.timer_tick(1033)
    trades = bot.j.read_kind("sniper_tx_trades")
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "time_cap_timer"
    assert trades[0]["exit_price_stale_seconds"] == 31



def test_high_conviction_lane_requires_signal_confirmation_then_later_execution(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)

    signal = [
        dict(trade_event(1001, user=BUYER)[1], sol_amount=300_000_000),
        dict(trade_event(1001, user=BUYER2)[1], sol_amount=300_000_000),
    ]
    bot.on_events([("trade", x) for x in signal], "v2-signal", 2, bot.started + 1)
    assert bot.v2_lane.position is None
    assert bot.v2_lane.pending[MINT]["stage"] == "signal"

    confirmation = [
        dict(trade_event(1002, user=BUYER, price_mult=1.0)[1], sol_amount=300_000_000),
        dict(trade_event(1002, user=BUYER2, price_mult=1.10)[1], sol_amount=300_000_000),
    ]
    bot.on_events([("trade", x) for x in confirmation], "v2-confirmation", 3, bot.started + 2)
    assert bot.v2_lane.position is None
    assert bot.v2_lane.pending[MINT]["stage"] == "confirmed"

    execution = [trade_event(1003, user=BUYER, price_mult=1.20)]
    bot.on_events(execution, "v2-execution", 4, bot.started + 3)
    pos = bot.v2_lane.position
    assert pos is not None
    assert pos["entry_signature"] == "v2-execution"
    assert pos["signal_signature"] == "v2-signal"
    assert pos["confirmation_signature"] == "v2-confirmation"
    assert pos["entry_sequence"] > pos["confirmation_sequence"] > pos["signal_sequence"]
    assert pos["entry_price_sol"] == bot.spot_sol(execution[0][1])


def test_high_conviction_lane_rejects_weak_confirmation_momentum(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)
    bot.on_events([
        trade_event(1001, user=BUYER),
        trade_event(1001, user=BUYER2),
    ], "v2-signal", 2, bot.started + 1)
    bot.on_events([
        trade_event(1002, user=BUYER, price_mult=1.0),
        trade_event(1002, user=BUYER2, price_mult=1.01),
    ], "v2-weak-confirmation", 3, bot.started + 2)

    assert MINT not in bot.v2_lane.pending
    assert bot.v2_lane.position is None
    decisions = bot.j.read_kind("sniper_v2_decisions")
    rejected = [x for x in decisions if x["decision"] == "rejected_confirmation_quality"]
    assert len(rejected) == 1
    assert "confirmation_momentum_too_weak" in rejected[0]["reasons"]


def test_high_conviction_lane_uses_conservative_entry_and_exit_marks(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)
    bot.on_events([
        ("trade", dict(trade_event(1001, user=BUYER)[1], sol_amount=300_000_000)),
        ("trade", dict(trade_event(1001, user=BUYER2)[1], sol_amount=300_000_000)),
    ], "v2-signal", 2, bot.started + 1)
    bot.on_events([
        ("trade", dict(trade_event(1002, user=BUYER, price_mult=1.0)[1], sol_amount=300_000_000)),
        ("trade", dict(trade_event(1002, user=BUYER2, price_mult=1.10)[1], sol_amount=300_000_000)),
    ], "v2-confirmation", 3, bot.started + 2)
    entry_events = [
        trade_event(1003, user=BUYER, price_mult=1.20),
        trade_event(1003, user=BUYER2, price_mult=1.30),
    ]
    bot.on_events(entry_events, "v2-execution", 4, bot.started + 3)
    expected_entry = max(bot.spot_sol(x[1]) for x in entry_events)
    assert bot.v2_lane.position["entry_price_sol"] == expected_entry

    exit_events = [
        trade_event(1004, buy=False, user=BUYER, price_mult=1.70),
        trade_event(1004, buy=False, user=BUYER2, price_mult=1.60),
    ]
    bot.on_events(exit_events, "v2-exit", 5, bot.started + 4)
    trades = bot.j.read_kind("sniper_v2_trades")
    expected_exit = min(bot.spot_sol(x[1]) for x in exit_events)
    assert len(trades) == 1
    assert trades[0]["exit_price_sol"] == expected_exit
    assert trades[0]["exit_price_basis"] == "min_first_and_final_event_price"


def test_high_conviction_lane_requires_positive_flow_on_execution_transaction(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)
    bot.on_events([trade_event(1001, user=BUYER), trade_event(1001, user=BUYER2)],
                  "v2-signal", 2, bot.started + 1)
    bot.on_events([
        ("trade", dict(trade_event(1002, user=BUYER, price_mult=1.0)[1], sol_amount=300_000_000)),
        ("trade", dict(trade_event(1002, user=BUYER2, price_mult=1.10)[1], sol_amount=300_000_000)),
    ], "v2-confirmation", 3, bot.started + 2)
    bot.on_events([trade_event(1003, buy=False, user=BUYER)], "v2-execution-sell", 4, bot.started + 3)
    assert bot.v2_lane.position is None
    decisions = bot.j.read_kind("sniper_v2_decisions")
    assert any(x["decision"] == "rejected_execution_flow" for x in decisions)


def test_high_conviction_lane_timer_exit_records_stale_quote_age(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)
    bot.on_events([
        ("trade", dict(trade_event(1001, user=BUYER)[1], sol_amount=300_000_000)),
        ("trade", dict(trade_event(1001, user=BUYER2)[1], sol_amount=300_000_000)),
    ], "v2-signal", 2, bot.started + 1)
    bot.on_events([
        ("trade", dict(trade_event(1002, user=BUYER, price_mult=1.0)[1], sol_amount=300_000_000)),
        ("trade", dict(trade_event(1002, user=BUYER2, price_mult=1.10)[1], sol_amount=300_000_000)),
    ], "v2-confirmation", 3, bot.started + 2)
    bot.on_events([trade_event(1003, user=BUYER, price_mult=1.20)], "v2-execution", 4, bot.started + 3)
    bot.v2_lane.timer_tick(1033)
    trades = bot.j.read_kind("sniper_v2_trades")
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "time_cap_timer"
    assert trades[0]["exit_price_stale_seconds"] == 30



def test_transaction_capture_records_feed_latency_and_observation_sequence(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)
    bot.on_events([
        ("trade", trade_event(1001, user=BUYER)[1]),
        ("trade", trade_event(1001, user=BUYER2)[1]),
    ], "latency-sig", 2, 1001.375)

    rows = bot.j.read_kind("sniper_transactions")
    assert len(rows) == 1
    assert rows[0]["feed_latency_seconds"] == 0.375
    assert rows[0]["observed_at_epoch"] == 1001.375
    assert rows[0]["observation_sequence"] > 0



def test_v3_quality_candidate_rejects_entry_after_ten_seconds_without_changing_v2(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)

    signal = [
        dict(trade_event(1008, user=BUYER)[1], sol_amount=300_000_000),
        dict(trade_event(1008, user=BUYER2)[1], sol_amount=300_000_000),
    ]
    bot.on_events([("trade", x) for x in signal], "v3-age-signal", 2, bot.started + 1)

    confirmation = [
        dict(trade_event(1009, user=BUYER, price_mult=1.0)[1], sol_amount=300_000_000),
        dict(trade_event(1009, user=BUYER2, price_mult=1.10)[1], sol_amount=300_000_000),
    ]
    bot.on_events([("trade", x) for x in confirmation], "v3-age-confirmation", 3, bot.started + 2)

    bot.on_events([trade_event(1011, user=BUYER, price_mult=1.20)],
                  "v3-age-execution", 4, bot.started + 3)

    assert bot.v2_lane.position is not None
    assert bot.v3_lane.position is None
    decisions = bot.j.read_kind("sniper_v3_decisions")
    assert any(x["decision"] == "rejected_entry_age" and x["entry_t_plus"] == 11 for x in decisions)
    assert bot.v2_lane.take_profit == 0.20
    assert bot.v3_lane.take_profit == 0.25


def test_v3_quality_candidate_allows_only_one_entry_per_mint(tmp_path):
    bot = make_sniper(tmp_path)
    bot.on_events([create_event()], "create-sig", 1, bot.started)

    def signal_and_confirm(signal_ts, confirm_ts, prefix):
        signal = [
            dict(trade_event(signal_ts, user=BUYER)[1], sol_amount=300_000_000),
            dict(trade_event(signal_ts, user=BUYER2)[1], sol_amount=300_000_000),
        ]
        bot.on_events([("trade", x) for x in signal], f"{prefix}-signal", 2, bot.started + 1)
        confirmation = [
            dict(trade_event(confirm_ts, user=BUYER, price_mult=1.0)[1], sol_amount=300_000_000),
            dict(trade_event(confirm_ts, user=BUYER2, price_mult=1.10)[1], sol_amount=300_000_000),
        ]
        bot.on_events([("trade", x) for x in confirmation], f"{prefix}-confirmation", 3, bot.started + 2)

    signal_and_confirm(1001, 1002, "first")
    bot.on_events([trade_event(1003, user=BUYER, price_mult=1.20)], "first-execution", 4, bot.started + 3)
    assert bot.v3_lane.position is not None

    bot.on_events([trade_event(1004, buy=False, user=BUYER, price_mult=1.60)],
                  "first-take-profit", 5, bot.started + 4)
    assert bot.v3_lane.position is None
    assert len(bot.j.read_kind("sniper_v3_trades")) == 1

    signal_and_confirm(1005, 1006, "second")
    bot.on_events([trade_event(1007, user=BUYER, price_mult=1.20)], "second-execution", 6, bot.started + 7)

    assert bot.v3_lane.position is None
    decisions = bot.j.read_kind("sniper_v3_decisions")
    assert any(x["decision"] == "rejected_mint_already_traded" for x in decisions)
