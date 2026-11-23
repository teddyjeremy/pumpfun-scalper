"""Loads the screen/backtest rule functions read-only, with the shared RPC pool stubbed out.

The paper trader never makes RPC calls through lib.rpc, so importing the screen modules
must not start the pipeline pool's health probes. We register a stub lib.rpc first.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path("/workspace/pumpfun")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "lib.rpc" not in sys.modules:
    stub = types.ModuleType("lib.rpc")

    def _blocked(*a, **k):
        raise RuntimeError("paper trader: lib.rpc is stubbed (no pool RPC from paper)")

    def __getattr__(name):  # any `from lib.rpc import X`
        return _blocked

    stub.__getattr__ = __getattr__
    stub.rpc_call = _blocked
    stub.rpc_batch = _blocked
    sys.modules["lib.rpc"] = stub

from lib.constants import (  # noqa: E402
    CURVE_FEE_BPS, DEFAULT_PRIORITY_SOL, DEFAULT_BASE_FEE_SOL, TOTAL_SUPPLY_UI,
    ACTIVE_LOOKBACK_SEC, ACTIVE_MIN_TRADES, ACTIVE_MIN_USD, PUMP_PROGRAM,
    TRADE_EVENT_DISC, CREATE_EVENT_DISC, COMPLETE_EVENT_DISC,
)
from lib.parse import parse_trade_event, b58  # noqa: E402
from screen.prescreen import (  # noqa: E402
    entry_setup_metrics, MIN_CUM_VOL, MIN_CUM_TRADES, MIN_CUM_WALLETS, MCAP_MIN, MCAP_MAX,
)
from screen.backtest import exit_path, DYNAMIC_SPECS, fade_hit, FADE_CFG  # noqa: E402
