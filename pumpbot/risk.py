"""Bankroll / no-wipeout layer (RULES.md 10-15). Pure logic, no I/O except optional state dict.

- one open position at a time
- stake ladder: $1 start, x1.2 each time the wallet grows 20% over the last step (and back down), stake <= 35% of
  wallet (floor $1), hard cap $20
- daily loss cap -25% of the start-of-day wallet, max 50 trades/day
- after 3 losses in a row skip the next 5 signals
- kill switch: wallet < $1.20 -> stop completely
- lane gate: a lane is disabled while the average of its last 100 trades is < 0
"""
from __future__ import annotations

import statistics as st
from collections import defaultdict, deque


class RiskManager:
    def __init__(self, cfg, wallet_usd: float | None = None):
        self.cfg = cfg
        self.wallet = wallet_usd if wallet_usd is not None else cfg.start_wallet_usd
        self.step_base = self.wallet
        self.stake = cfg.base_stake_usd
        self.day = None
        self.day_start = self.wallet
        self.day_trades = 0
        self.streak = 0
        self.skip = 0
        self.open = 0
        self.killed = False
        self.lane_hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=cfg.lane_window))
        self.wipeouts = 0
        self.paper_reset = False       # paper mode: after a wipeout, count it and restart the $2 wallet to keep collecting data

    # ---------------------------------------------------------------- state
    def new_day(self, day: str):
        if day != self.day:
            self.day, self.day_start, self.day_trades = day, self.wallet, 0

    def lane_enabled(self, lane: str) -> bool:
        h = self.lane_hist[lane]
        if len(h) < self.cfg.lane_window:
            return True                      # not enough history yet (paper mode builds it)
        return st.mean(h) >= 0

    def _ladder(self):
        step = self.cfg.ladder_step
        while self.wallet >= self.step_base * step:
            self.step_base *= step; self.stake *= step
        while self.wallet < self.step_base / step and self.stake > self.cfg.base_stake_usd:
            self.step_base /= step; self.stake = max(self.cfg.base_stake_usd, self.stake / step)

    def stake_for_next(self) -> float:
        self._ladder()
        s = min(self.stake, max(self.cfg.base_stake_usd, self.cfg.max_stake_frac * self.wallet))
        return round(min(s, self.cfg.max_stake_usd), 4)

    # ---------------------------------------------------------------- decisions
    def allow(self, lane: str, day: str) -> tuple[bool, str, float]:
        """Ask before entering. Returns (ok, reason, stake_usd). Consumes a 'skip' slot when skipping."""
        self.new_day(day)
        if self.killed or self.wallet < self.cfg.kill_wallet_usd:
            if self.paper_reset and self.open == 0:
                self.wipeouts += 1
                self.wallet = self.step_base = self.day_start = self.cfg.start_wallet_usd
                self.stake = self.cfg.base_stake_usd; self.killed = False; self.streak = 0; self.skip = 0
            else:
                self.killed = True
                return False, "kill_switch_wallet_below_min", 0.0
        if self.open >= 1:
            return False, "one_position_at_a_time", 0.0
        if self.day_trades >= self.cfg.max_trades_per_day:
            return False, "daily_trade_cap", 0.0
        if self.wallet <= self.day_start * (1 - self.cfg.daily_loss_cap):
            return False, "daily_loss_cap", 0.0
        if not self.lane_enabled(lane):
            return False, f"lane_{lane}_disabled_last{self.cfg.lane_window}_avg_lt_0", 0.0
        if self.skip > 0:
            self.skip -= 1
            return False, "cooldown_after_loss_streak", 0.0
        stake = self.stake_for_next()
        if stake > self.wallet:
            return False, "stake_gt_wallet", 0.0
        return True, "ok", stake

    def on_open(self):
        self.open += 1
        self.day_trades += 1

    def on_close(self, lane: str, pnl_usd: float, net_per_1usd: float):
        self.open = max(0, self.open - 1)
        self.wallet += pnl_usd
        self.lane_hist[lane].append(net_per_1usd)
        if pnl_usd < 0:
            self.streak += 1
        else:
            self.streak = 0
        if self.streak >= self.cfg.loss_streak:
            self.skip = self.cfg.skip_after_streak
            self.streak = 0
        if self.wallet < self.cfg.kill_wallet_usd:
            self.killed = True

    def state(self) -> dict:
        return {"wallet": round(self.wallet, 4), "stake_next": self.stake_for_next(), "day": self.day,
                "day_start": round(self.day_start, 4), "day_trades": self.day_trades, "skip": self.skip,
                "streak": self.streak, "killed": self.killed, "wipeouts": self.wipeouts,
                "lanes": {k: {"n": len(v), "avg": round(st.mean(v), 4) if v else None, "enabled": self.lane_enabled(k)}
                          for k, v in self.lane_hist.items()}}


def pnl_for_stake(net_per_1usd: float, stake: float, gas: float = 0.0285) -> float:
    """Scale a $1-stake net result to another stake (fees scale, gas is flat)."""
    return stake * (net_per_1usd + gas) - gas


def portfolio_replay(trades: list[dict], cfg) -> dict:
    """Run replayed trades (all coins, in time order) through the risk manager from a $2 wallet."""
    rm = RiskManager(cfg)
    taken = []; skipped = defaultdict(int)
    busy_until = 0
    for r in sorted(trades, key=lambda r: r["ent_bt"]):
        if r["ent_bt"] < busy_until:
            skipped["one_position_at_a_time"] += 1
            continue
        day = str(r["day"])
        ok, why, stake = rm.allow(r["lane"], day)
        if not ok:
            skipped[why] += 1
            if why == "kill_switch_wallet_below_min":
                break
            continue
        rm.on_open()
        pnl = pnl_for_stake(r["net"], stake, cfg.gas_usd_round_trip)
        rm.on_close(r["lane"], pnl, r["net"])
        busy_until = r["exit_bt"] + 1
        taken.append((day, stake, pnl))
    by = defaultdict(lambda: [0, 0.0])
    for d, s, p in taken:
        by[d][0] += 1; by[d][1] += p
    return {"start_wallet": cfg.start_wallet_usd, "end_wallet": round(rm.wallet, 4), "trades_taken": len(taken),
            "killed": rm.killed, "skipped": dict(skipped), "per_day": {d: {"n": v[0], "pnl": round(v[1], 3)} for d, v in sorted(by.items())}}
