from pumpbot.journal import Journal
from pumpbot.latency_research import LatencyAwareWalletGuard


MINT = "Mint111111111111111111111111111111111111"
BUYER_A = "BuyerA111111111111111111111111111111111"
BUYER_B = "BuyerB111111111111111111111111111111111"


def make_lane(tmp_path, latency=0.5):
    return LatencyAwareWalletGuard(
        Journal(str(tmp_path), "sniper"),
        latency_seconds=latency, stake_usd=1.0,
        take_profit=0.20, stop_loss=0.12, max_hold_seconds=30,
        confirm_seconds=3.0, entry_slippage=0.015, exit_slippage=0.015,
        fee_rate=0.0125, gas_usd=0.0285,
    )


def tx(*, signature, slot, observed, price=1.0, mint=MINT, buyers=None,
       timestamp=1000, net=0.2, creator_buy=False, creator_sell=False):
    buyers = [BUYER_A, BUYER_B] if buyers is None else buyers
    return {
        "mint": mint, "symbol": "TEST", "creator": "Creator",
        "signature": signature, "slot": slot, "timestamp": timestamp,
        "t_plus": timestamp - 1000, "observed_at_epoch": observed,
        "final_price_sol": price, "buyer_wallets": buyers,
        "first_buyer_wallet": buyers[0] if buyers else None,
        "buyer_count": len(buyers), "net_buy_sol": net,
        "buy_sol": max(net, 0), "sell_sol": max(-net, 0),
        "creator_buy": creator_buy, "creator_sell": creator_sell,
    }


def test_entry_requires_latency_and_later_slot(tmp_path):
    lane = make_lane(tmp_path, latency=0.5)
    lane.process(tx(signature="signal", slot=10, observed=100.0))
    # A same-slot transaction cannot be treated as an executable entry.
    lane.process(tx(signature="same-slot", slot=10, observed=101.0))
    assert lane.position is None
    lane.process(tx(signature="later-slot", slot=11, observed=101.0))
    assert lane.position is not None
    assert lane.position["entry_signature"] == "later-slot"
    assert lane.position["entry_latency_seconds"] == 1.0


def test_exit_waits_for_delay_and_later_slot_then_marks_actual_later_price(tmp_path):
    lane = make_lane(tmp_path, latency=0.5)
    lane.process(tx(signature="signal", slot=10, observed=100.0))
    lane.process(tx(signature="entry", slot=11, observed=100.6, price=1.0))
    lane.process(tx(signature="tp-trigger", slot=12, observed=101.0, price=1.25))
    assert lane.position is not None
    # Delay elapsed, but still the trigger slot: must not close.
    lane.process(tx(signature="same-slot-after-delay", slot=12, observed=102.0, price=1.30))
    assert lane.position is not None
    # The next slot is eligible; use its price, not the trigger mark.
    lane.process(tx(signature="later-exit", slot=13, observed=102.0, price=1.10))
    assert lane.position is None
    trades = lane.j.read_kind(f"{lane.prefix}_trades")
    assert len(trades) == 1
    assert trades[0]["exit_signature"] == "later-exit"
    assert trades[0]["exit_price_sol"] == 1.10
    assert trades[0]["exit_trigger_price_sol"] == 1.25
    assert trades[0]["exit_latency_slots"] == 1


def test_wallet_is_locked_only_after_delayed_trade_closes_at_a_loss(tmp_path):
    lane = make_lane(tmp_path, latency=0.5)
    lane.process(tx(signature="signal", slot=10, observed=100.0))
    lane.process(tx(signature="entry", slot=11, observed=100.6, price=1.0))
    lane.process(tx(signature="sl-trigger", slot=12, observed=101.0, price=0.80))
    lane.process(tx(signature="later-exit", slot=13, observed=101.6, price=0.80))
    assert BUYER_A in lane.locked_wallets
    decisions = lane.j.read_kind(f"{lane.prefix}_decisions")
    assert any(row["decision"] == "wallet_locked_after_loss" for row in decisions)


def test_time_cap_does_not_close_using_stale_quote(tmp_path):
    lane = make_lane(tmp_path, latency=0.5)
    lane.process(tx(signature="signal", slot=10, observed=100.0))
    lane.process(tx(signature="entry", slot=11, observed=100.6, price=1.0))
    lane.timer_tick(131.0)
    assert lane.position is not None
    assert lane.position["pending_exit"]["reason"] == "time_cap"
    assert lane.j.read_kind(f"{lane.prefix}_trades") == []
