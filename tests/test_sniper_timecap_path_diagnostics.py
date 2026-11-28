import json

from scripts.sniper_timecap_path_diagnostics import _analyze_trade, build_report


def event(signature, slot, price, t_plus, timestamp):
    return {
        "signature": signature, "slot": slot, "price_sol": price,
        "t_plus": t_plus, "block_timestamp": timestamp,
        "quote_mint": "SOL", "mint": "mint",
    }


def test_timecap_path_separates_entry_excursion_from_post_trigger_excursion():
    tape = [
        event("entry", 10, 1.0, 0, 100),
        event("high-before-trigger", 11, 1.2, 10, 110),
        event("trigger", 12, 0.95, 30, 130),
        event("post-trigger-low", 13, 0.90, 32, 132),
        event("exit", 15, 0.92, 35, 135),
    ]
    trade = {
        "mint": "mint", "symbol": "TEST", "entry_signature": "entry",
        "paired_exit_trigger_signature": "trigger", "exit_signature": "exit",
        "paired_exit_reason": "time_cap", "curve_replay_pnl_usd": -0.1,
    }
    result = _analyze_trade(trade, tape, {row["signature"]: i for i, row in enumerate(tape)})
    assert result["status"] == "ok"
    assert abs(result["max_favorable_move_from_entry_pct"] - 20.0) < 1e-9
    assert result["max_adverse_move_from_entry_pct"] == -10.0
    assert result["max_favorable_move_after_trigger_pct"] == 0.0
    assert result["max_adverse_move_after_trigger_pct"] < -5.0
    assert result["trigger_to_exit_seconds"] == 5
    assert result["trigger_to_exit_slot_gap"] == 3


def test_report_only_includes_timecap_and_writes_outputs(tmp_path):
    data, out = tmp_path / "data", tmp_path / "report"
    data.mkdir()
    tape = [
        event("entry", 10, 1.0, 0, 100),
        event("trigger", 12, 0.95, 30, 130),
        event("exit", 13, 0.94, 31, 131),
    ]
    (data / "sniper_tape_block_ordered_sniper.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in tape)
    )
    paired = {"trades": {
        "latency_500ms_no_wallet_lock": [{
            "mint": "mint", "symbol": "TEST", "entry_signature": "entry",
            "paired_exit_trigger_signature": "trigger", "exit_signature": "exit",
            "paired_exit_reason": "time_cap", "curve_replay_pnl_usd": -0.1,
        }]
    }}
    paired_path = tmp_path / "paired.json"
    paired_path.write_text(json.dumps(paired))
    report = build_report(str(data), str(out), str(paired_path))
    assert report["lanes"]["latency_500ms_no_wallet_lock"]["matched_paths"] == 1
    assert report["lanes"]["latency_500ms_no_wallet_lock"]["time_cap_trades"] == 1
    assert (out / "sniper_timecap_path_diagnostics.json").exists()
    assert (out / "sniper_timecap_path_diagnostics.md").exists()
