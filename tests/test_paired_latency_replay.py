from scripts.paired_latency_replay import replay_lane


def row(mint, signature, slot, observed, price, buyers=None, first=None,
        buy_count=1, net=0.2, creator_sell=False):
    buyers = ["WalletA", "WalletB"] if buyers is None else buyers
    return {
        "mint": mint, "symbol": mint, "signature": signature, "slot": slot,
        "timestamp": int(observed), "observed_at_epoch": observed,
        "final_price_sol": price, "buyer_wallets": buyers,
        "first_buyer_wallet": first or (buyers[0] if buyers else None),
        "buyer_count": len(buyers), "buy_count": buy_count,
        "buy_sol": max(net, 0), "sell_sol": max(-net, 0),
        "net_buy_sol": net, "creator_sell": creator_sell,
    }


def test_wallet_lock_isolated_against_unlocked_control():
    mint_a, mint_b = "MintA", "MintB"
    rows = [
        row(mint_a, "signal-a", 1, 100.0, 1.0, first="WalletA"),
        row(mint_a, "entry-a", 2, 100.6, 1.0, buyers=["Other1"]),
        row(mint_a, "stop-a", 3, 101.0, 0.80, buyers=["Other2"]),
        row(mint_a, "exit-a", 4, 101.6, 0.80, buyers=["Other3"]),
        row(mint_b, "signal-b", 5, 102.0, 1.0, first="WalletA"),
        row(mint_b, "entry-b", 6, 102.6, 1.0, buyers=["Other4"]),
    ]
    unlocked = replay_lane(rows, latency_seconds=0.5, wallet_loss_guard=False,
                           positive_entry_flow=False)
    guarded = replay_lane(rows, latency_seconds=0.5, wallet_loss_guard=True,
                          positive_entry_flow=False)
    assert len(unlocked["entries"]) == 2
    assert len(guarded["entries"]) == 1
    assert "WalletA" in guarded["locked_wallets"]


def test_positive_flow_gate_waits_for_later_qualifying_transaction():
    mint = "FlowMint"
    rows = [
        row(mint, "signal", 10, 100.0, 1.0),
        row(mint, "sell-pressure", 11, 100.6, 1.02,
            buyers=["Seller"], buy_count=0, net=-0.4),
        row(mint, "buy-pressure", 12, 101.0, 1.04,
            buyers=["Buyer"], buy_count=1, net=0.3),
    ]
    control = replay_lane(rows, latency_seconds=0.5, wallet_loss_guard=False,
                          positive_entry_flow=False)
    gated = replay_lane(rows, latency_seconds=0.5, wallet_loss_guard=False,
                        positive_entry_flow=True)
    assert control["entries"][0]["entry_signature"] == "sell-pressure"
    assert gated["entries"][0]["entry_signature"] == "buy-pressure"
    assert gated["entries"][0]["entry_net_buy_sol"] > 0


def test_exit_uses_later_slot_after_delay_and_keeps_unclosed_position():
    mint = "ExitMint"
    rows = [
        row(mint, "signal", 20, 100.0, 1.0),
        row(mint, "entry", 21, 100.6, 1.0, buyers=["EntryBuyer"]),
        row(mint, "trigger", 22, 101.0, 1.25, buyers=["TriggerBuyer"]),
        row(mint, "same-slot", 22, 102.0, 1.30, buyers=["SameSlotBuyer"]),
        row(mint, "delayed-exit", 23, 102.0, 1.10, buyers=["ExitBuyer"]),
    ]
    result = replay_lane(rows, latency_seconds=0.5, wallet_loss_guard=False,
                         positive_entry_flow=False)
    assert result["open_position"] is None
    trade = result["trades"][0]
    assert trade["exit_signature"] == "delayed-exit"
    assert trade["exit_price_sol"] == 1.10
    assert trade["exit_trigger_price_sol"] == 1.25
    assert trade["exit_latency_slots"] == 1



def test_single_buyer_candidate_is_rejected_before_later_multi_buyer_signal():
    mint = "SignalMint"
    rows = [
        row(mint, "single-buyer-signal", 1, 100.0, 1.0, buyers=["OnlyOne"]),
        row(mint, "first-followup", 2, 100.6, 1.0, buyers=["BuyerX", "BuyerY"]),
        row(mint, "qualified-signal", 3, 101.0, 1.0, buyers=["BuyerA", "BuyerB"]),
        row(mint, "entry", 4, 101.6, 1.0, buyers=["EntryBuyer"]),
    ]
    result = replay_lane(rows, latency_seconds=0.5, wallet_loss_guard=False,
                         positive_entry_flow=False)
    assert len(result["entries"]) == 1
    assert result["entries"][0]["signal_signature"] == "qualified-signal"
    assert result["entries"][0]["entry_signature"] == "entry"
