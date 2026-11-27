#!/usr/bin/env python3
"""Fetch canonical block order for reserve-discontinuous sniper replay windows."""
from __future__ import annotations

import argparse
import os

from pumpbot.slot_order import reconstruct_slot_order


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--out", default="./report")
    parser.add_argument("--delay", type=float, default=0.05,
                        help="Seconds between confirmed-block RPC requests")
    args = parser.parse_args()
    api_key = os.environ.get("HELIUS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("HELIUS_API_KEY is required for canonical slot-order reconstruction")
    report = reconstruct_slot_order(
        args.data_dir, args.out, api_key, delay_seconds=max(0.0, args.delay)
    )
    print(
        "slot-order reconstruction: "
        f"candidate_slots={len(report['candidate_slots'])} "
        f"fetched={len(report['fetched_slots'])} "
        f"failed={len(report['failed_slots'])} "
        f"recovered_signatures={report['target_signatures_recovered']}"
    )


if __name__ == "__main__":
    main()
