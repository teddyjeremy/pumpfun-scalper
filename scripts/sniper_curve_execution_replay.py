#!/usr/bin/env python3
"""Build the offline curve-aware execution replay for the launch sniper."""
from __future__ import annotations

import argparse

from pumpbot.curve_replay import build_report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--out", default="./report")
    parser.add_argument("--sol-usd", type=float, required=True,
                        help="SOL/USD reference used to convert the fixed USD stake into lamports")
    parser.add_argument("--stake-usd", type=float, default=1.0)
    parser.add_argument("--gas-usd", type=float, default=0.0285)
    parser.add_argument("--max-formula-error", type=float, default=1e-4)
    args = parser.parse_args()
    report = build_report(
        args.data_dir, args.out, sol_usd=args.sol_usd,
        stake_usd=args.stake_usd, gas_usd=args.gas_usd,
        max_formula_error=args.max_formula_error,
    )
    for lane, summary in report["lanes"].items():
        print(
            f"{lane}: replayed={summary['replayed_trades']}/{summary['trades_in_lane']} "
            f"wins={summary['wins']} losses={summary['losses']} "
            f"net={summary['curve_replay_net_pnl_usd']:.4f} USD"
        )


if __name__ == "__main__":
    main()
