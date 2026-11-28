"""Canonical Solana transaction ordering for selected sniper replay windows.

The live websocket tape is ordered by callback arrival. Solana only exposes a
slot on logsSubscribe notifications, not the transaction's index within that
slot. This module fetches confirmed blocks only for tape windows where reserve
continuity fails, then replaces those mint/slot rows with block-ordered events.
It is research-only and never submits transactions.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections import defaultdict
from typing import Callable

from .curve_replay import (
    LANES,
    _first_later_slot_index,
    read_jsonl,
    validate_observed_transition,
)
from .events import SOL, events_from_logs, spot_quote_per_token


def _signature_indices(rows: list[dict]) -> dict[str, int]:
    """Map a signature to its final event row; callback rows are contiguous."""
    result: dict[str, int] = {}
    for index, row in enumerate(rows):
        signature = row.get("signature")
        if signature:
            result[signature] = index
    return result


def collect_candidate_slots(data_dir: str) -> tuple[set[int], set[str], dict]:
    """Find slots requiring canonical ordering or block backfill.

    Only intervals from existing latency-lane entry decisions through their
    later-slot exit fill are considered. A broken same-slot edge requests that
    slot; a cross-slot edge requests both endpoints and short gaps between them.
    """
    tape = read_jsonl(os.path.join(data_dir, "sniper_tape_sniper.jsonl"))
    by_mint: dict[str, list[dict]] = defaultdict(list)
    for row in tape:
        if row.get("quote_mint") == SOL and row.get("mint") and row.get("signature"):
            by_mint[row["mint"]].append(row)

    candidate_slots: set[int] = set()
    target_mints: set[str] = set()
    diagnostics = {
        "lane_trades_scanned": 0,
        "trades_with_reserve_discontinuity": 0,
        "discontinuity_edges": 0,
        "skipped_missing_signature": 0,
        "skipped_missing_later_slot_exit": 0,
    }

    for filename in LANES.values():
        trades = read_jsonl(os.path.join(data_dir, filename))
        for trade in trades:
            diagnostics["lane_trades_scanned"] += 1
            mint = trade.get("mint")
            rows = by_mint.get(mint)
            entry_sig, exit_sig = trade.get("entry_signature"), trade.get("exit_signature")
            if not rows or not entry_sig or not exit_sig or exit_sig == "timer":
                diagnostics["skipped_missing_signature"] += 1
                continue
            indices = _signature_indices(rows)
            if entry_sig not in indices or exit_sig not in indices:
                diagnostics["skipped_missing_signature"] += 1
                continue
            entry_index, exit_index = indices[entry_sig], indices[exit_sig]
            if exit_index <= entry_index:
                diagnostics["skipped_missing_signature"] += 1
                continue
            entry_slot = int(rows[entry_index].get("slot") or 0)
            exit_slot = int(rows[exit_index].get("slot") or 0)
            entry_fill_index = _first_later_slot_index(rows, entry_index, entry_slot)
            exit_fill_index = _first_later_slot_index(rows, exit_index, exit_slot)
            if entry_fill_index is None or exit_fill_index is None or exit_fill_index <= entry_fill_index:
                diagnostics["skipped_missing_later_slot_exit"] += 1
                continue

            found_gap = False
            previous = rows[entry_index]
            for event in rows[entry_index + 1:exit_fill_index + 1]:
                check = validate_observed_transition(previous, event)
                if not check["ok"]:
                    found_gap = True
                    diagnostics["discontinuity_edges"] += 1
                    previous_slot = int(previous.get("slot") or 0)
                    event_slot = int(event.get("slot") or 0)
                    candidate_slots.add(previous_slot)
                    candidate_slots.add(event_slot)
                    if 0 < event_slot - previous_slot <= 3:
                        candidate_slots.update(range(previous_slot + 1, event_slot))
                previous = event
            if found_gap:
                diagnostics["trades_with_reserve_discontinuity"] += 1
                target_mints.add(mint)

    return candidate_slots, target_mints, diagnostics


def fetch_confirmed_block(
    slot: int,
    api_key: str,
    *,
    delay_seconds: float = 0.15,
    opener: Callable = urllib.request.urlopen,
    retries: int = 4,
) -> dict:
    """Fetch one confirmed Solana block from Helius JSON-RPC."""
    url = f"https://mainnet.helius-rpc.com/?api-key={api_key}"
    payload = {
        "jsonrpc": "2.0",
        "id": slot,
        "method": "getBlock",
        "params": [
            slot,
            {
                "commitment": "confirmed",
                "encoding": "json",
                "transactionDetails": "full",
                "rewards": False,
                "maxSupportedTransactionVersion": 1,
            },
        ],
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    last_error = None
    for attempt in range(retries):
        try:
            with opener(request, timeout=40) as response:
                body = json.loads(response.read().decode("utf-8"))
            if body.get("error"):
                raise RuntimeError(f"getBlock RPC error: {body['error']}")
            if body.get("result") is None:
                raise RuntimeError(f"getBlock returned no confirmed block for slot {slot}")
            if delay_seconds:
                time.sleep(delay_seconds)
            return body["result"]
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt + 1 == retries:
                break
        except (urllib.error.URLError, TimeoutError, RuntimeError, ValueError) as exc:
            last_error = exc
            if attempt + 1 == retries:
                break
        time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"Unable to fetch confirmed block {slot}: {last_error}")


def rows_from_block(
    block: dict,
    slot: int,
    target_mints: set[str],
    launches: dict[str, dict],
) -> tuple[list[dict], dict]:
    """Decode selected-mint Pump trade events in canonical transaction order."""
    output: list[dict] = []
    transaction_count = 0
    transactions_without_logs = 0
    for transaction_index, transaction in enumerate(block.get("transactions") or []):
        transaction_count += 1
        tx = transaction.get("transaction") or {}
        signatures = tx.get("signatures") or []
        if not signatures:
            continue
        signature = signatures[0]
        meta = transaction.get("meta") or {}
        logs = meta.get("logMessages") or []
        if not logs:
            transactions_without_logs += 1
            continue
        event_index = 0
        for kind, event in events_from_logs(logs):
            if kind != "trade" or event.get("mint") not in target_mints:
                continue
            if event.get("quote_mint") != SOL:
                continue
            mint = event["mint"]
            launch = launches.get(mint) or {}
            timestamp = int(event.get("timestamp") or 0)
            virtual_sol = int(event.get("virtual_sol_reserves") or 0)
            virtual_token = int(event.get("virtual_token_reserves") or 0)
            output.append({
                "mint": mint,
                "symbol": launch.get("symbol") or mint[:8],
                "t_plus": timestamp - int(launch.get("created_at") or timestamp),
                "block_timestamp": timestamp,
                "slot": int(slot),
                "signature": signature,
                "side": "buy" if event.get("is_buy") else "sell",
                "trader": event.get("user"),
                "creator": event.get("creator"),
                "quote_mint": SOL,
                "price_sol": spot_quote_per_token(virtual_sol, virtual_token) if virtual_sol and virtual_token else None,
                "sol_amount_raw": event.get("sol_amount"),
                "token_amount_raw": event.get("token_amount"),
                "virtual_sol_reserves_raw": virtual_sol,
                "virtual_token_reserves_raw": virtual_token,
                "fee_bps": event.get("fee_bps"),
                "fee_raw": event.get("fee"),
                "creator_fee_bps": event.get("creator_fee_bps"),
                "creator_fee_raw": event.get("creator_fee"),
                "buyback_bps": event.get("buyback_bps"),
                "buyback_fee_raw": event.get("buyback_fee"),
                "cashback_bps": event.get("cashback_bps"),
                "cashback_fee_raw": event.get("cashback"),
                "mayhem_mode": event.get("mayhem_mode", launch.get("is_mayhem_mode", False)),
                "ix_name": event.get("ix_name"),
                "real_sol_reserves_raw": event.get("real_sol_reserves"),
                "real_token_reserves_raw": event.get("real_token_reserves"),
                "block_transaction_index": transaction_index,
                "block_event_index": event_index,
                "block_order_source": "helius_getBlock_confirmed",
            })
            event_index += 1
    return output, {
        "block_transactions": transaction_count,
        "transactions_without_logs": transactions_without_logs,
        "decoded_target_trade_events": len(output),
    }


def merge_block_ordered_rows(
    original_rows: list[dict],
    block_rows: list[dict],
    fetched_slots: set[int],
    target_mints: set[str],
) -> list[dict]:
    """Replace targeted mint/slot rows with canonical block-decoded events."""
    merged: list[tuple[int, dict]] = []
    replaced = 0
    for original_index, row in enumerate(original_rows):
        if row.get("mint") in target_mints and int(row.get("slot") or 0) in fetched_slots:
            replaced += 1
            continue
        merged.append((original_index, row))

    # Synthetic order values only affect rows within a slot. All rows for a
    # targeted mint in a fetched slot have been replaced by block-indexed rows.
    for block_index, row in enumerate(block_rows):
        merged.append((len(original_rows) + block_index, row))

    merged.sort(key=lambda item: (
        item[1].get("mint") or "",
        int(item[1].get("slot") or 0),
        int(item[1].get("block_transaction_index", item[0])),
        int(item[1].get("block_event_index", item[0])),
    ))
    return [row for _, row in merged]


def reconstruct_slot_order(
    data_dir: str,
    out_dir: str,
    api_key: str,
    *,
    delay_seconds: float = 0.05,
    fetcher: Callable = fetch_confirmed_block,
) -> dict:
    tape_path = os.path.join(data_dir, "sniper_tape_sniper.jsonl")
    original_rows = read_jsonl(tape_path)
    candidate_slots, target_mints, scan = collect_candidate_slots(data_dir)
    launches = {
        row["mint"]: row
        for row in read_jsonl(os.path.join(data_dir, "sniper_launches_sniper.jsonl"))
        if row.get("mint")
    }

    fetched_slots: set[int] = set()
    block_rows: list[dict] = []
    block_summaries: dict[str, dict] = {}
    fetch_errors: dict[str, str] = {}
    for slot in sorted(candidate_slots):
        try:
            block = fetcher(slot, api_key, delay_seconds=delay_seconds)
            rows, summary = rows_from_block(block, slot, target_mints, launches)
            fetched_slots.add(slot)
            block_rows.extend(rows)
            block_summaries[str(slot)] = summary
        except Exception as exc:
            fetch_errors[str(slot)] = f"{type(exc).__name__}: {exc}"

    ordered_rows = merge_block_ordered_rows(
        original_rows, block_rows, fetched_slots, target_mints
    )
    os.makedirs(data_dir, exist_ok=True)
    ordered_path = os.path.join(data_dir, "sniper_tape_block_ordered_sniper.jsonl")
    with open(ordered_path, "w", encoding="utf-8") as handle:
        for row in ordered_rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")

    original_signatures = {
        (row.get("mint"), row.get("signature"), int(row.get("slot") or 0))
        for row in original_rows
    }
    block_signatures = {
        (row.get("mint"), row.get("signature"), int(row.get("slot") or 0))
        for row in block_rows
    }
    report = {
        "model": "confirmed-block transaction ordering and targeted Pump trade-event backfill",
        "paper_only": True,
        "strategy_decisions_changed": False,
        "raw_tape_rows": len(original_rows),
        "ordered_tape_rows": len(ordered_rows),
        "candidate_slots": sorted(candidate_slots),
        "fetched_slots": sorted(fetched_slots),
        "failed_slots": fetch_errors,
        "target_mints": len(target_mints),
        "block_transactions": sum(x["block_transactions"] for x in block_summaries.values()),
        "decoded_target_trade_events": len(block_rows),
        "target_signatures_recovered": len(block_signatures - original_signatures),
        "raw_target_rows_replaced": sum(
            1 for row in original_rows
            if row.get("mint") in target_mints and int(row.get("slot") or 0) in fetched_slots
        ),
        "scan": scan,
        "block_summaries": block_summaries,
        "ordered_tape_file": os.path.basename(ordered_path),
        "limitations": [
            "getBlock uses confirmed canonical blocks; processed websocket events that never landed in a confirmed block are excluded from targeted fetched slots.",
            "Only slots with reserve discontinuities inside the existing latency-lane trade windows are fetched; this is not a full historical backfill of every launch.",
            "A failed block fetch leaves that slot's original callback-order tape rows unchanged.",
            "A transaction whose confirmed meta.logMessages are unavailable cannot be decoded by this report.",
            "The replay remains a counterfactual fill model, not proof of transaction inclusion or an executable fill.",
        ],
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "sniper_slot_order_reconstruction.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    lines = [
        "# Sniper canonical slot-order reconstruction",
        "",
        f"- Candidate slots: {len(candidate_slots)}",
        f"- Successfully fetched: {len(fetched_slots)}",
        f"- Failed: {len(fetch_errors)}",
        f"- Target mints: {len(target_mints)}",
        f"- Decoded target trade events: {len(block_rows)}",
        f"- Newly recovered target signatures: {len(block_signatures - original_signatures)}",
        f"- Ordered tape rows: {len(ordered_rows)}",
        "",
        "This enriches the offline replay tape only. It does not change strategy decisions or submit transactions.",
        "",
        "## Fetch failures",
        "",
    ]
    lines.extend(f"- Slot {slot}: {error}" for slot, error in fetch_errors.items())
    if not fetch_errors:
        lines.append("- None")
    lines += ["", "## Limitations", ""]
    lines.extend(f"- {item}" for item in report["limitations"])
    with open(os.path.join(out_dir, "sniper_slot_order_reconstruction.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return report
