from scripts.wallet_sequential_shadow import wallet_gate, Portfolio


def test_wallet_gate_requires_known_positive_net_buying():
    assert not wallet_gate(None)
    assert not wallet_gate({"positive_wallets_30s": "0", "positive_wallet_net_usd_30s": "10", "negative_wallet_net_usd_30s": "0"})
    assert not wallet_gate({"positive_wallets_30s": "1", "positive_wallet_net_usd_30s": "2", "negative_wallet_net_usd_30s": "3"})
    assert wallet_gate({"positive_wallets_30s": "1", "positive_wallet_net_usd_30s": "2", "negative_wallet_net_usd_30s": "1"})


def test_portfolio_gate_reserves_one_position_until_exit():
    p = Portfolio("portfolio_baseline")
    gate_a = p.gate_for("mint-a", {})
    gate_b = p.gate_for("mint-b", {})
    assert gate_a({"lane": "K", "t": 50})[0]
    assert not gate_b({"lane": "K", "t": 60})[0]
    p.consume("mint-a", [("exited", {
        "lane": "K", "entry_t": 50, "exit_bt": 200, "hold_s": 150,
        "exit": "time_cap", "mult": 1, "net": -0.1, "mfe": 0, "mae": -0.1
    })])
    assert gate_b({"lane": "K", "t": 210})[0]
