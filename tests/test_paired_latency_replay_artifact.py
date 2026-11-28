import json

from scripts.paired_latency_replay import build_report


def tx(mint, signature, slot, observed, price, *, sequence, first=None):
    buyers = ["WalletA", "WalletB"]
    return {
        "mint": mint,
        "symbol": mint,
        "signature": signature,
        "slot": slot,
        "observed_at_epoch": observed,
        "final_price_sol": price,
        "buyer_wallets": buyers,
        "first_buyer_wallet": first or buyers[0],
        "buyer_count": len(buyers),
        "buy_count": 1,
        "buy_sol": 0.2,
        "sell_sol": 0.0,
        "net_buy_sol": 0.2,
        "creator_sell": False,
        "observation_sequence": sequence,
    }


def test_build_report_persists_paired_trade_signatures(tmp_path):
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "report"
    data_dir.mkdir()
    rows = [
        tx("MintA", "signal-a", 1, 100.0, 1.0, sequence=1),
        tx("MintA", "entry-a", 2, 100.6, 1.0, sequence=2),
        tx("MintA", "stop-a", 3, 101.0, 0.8, sequence=3),
        tx("MintA", "exit-a", 4, 101.6, 0.8, sequence=4),
    ]
    source = data_dir / "sniper_transactions_sniper.jsonl"
    source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    build_report(str(data_dir), str(out_dir))

    report = json.loads((out_dir / "paired_latency_replay.json").read_text(encoding="utf-8"))
    trades = report["trade_details"]["latency_500ms_wallet_lock"]["trades"]
    assert len(trades) == 1
    assert trades[0]["entry_signature"] == "entry-a"
    assert trades[0]["exit_trigger_signature"] == "stop-a"
    assert trades[0]["exit_signature"] == "exit-a"
