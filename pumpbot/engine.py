"""Per-coin decision engine shared by the live bot and the backtest replay.

Driven by on_trade(trade) and on_tick(now). Emits actions:
  ("enter", info)  -> the coin wants an entry now (the bot/risk manager decides whether to take it)
  ("exit", info)   -> exit trigger for an open position
Paper/replay fills follow the research convention: the fill is the curve spot of the first trade at or after
trigger+1s (within 2s), else the last spot at trigger+1s.

HARD SAFETY LIMITS (not configurable): no entry before t+30s or after t+300s after launch, and every position
is closed by 120s at the latest (the configured hold cap can only be lower). No holding, no sniping.
"""
from __future__ import annotations

from .signals import decide, guards, lane_k, lane_b
from .tape import Tape

HARD_MIN_ENTRY_T = 30
HARD_MAX_ENTRY_T = 300
HARD_MAX_HOLD_S = 120
FILL_DELAY = 1
FILL_WINDOW = 2
FADE_CFG = {"vol_frac": 0.30, "min_trades_15": 3, "sell_buy_ratio": 2.0, "vol_window": 15, "flow_window": 30}


def net_pnl(ent_px: float, exit_px: float, stake: float = 1.0, fee: float = 0.0125, gas: float = 0.0285) -> float:
    """$ P&L of a stake bought at ent_px and sold at exit_px, after 1.25% each way and flat round-trip gas."""
    return stake * ((1 - fee) ** 2 * exit_px / ent_px - 1) - gas


class Pending:
    """A fill waiting for the first trade with bt >= target (within FILL_WINDOW)."""
    def __init__(self, kind: str, target: int, info: dict):
        self.kind, self.target, self.info = kind, target, info


class Position:
    def __init__(self, lane, ent_px, ent_bt, ent_i, swing, entry_t, stake=1.0):
        self.lane, self.ent_px, self.ent_bt, self.ent_i, self.swing = lane, ent_px, ent_bt, ent_i, swing
        self.entry_t, self.stake = entry_t, stake
        self.peak = ent_px; self.trough = ent_px
        self.exit_reason = None; self.trig_bt = None


class CoinEngine:
    def __init__(self, tape: Tape, cfg, lanes=None, rug_bundle: bool = True, fade: bool = False,
                 select_from_bt: int | None = None, select_until_bt: int | None = None, gate=None):
        self.tape, self.cfg = tape, cfg
        self.lanes = lanes if lanes is not None else cfg.lanes
        self.rug_bundle, self.fade = rug_bundle, fade
        self.hold = min(int(cfg.max_hold_s), HARD_MAX_HOLD_S)
        self.min_t = max(HARD_MIN_ENTRY_T, int(cfg.entry_min_t))
        self.max_t = min(HARD_MAX_ENTRY_T, int(cfg.entry_max_t))
        self.sel_from, self.sel_until = select_from_bt, select_until_bt
        self.gate = gate              # callable(info) -> (ok, reason, stake); None = always take (research replay)
        self.pending: Pending | None = None
        self.pos: Position | None = None
        self.entries = 0
        self.next_ok_bt = 0
        self.closed: list[dict] = []
        self.last_eval_bt = None

    # ------------------------------------------------------------------ helpers
    def _resolve_pending_fallback(self, now_bt: float):
        """No trade inside [target, target+2]: fill at the last known spot at target+1."""
        p = self.pending
        if p is None or now_bt <= p.target + FILL_WINDOW:
            return None
        i = self.tape.n - 1
        return self._fill(p, self.tape.spot[i], p.target, i)

    def _fill(self, p: Pending, px: float, fbt: int, fi: int):
        self.pending = None
        if p.kind == "enter":
            info = p.info
            self.pos = Position(info["lane"], px, fbt, fi, info["swing"], info["t"], info.get("stake", 1.0))
            self.entries += 1
            return ("entered", {"px": px, "bt": fbt, **info})
        pos = self.pos
        self.pos = None
        rec = {"mint": self.tape.mint, "lane": pos.lane, "entry_t": pos.entry_t, "ent_bt": pos.ent_bt, "ent_px": pos.ent_px,
               "exit_bt": fbt, "exit_px": px, "hold_s": fbt - pos.ent_bt, "exit": p.info["reason"], "mult": px / pos.ent_px,
               "net": net_pnl(pos.ent_px, px, 1.0, self.cfg.fee_rate, self.cfg.gas_usd_round_trip),
               "mfe": pos.peak / pos.ent_px - 1, "mae": pos.trough / pos.ent_px - 1, "stake": pos.stake,
               "quote_mint": self.tape.quote_mint}
        self.closed.append(rec)
        self.next_ok_bt = fbt + 1
        return ("exited", rec)

    def _diagnose(self, i: int) -> dict | None:
        """Explain every completed-second entry evaluation without changing trading decisions."""
        tp = self.tape
        t = tp.t[i]
        if not (HARD_MIN_ENTRY_T < t <= HARD_MAX_ENTRY_T):
            return None
        mc = tp.spot[i] * 1e9
        base = {"mint": tp.mint, "t_plus": t, "bt": tp.bt[i], "spot": tp.spot[i], "mcap": mc,
                "cum_trades": i + 1, "decision": "rejected", "reasons": []}
        if self.pos:
            base["reasons"] = ["position_already_open"]
            return base
        if self.pending:
            base["reasons"] = ["entry_or_exit_pending"]
            return base
        if self.entries >= self.cfg.max_entries_per_coin:
            base["reasons"] = ["max_entries_per_coin_reached"]
            return base
        if tp.bt[i] < self.next_ok_bt:
            base["reasons"] = ["post_exit_cooldown"]
            return base
        if not (self.cfg.mcap_min <= mc <= self.cfg.mcap_max):
            base["reasons"] = ["market_cap_below_min" if mc < self.cfg.mcap_min else "market_cap_above_max"]
            return base
        if tp.n < 20:
            base["reasons"] = ["insufficient_tape_history"]
            return base
        f = tp.features(i)
        base["features"] = f
        why = guards(f, self.cfg, self.rug_bundle)
        if why:
            base["reasons"] = [why]
            return base
        lane_reasons = {}
        for ln in self.lanes:
            if ln == "K":
                passed = lane_k(f, self.cfg)
                failures = []
                if f["n10"] < self.cfg.k_min_trades_10:
                    failures.append("lane_k_trades_10_below_min")
                if f["b10"] < self.cfg.k_min_buy_usd_10:
                    failures.append("lane_k_buy_usd_10_below_min")
                if f["b10"] < self.cfg.k_buy_sell_ratio * max(f["s10"], 1.0):
                    failures.append("lane_k_buy_sell_ratio")
            elif ln == "B":
                passed = lane_b(f, self.cfg)
                failures = []
                if not f["steady"]:
                    failures.append("lane_b_not_steady")
                if f["nw30"] < 15:
                    failures.append("lane_b_fresh_buyers_below_15")
                if f["uniq60"] < self.cfg.uniq60_min:
                    failures.append("lane_b_unique_trader_ratio")
            else:
                lane_reasons[ln] = ["unsupported_lane"]
                continue
            lane_reasons[ln] = [] if passed else failures
        base["lane_checks"] = lane_reasons
        passed_lanes = [ln for ln, reasons in lane_reasons.items() if not reasons]
        if passed_lanes:
            base["decision"] = "signal"
            base["signal_lanes"] = passed_lanes
            base["reasons"] = []
        else:
            base["reasons"] = sorted({reason for reasons in lane_reasons.values() for reason in reasons}) or ["no_configured_lane_passed"]
        return base

    def _evaluate(self, i: int):
        """Entry decision at the last trade of a completed second."""
        tp = self.tape
        if self.pos or self.pending or self.entries >= self.cfg.max_entries_per_coin:
            return None
        if tp.bt[i] < self.next_ok_bt:
            return None
        t = tp.t[i]
        if not (self.min_t < t <= self.max_t):            # hard: never before t+30s, never after t+5min
            return None
        mc = tp.spot[i] * 1e9
        if not (self.cfg.mcap_min <= mc <= self.cfg.mcap_max):
            return None
        if tp.n < 20:
            return None
        if self.sel_from is not None and (tp.bt[i] < self.sel_from or tp.bt[i] + 123 > (self.sel_until or 1e18)):
            return None
        f = tp.features(i)
        lane, why = decide(f, self.cfg, self.lanes, self.rug_bundle)
        if lane is None:
            return None
        info = {"lane": lane, "sig_bt": tp.bt[i], "sig_i": i, "t": t, "mc": mc, "swing": f["swing30"], "feat": f}
        return info

    def _exit_check(self, j: int):
        pos = self.pos; tp = self.tape
        bt = tp.bt[j]; s = tp.spot[j]
        pos.peak = max(pos.peak, s); pos.trough = min(pos.trough, s)
        if s >= pos.ent_px * self.cfg.tp_mult:
            return "tp"
        dt = bt - pos.ent_bt
        if dt <= self.cfg.inval_sec and s < pos.swing:
            return "inval_low"
        if dt >= 5:
            w = tp.win(j, 10)
            if w["ns"] >= 2 * max(1, w["nb"]) and w["ns"] >= 3:
                return "sell_surge"
        if self.fade:
            if dt >= FADE_CFG["vol_window"]:
                w = tp.win(j, FADE_CFG["vol_window"])
                if w["n"] < FADE_CFG["min_trades_15"]:
                    return "fade"
            if dt >= FADE_CFG["flow_window"]:
                w = tp.win(j, FADE_CFG["flow_window"])
                if w["sell_usd"] > 0 and w["sell_usd"] >= FADE_CFG["sell_buy_ratio"] * w["buy_usd"]:
                    return "fade"
        return None

    # ------------------------------------------------------------------ public
    def _last_le(self, j: int, target: int) -> int:
        k = j
        while k > 0 and self.tape.bt[k] > target:
            k -= 1
        return k

    def _resolve_with(self, j: int):
        """Resolve a pending fill using trade j (just appended). Returns (event, consumed_trade)."""
        p = self.pending; bt = self.tape.bt[j]
        if p is None or bt < p.target:
            return None, False
        if bt <= p.target + FILL_WINDOW:
            return self._fill(p, self.tape.spot[j], bt, j), True
        k = self._last_le(j - 1, p.target)                 # no trade inside the window: last spot at target
        return self._fill(p, self.tape.spot[k], p.target, k), False

    def on_trade(self, x: dict) -> list:
        """Feed one trade. Events: ('signal', info), ('entered', ...), ('exit_trigger', ...), ('exited', rec)."""
        out = []
        tp = self.tape
        if not x.get("spot"):
            return out
        bt = int(x["bt"])
        # 1) the previous second is complete -> entry decision at its last trade
        if tp.n and bt > tp.bt[-1]:
            diagnostic = self._diagnose(tp.n - 1)
            if diagnostic:
                out.append(("diagnostic", diagnostic))
            if not self.pending:
                info = self._evaluate(tp.n - 1)
                if info:
                    ok, why, stake = self.gate(info) if self.gate else (True, "ok", 1.0)
                    info["taken"], info["gate_reason"], info["stake"] = ok, why, stake
                    out.append(("signal", info))
                    if ok:
                        self.pending = Pending("enter", info["sig_bt"] + FILL_DELAY, info)
        j = tp.append(x)
        # 2) open position: time cap first (hard <= 120s)
        if self.pos and not self.pending and bt > self.pos.ent_bt + self.hold:
            dl = self.pos.ent_bt + self.hold
            self.pending = Pending("exit", dl + FILL_DELAY, {"reason": "time_cap", "trig_bt": dl})
            out.append(("exit_trigger", {"reason": "time_cap", "trig_bt": dl}))
        # 3) resolve fills with this trade
        ev, consumed = self._resolve_with(j)
        if ev:
            out.append(ev)
            if consumed or ev[0] == "exited":
                return out                  # trades in the fill's own second are not exit-checked
        # 4) exit checks for an open position
        if self.pos and not self.pending and bt > self.pos.ent_bt:
            why = self._exit_check(j)
            if why:
                self.pending = Pending("exit", bt + FILL_DELAY, {"reason": why, "trig_bt": bt})
                out.append(("exit_trigger", {"reason": why, "trig_bt": bt}))
        return out

    def on_tick(self, now: float) -> list:
        """Wall-clock driver for live use: time cap and fill fallbacks when the coin goes quiet."""
        out = []
        if self.pending and now > self.pending.target + FILL_WINDOW:
            ev = self._resolve_pending_fallback(now)
            if ev:
                out.append(ev)
        if self.pos and not self.pending and now > self.pos.ent_bt + self.hold:
            dl = self.pos.ent_bt + self.hold
            self.pending = Pending("exit", dl + FILL_DELAY, {"reason": "time_cap", "trig_bt": dl})
            out.append(("exit_trigger", {"reason": "time_cap", "trig_bt": dl}))
        return out

    def finish(self) -> list:
        """End of a recorded tape (replay): flush the last second and close anything open at the last spot."""
        out = []
        tp = self.tape
        if not tp.n:
            return out
        if not self.pos and not self.pending:
            info = self._evaluate(tp.n - 1)
            if info:
                ok, why, stake = self.gate(info) if self.gate else (True, "ok", 1.0)
                info["taken"], info["gate_reason"], info["stake"] = ok, why, stake
                out.append(("signal", info))
                if ok:
                    self.pending = Pending("enter", info["sig_bt"] + FILL_DELAY, info)
        if self.pending and self.pending.kind == "enter":
            out.append(self._fill(self.pending, tp.spot[-1], self.pending.target, tp.n - 1))
        if self.pos and not self.pending:
            dl = self.pos.ent_bt + self.hold
            self.pending = Pending("exit", dl + FILL_DELAY, {"reason": "time_cap", "trig_bt": dl})
        if self.pending:
            k = tp.n - 1
            while k > 0 and tp.bt[k] > self.pending.target:
                k -= 1
            out.append(self._fill(self.pending, tp.spot[k], self.pending.target, k))
        return out
