"""Configuration from environment variables. Secrets are read by NAME only and never logged.

MODE:
  paper     (default) live feed, simulated fills at the next trade >= trigger+1s.
  simulate  live feed + unsigned simulateTransaction; never sends transactions.
  live      real money; requires explicit risk acknowledgement and burner wallet.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _f(name, default):
    v = os.environ.get(name)
    return float(v) if v not in (None, "") else default


def _i(name, default):
    v = os.environ.get(name)
    return int(v) if v not in (None, "") else default


def _b(name, default):
    v = os.environ.get(name)
    if v in (None, ""):
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def _list(name, default):
    v = os.environ.get(name)
    if v in (None, ""):
        return list(default)
    return [x.strip() for x in v.split(",") if x.strip()]


SECRET_ENV_NAMES = ("WALLET_PRIVATE_KEY", "RPC_HTTP_URL", "FEED_WS_URLS", "SEND_RPC_URLS", "JITO_URL",
                    "TELEGRAM_BOT_TOKEN", "HELIUS_API_KEY")


def redact(s: str) -> str:
    import re
    s = re.sub(r"(api[-_]?key=)[^&\\s\\\"']+", r"\\1***", s, flags=re.I)
    s = re.sub(r"(https?|wss?)://([^/\\s\\\"']+)/[^\\s\\\"']*", r"\\1://\\2/***", s)
    for n in SECRET_ENV_NAMES:
        v = os.environ.get(n)
        if v and len(v) > 6:
            s = s.replace(v, "***")
    return s


@dataclass
class Config:
    mode: str = "paper"
    data_dir: str = "./data"
    feed_ws_urls: list = field(default_factory=lambda: ["wss://api.mainnet-beta.solana.com"])
    rpc_http_url: str = "https://api.mainnet-beta.solana.com"
    send_rpc_urls: list = field(default_factory=list)
    jito_url: str = ""
    jito_tip_lamports: int = 0
    priority_micro_lamports: int = 200_000
    cu_limit: int = 120_000
    # High-frequency lane K is evaluated first; Lane B remains a fallback.
    lanes: list = field(default_factory=lambda: ["K", "B"])
    tradeable_quotes: list = field(default_factory=lambda: ["SOL"])
    # Standing guards (shared by every lane)
    entry_min_t: int = 30
    entry_max_t: int = 300
    mcap_min: float = 5_000.0
    mcap_max: float = 10_000.0
    min_cum_trades: int = 300
    creator_hold_max: float = 0.08
    bundle_hold_max: float = 0.25
    uniq60_min: float = 0.5
    # Lane K momentum parameters
    k_buy_sell_ratio: float = 1.0
    k_min_trades_10: int = 4
    k_min_buy_usd_10: float = 0.0
    # Exits
    tp_mult: float = 1.5
    max_hold_s: int = 90
    inval_sec: int = 60
    max_entries_per_coin: int = 2
    # Execution
    buy_slippage_bps: int = 1500
    sell_slippage_bps: list = field(default_factory=lambda: [3000, 6000, 9500])
    fee_rate: float = 0.0125
    gas_usd_round_trip: float = 0.0285
    # Bankroll / risk
    start_wallet_usd: float = 2.0
    base_stake_usd: float = 1.0
    max_stake_frac: float = 0.35
    max_stake_usd: float = 20.0
    ladder_step: float = 1.2
    daily_loss_cap: float = 0.25
    max_trades_per_day: int = 50
    loss_streak: int = 3
    skip_after_streak: int = 5
    kill_wallet_usd: float = 1.20
    lane_window: int = 100
    lane_min_trades_for_gate: int = 100
    live_require_paper_edge: bool = True
    i_understand_risk: bool = False
    sim_wallet_pubkey: str = ""
    telegram_chat_id: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        c = cls()
        c.mode = os.environ.get("MODE", "paper").strip().lower()
        c.data_dir = os.environ.get("DATA_DIR", c.data_dir)
        c.feed_ws_urls = _list("FEED_WS_URLS", c.feed_ws_urls)
        c.rpc_http_url = os.environ.get("RPC_HTTP_URL") or c.rpc_http_url
        c.send_rpc_urls = _list("SEND_RPC_URLS", [])
        c.jito_url = os.environ.get("JITO_URL", "")
        c.jito_tip_lamports = _i("JITO_TIP_LAMPORTS", 0)
        c.priority_micro_lamports = _i("PRIORITY_MICRO_LAMPORTS", c.priority_micro_lamports)
        c.cu_limit = _i("CU_LIMIT", c.cu_limit)
        c.lanes = [x.upper() for x in _list("LANES", c.lanes)]
        c.tradeable_quotes = _list("TRADEABLE_QUOTES", c.tradeable_quotes)
        c.k_buy_sell_ratio = _f("K_BUY_SELL_RATIO", c.k_buy_sell_ratio)
        c.k_min_trades_10 = _i("K_MIN_TRADES_10", c.k_min_trades_10)
        c.k_min_buy_usd_10 = _f("K_MIN_BUY_USD_10", c.k_min_buy_usd_10)
        c.tp_mult = max(1.5, _f("TP_MULT", c.tp_mult))
        c.max_hold_s = min(120, _i("MAX_HOLD_S", c.max_hold_s))
        c.buy_slippage_bps = _i("BUY_SLIPPAGE_BPS", c.buy_slippage_bps)
        c.sell_slippage_bps = [int(x) for x in _list("SELL_SLIPPAGE_BPS", [str(x) for x in c.sell_slippage_bps])]
        c.start_wallet_usd = _f("START_WALLET_USD", c.start_wallet_usd)
        c.base_stake_usd = _f("BASE_STAKE_USD", c.base_stake_usd)
        c.max_stake_usd = min(20.0, _f("MAX_STAKE_USD", c.max_stake_usd))
        c.max_trades_per_day = min(50, _i("MAX_TRADES_PER_DAY", c.max_trades_per_day))
        c.daily_loss_cap = _f("DAILY_LOSS_CAP", c.daily_loss_cap)
        c.kill_wallet_usd = _f("KILL_WALLET_USD", c.kill_wallet_usd)
        c.live_require_paper_edge = _b("LIVE_REQUIRE_PAPER_EDGE", True)
        c.i_understand_risk = os.environ.get("I_UNDERSTAND_RISK", "") == "yes"
        c.sim_wallet_pubkey = os.environ.get("SIM_WALLET_PUBKEY", "")
        c.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        return c

    def validate(self) -> list[str]:
        errs = []
        if self.mode not in ("paper", "simulate", "live"):
            errs.append(f"MODE must be paper|simulate|live, got {self.mode!r}")
        if not (30 <= self.entry_min_t < self.entry_max_t <= 300):
            errs.append("entry window must stay inside t+30s..t+300s")
        if self.k_buy_sell_ratio < 0 or self.k_min_trades_10 < 1 or self.k_min_buy_usd_10 < 0:
            errs.append("invalid Lane K momentum thresholds")
        if self.mode == "simulate" and not self.sim_wallet_pubkey:
            errs.append("MODE=simulate needs SIM_WALLET_PUBKEY (public key only)")
        if self.mode == "live":
            if not self.i_understand_risk:
                errs.append("MODE=live requires I_UNDERSTAND_RISK=yes")
            if not os.environ.get("WALLET_PRIVATE_KEY"):
                errs.append("MODE=live requires WALLET_PRIVATE_KEY (burner wallet only)")
            if any(u.startswith("wss://api.mainnet-beta.solana.com") for u in self.feed_ws_urls) and len(self.feed_ws_urls) == 1:
                errs.append("MODE=live: the free public websocket lags 2-5s; set FEED_WS_URLS to a paid/low-latency endpoint")
        return errs

    def public_dict(self) -> dict:
        d = dict(self.__dict__)
        d["feed_ws_urls"] = [redact(u) for u in self.feed_ws_urls]
        d["rpc_http_url"] = redact(self.rpc_http_url)
        d["send_rpc_urls"] = [redact(u) for u in self.send_rpc_urls]
        d["jito_url"] = redact(self.jito_url)
        d["wallet_private_key_set"] = bool(os.environ.get("WALLET_PRIVATE_KEY"))
        return d
