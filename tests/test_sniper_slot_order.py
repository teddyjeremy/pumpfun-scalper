import json
from unittest.mock import patch

from pumpbot.slot_order import (
    collect_candidate_slots,
    merge_block_ordered_rows,
    rows_from_block,
)


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _buy_builder(mint):
    vq, vt = 30_000_000_000, 1_000_000_000_000_000

    def buy(signature, slot, quote=10_000_000):
        nonlocal vq, vt
        tokens = (quote - 1) * vt // (vq + quote - 1)
        vq += quote
        vt -= tokens
        return {
            "mint": mint, "quote_mint": "SOL", "signature": signature,
            "slot": slot, "side": "buy", "sol_amount_raw": quote,
            "token_amount_raw": tokens, "virtual_sol_reserves_raw": vq,
            "virtual_token_reserves_raw": vt,
        }

    return buy


def test_collect_candidate_slots_finds_same_slot_reserve_gap(tmp_path):
    mint = "SlotGapMint"
    buy = _buy_builder(mint)
    tape = [
        buy("entry-decision", 10),
        buy("entry-fill", 11),
        buy("gap-a", 12),
    ]
    bad = buy("gap-b", 12)
    bad["virtual_sol_reserves_raw"] += 1
    tape.append(bad)
    tape.extend([buy("exit-trigger", 13), buy("exit-fill", 14)])

    _write_jsonl(tmp_path / "sniper_tape_sniper.jsonl", tape)
    _write_jsonl(tmp_path / "sniper_latency_0500ms_trades_sniper.jsonl", [{
        "mint": mint, "entry_signature": "entry-decision",
        "exit_signature": "exit-trigger",
    }])

    slots, mints, diagnostics = collect_candidate_slots(str(tmp_path))

    assert 12 in slots
    assert mint in mints
    assert diagnostics["trades_with_reserve_discontinuity"] == 1


def test_merge_block_ordered_rows_replaces_callback_order_with_block_order():
    mint = "CanonicalMint"
    other = "OtherMint"
    original = [
        {"mint": mint, "slot": 20, "signature": "tx-b", "side": "sell"},
        {"mint": mint, "slot": 20, "signature": "tx-a", "side": "buy"},
        {"mint": other, "slot": 20, "signature": "other-tx"},
        {"mint": mint, "slot": 21, "signature": "next-slot"},
    ]
    block_rows = [
        {"mint": mint, "slot": 20, "signature": "tx-a",
         "block_transaction_index": 3, "block_event_index": 0},
        {"mint": mint, "slot": 20, "signature": "tx-a",
         "block_transaction_index": 3, "block_event_index": 1},
        {"mint": mint, "slot": 20, "signature": "tx-b",
         "block_transaction_index": 7, "block_event_index": 0},
    ]

    merged = merge_block_ordered_rows(original, block_rows, {20}, {mint})
    target = [row for row in merged if row.get("mint") == mint and row.get("slot") == 20]

    assert [row["signature"] for row in target] == ["tx-a", "tx-a", "tx-b"]
    assert [row["block_event_index"] for row in target] == [0, 1, 0]
    assert any(row.get("mint") == other for row in merged)
    assert any(row.get("signature") == "next-slot" for row in merged)


def test_rows_from_block_keeps_transaction_and_inner_event_order():
    mint = "DecodedMint"
    fake_event = {
        "mint": mint, "quote_mint": "SOL", "timestamp": 100,
        "is_buy": True, "user": "buyer", "creator": "creator",
        "sol_amount": 10_000_000, "token_amount": 100_000_000,
        "virtual_sol_reserves": 30_010_000_000,
        "virtual_token_reserves": 999_900_000_000_000,
        "real_sol_reserves": 10_000_000, "real_token_reserves": 699_900_000_000_000,
        "fee_bps": 95, "fee": 95_000, "creator_fee_bps": 30,
        "creator_fee": 30_000, "buyback_bps": 5_000, "buyback_fee": 0,
        "cashback_bps": 0, "cashback": 0, "mayhem_mode": False,
        "ix_name": "buy",
    }
    block = {
        "transactions": [{
            "transaction": {"signatures": ["signature-1"]},
            "meta": {"logMessages": ["ignored"]},
        }]
    }
    with patch("pumpbot.slot_order.events_from_logs", return_value=[("trade", fake_event), ("trade", fake_event)]):
        rows, summary = rows_from_block(
            block, 50, {mint}, {mint: {"symbol": "TEST", "created_at": 90}}
        )

    assert len(rows) == 2
    assert rows[0]["block_transaction_index"] == 0
    assert [row["block_event_index"] for row in rows] == [0, 1]
    assert all(row["signature"] == "signature-1" for row in rows)
    assert rows[0]["t_plus"] == 10
    assert summary["decoded_target_trade_events"] == 2



def test_fetch_confirmed_block_advertises_versioned_transaction_support():
    from pumpbot.slot_order import fetch_confirmed_block

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"jsonrpc":"2.0","id":123,"result":{"transactions":[]}}'

    def opener(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        assert timeout == 40
        assert payload["method"] == "getBlock"
        assert payload["params"][1]["maxSupportedTransactionVersion"] == 1
        return Response()

    result = fetch_confirmed_block(123, "test-key", delay_seconds=0, opener=opener)

    assert result == {"transactions": []}
