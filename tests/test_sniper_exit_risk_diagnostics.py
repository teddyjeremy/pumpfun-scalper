import json

from scripts.sniper_exit_risk_diagnostics import _analyze_trade, build_report


def event(signature, slot, side, price, reserve, amount, trader="buyer", creator="creator", ts=100):
    return {
        "signature": signature, "slot": slot, "side": side, "price_sol": price,
        "virtual_sol_reserves_raw": reserve, "virtual_token_reserves_raw": 10**15,
        "sol_amount_raw": amount, "quote_mint": "SOL", "mint": "mint",
        "trader": trader, "creator": creator, "block_timestamp": ts,
    }


def test_sell_warning_excludes_original_trigger_and_marks_next_later_slot():
    tape = [
        event("entry", 10, "buy", 1.0, 30_000_000_000, 100_000_000),
        event("same-slot-sell", 10, "sell", 0.98, 30_000_000_000, 900_000_000),
        event("warning-sell", 11, "sell", 0.90, 29_000_000_000, 1_500_000_000),
        event("same-slot-follow", 11, "buy", 0.89, 27_500_000_000, 100_000_000),
        event("later-mark", 12, "buy", 0.88, 27_600_000_000, 100_000_000),
        event("exit-trigger", 13, "sell", 0.80, 27_700_000_000, 1_000_000_000),
    ]
    trade = {
        "mint": "mint", "symbol": "TEST", "entry_signature": "entry",
        "exit_trigger_signature": "exit-trigger", "entry_slot": 10,
        "exit_trigger_slot": 13, "entry_price_sol": 1.0,
        "exit_reason": "stop_loss", "net_pnl_usd": -0.2,
        "exit_observed_at_epoch": 115,
    }
    result = _analyze_trade(trade, tape, {row["signature"]: i for i, row in enumerate(tape)})
    assert result["status"] == "ok"
    assert len(result["sell_events"]) == 2
    warning = result["sell_events"][1]
    assert warning["signature"] == "warning-sell"
    assert warning["sell_vs_pre_event_virtual_sol_reserve_pct"] == 1_500_000_000 / 30_000_000_000 * 100
    assert warning["next_later_slot_signature"] == "later-mark"
    assert warning["next_later_slot_move_from_entry_pct"] == -12.0
    assert all(row["signature"] != "exit-trigger" for row in result["sell_events"])


def test_build_report_handles_overlapping_lanes_and_missing_files(tmp_path):
    data = tmp_path / "data"
    out = tmp_path / "out"
    data.mkdir()
    tape = [
        event("entry", 10, "buy", 1.0, 30_000_000_000, 100_000_000),
        event("later", 11, "buy", 1.01, 30_100_000_000, 100_000_000),
        event("trigger", 12, "sell", 0.9, 30_200_000_000, 100_000_000),
    ]
    (data / "sniper_tape_sniper.jsonl").write_text("".join(json.dumps(x) + "\n" for x in tape))
    trade = {
        "mint": "mint", "symbol": "TEST", "entry_signature": "entry",
        "exit_trigger_signature": "trigger", "entry_slot": 10,
        "exit_trigger_slot": 12, "entry_price_sol": 1.0,
        "exit_reason": "stop_loss", "net_pnl_usd": -0.1,
    }
    for lane in ("0500ms", "1000ms"):
        (data / f"sniper_latency_{lane}_trades_sniper.jsonl").write_text(json.dumps(trade) + "\n")
    report = build_report(str(data), str(out))
    assert report["unique_entry_opportunities_across_lanes"] == 1
    assert report["lanes"]["500ms"]["matched_trade_paths"] == 1
    assert report["lanes"]["1000ms"]["matched_trade_paths"] == 1
    assert "1500ms" not in report["lanes"]
    assert (out / "sniper_exit_risk_diagnostics.json").exists()
    assert (out / "sniper_exit_risk_diagnostics.md").exists()
