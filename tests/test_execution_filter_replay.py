from pumpbot.execution_filter_replay import replay


def tx(mint, sig, ts, seq, *, buyers=2, buy_sol=0.6, net=0.6,
       move=0.10, buy_count=2, creator_sell=False, price=1.0):
    return {
        "mint": mint, "symbol": mint, "signature": sig, "timestamp": ts,
        "observation_sequence": seq, "buyer_count": buyers,
        "buy_sol": buy_sol, "net_buy_sol": net, "intra_transaction_price_move": move,
        "buy_count": buy_count, "creator_sell": creator_sell,
        "first_event_price_sol": price, "final_price_sol": price,
        "event_count": max(buyers, 1), "sell_count": 0, "seller_count": 0,
    }


def test_execution_filter_variants_replay_same_signal_confirmation_and_exit():
    mint = "mintA"
    rows = [
        tx(mint, "signal", 100, 1, buy_sol=0.8, price=1.0),
        tx(mint, "confirm", 101, 2, buy_sol=0.8, move=0.10, price=1.1),
        tx(mint, "entry", 102, 3, buyers=2, buy_sol=1.2, price=1.2),
        tx(mint, "exit", 104, 4, buyers=0, buy_sol=0, net=-1, buy_count=0,
           move=0, price=1.5),
    ]
    result = replay(rows)
    variants = {x["variant"]: x for x in result["variants"]}
    assert variants["control_existing_execution"]["entries"] == 1
    assert variants["execution_buy_0_5_sol"]["entries"] == 1
    assert variants["execution_buy_1_sol"]["entries"] == 1
    assert variants["execution_buy_2_sol"]["entries"] == 0
    assert variants["execution_buy_0_5_sol_and_2_buyers"]["entries"] == 1
    assert variants["execution_buy_1_sol"]["closed_trades"] == 1


def test_execution_filter_requires_execution_transaction_buyer_count():
    mint = "mintB"
    rows = [
        tx(mint, "signal", 100, 1, buy_sol=0.8),
        tx(mint, "confirm", 101, 2, buy_sol=0.8, move=0.10),
        tx(mint, "entry", 102, 3, buyers=1, buy_sol=0.8),
    ]
    result = replay(rows)
    variants = {x["variant"]: x for x in result["variants"]}
    assert variants["execution_buy_0_5_sol"]["entries"] == 1
    assert variants["execution_buy_0_5_sol_and_2_buyers"]["entries"] == 0
    assert variants["execution_buy_0_5_sol_and_2_buyers"]["decisions"]["rejected_execution_buyer_count"] == 1


def test_execution_filter_preserves_full_transaction_stream_and_timer_exit():
    mint = "mintC"
    rows = [
        tx(mint, "signal", 100, 1, buy_sol=0.8),
        tx(mint, "confirm", 101, 2, buy_sol=0.8, move=0.10),
        tx(mint, "entry", 102, 3, buyers=2, buy_sol=0.8),
    ]
    result = replay(rows)
    lane = next(x for x in result["variants"] if x["variant"] == "execution_buy_0_5_sol")
    assert lane["closed_trades"] == 1
    assert lane["exit_reasons"] == {"time_cap_timer": 1}
    assert lane["trades_detail"][0]["exit_price_stale_seconds"] == 30
