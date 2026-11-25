"""Streaming per-coin trade tape with causal features (no lookahead)."""
from __future__ import annotations

import bisect
from collections import defaultdict

SUPPLY = 1e9


class Tape:
    def __init__(self, mint: str, t0: int, creator: str | None = None, quote_mint: str = "SOL"):
        self.mint, self.t0, self.creator, self.quote_mint = mint, int(t0), creator, quote_mint
        self.bt: list[int] = []; self.t: list[int] = []; self.spot: list[float] = []; self.buy: list[bool] = []
        self.usd: list[float] = []; self.trader: list = []; self.tok: list[float] = []; self.sol: list[float] = []
        self.slot: list[int] = []
        self.high: list[float] = []; self.t_high: list[int] = []; self.prev_high: list[float] = []
        self.prev_high_age: list[int] = []; self.low_since_high: list[float] = []; self.t_low_since_high: list[int] = []
        self.cum_vol: list[float] = []; self.cum_w: list[int] = []; self.top_seller_share: list[float] = []
        self.creator_hold: list[float] = []; self.bundle_hold: list[float] = []; self.last_cre_sell: list[int] = []
        self._pb = [0.0]; self._ps = [0.0]; self._nb = [0]; self._ns = [0]; self._nw = [0]
        self._hi = 0.0; self._thi = 0; self._lo = float("inf"); self._tlo = 0; self._cv = 0.0
        self._wallets: set = set(); self._seen: set = set()
        self._sellers: dict = defaultdict(float); self._sell_tot = 0.0
        self._bal: dict = defaultdict(float); self._bundle: set = set(); self._slot0 = None
        self._cre_sell = -1

    @property
    def n(self) -> int:
        return len(self.bt)

    def append(self, x: dict) -> int | None:
        s = x.get("spot")
        if not s:
            return None
        s = float(s); bt = int(x["bt"]); side_buy = x["side"] == "buy"; u = float(x.get("usd") or 0.0)
        tr = x.get("trader"); tok = float(x.get("tok") or 0.0); sl = int(x.get("slot") or 0)
        self.prev_high.append(self._hi); self.prev_high_age.append(bt - self._thi if self._hi > 0 else 0)
        if s > self._hi:
            self._hi, self._thi = s, bt; self._lo, self._tlo = s, bt
            self._sellers = defaultdict(float); self._sell_tot = 0.0
        if s < self._lo:
            self._lo, self._tlo = s, bt
        if not side_buy:
            self._sellers[tr] += u; self._sell_tot += u
        self._cv += u
        if tr:
            self._wallets.add(tr)
        if self._slot0 is None:
            self._slot0 = sl
        if side_buy and sl <= self._slot0 + 1:
            self._bundle.add(tr)
        if side_buy:
            self._bal[tr] += tok
        else:
            self._bal[tr] -= tok
            if self.creator and tr == self.creator:
                self._cre_sell = bt
        nw = 1 if (tr and tr not in self._seen and side_buy) else 0
        if tr:
            self._seen.add(tr)
        self.bt.append(bt); self.t.append(int(x["t"]) if x.get("t") is not None else bt - self.t0)
        self.spot.append(s); self.buy.append(side_buy); self.usd.append(u); self.trader.append(tr)
        self.tok.append(tok); self.sol.append(float(x.get("sol") or 0.0)); self.slot.append(sl)
        self.high.append(self._hi); self.t_high.append(self._thi)
        self.low_since_high.append(self._lo); self.t_low_since_high.append(self._tlo)
        self.cum_vol.append(self._cv); self.cum_w.append(len(self._wallets))
        self.top_seller_share.append(max(self._sellers.values()) / self._sell_tot if self._sell_tot > 0 else 0.0)
        cre = self.creator
        self.creator_hold.append(max(0.0, self._bal.get(cre, 0.0)) / SUPPLY if cre else 0.0)
        self.bundle_hold.append(sum(max(0.0, self._bal[w]) for w in self._bundle) / SUPPLY)
        self.last_cre_sell.append(self._cre_sell)
        self._nw.append(self._nw[-1] + nw)
        self._pb.append(self._pb[-1] + (u if side_buy else 0.0)); self._ps.append(self._ps[-1] + (0.0 if side_buy else u))
        self._nb.append(self._nb[-1] + (1 if side_buy else 0)); self._ns.append(self._ns[-1] + (0 if side_buy else 1))
        return self.n - 1

    def win(self, i: int, w: int, lag: int = 0) -> dict:
        end_t = self.bt[i] - lag
        hi = bisect.bisect_right(self.bt, end_t, 0, i + 1)
        lo = bisect.bisect_right(self.bt, end_t - w, 0, hi)
        bu = self._pb[hi] - self._pb[lo]; su = self._ps[hi] - self._ps[lo]
        nb = self._nb[hi] - self._nb[lo]; ns = self._ns[hi] - self._ns[lo]
        return {"buy_usd": bu, "sell_usd": su, "nb": nb, "ns": ns, "n": nb + ns, "vol": bu + su,
                "new_buyers": self._nw[hi] - self._nw[lo], "lo": lo, "hi": hi}

    def steady(self, i: int) -> bool:
        a = self.win(i, 30); b = self.win(i, 60)
        if a["buy_usd"] <= a["sell_usd"] or b["buy_usd"] <= b["sell_usd"]:
            return False
        big = max((self.usd[k] for k in range(a["lo"], a["hi"]) if self.buy[k]), default=0.0)
        return big <= 0.35 * a["buy_usd"]

    def not_crash(self, i: int) -> bool:
        a = self.win(i, 10); b = self.win(i, 10, lag=10)
        accel = a["ns"] >= 2 * max(1, b["ns"]) and a["sell_usd"] > a["buy_usd"]
        return (not accel) and a["buy_usd"] >= 0.6 * a["sell_usd"]

    def uniq60(self, i: int) -> float:
        lo = bisect.bisect_right(self.bt, self.bt[i] - 60, 0, i + 1)
        n = i + 1 - lo
        return len({self.trader[k] for k in range(lo, i + 1)}) / n if n else 1.0

    def swing_low(self, i: int, secs: int = 30) -> float:
        lo = bisect.bisect_left(self.bt, self.bt[i] - secs, 0, i + 1)
        return min(self.spot[lo:i + 1])

    def features(self, i: int) -> dict:
        """Everything the entry rules need, known right after trade i (the last trade of its second)."""
        H = self.high[i]; L = self.low_since_high[i]; s = self.spot[i]
        w10 = self.win(i, 10); w15 = self.win(i, 15); w30 = self.win(i, 30)
        th = self.t_high[i]
        def ret(seconds: int):
            j = bisect.bisect_right(self.bt, self.bt[i] - seconds, 0, i + 1) - 1
            return (s / self.spot[j] - 1) if j >= 0 and self.spot[j] > 0 else None
        prev10 = self.win(i, 10, lag=10)
        large_sells = sum(
            1 for k in range(w10["lo"], w10["hi"])
            if not self.buy[k] and self.usd[k] >= max(1.0, 0.25 * w10["sell_usd"])
        )
        return {
            "i": i, "bt": self.bt[i], "t": self.t[i], "mc": s * SUPPLY, "spot": s,
            "dip": 1 - s / H if H else 0.0, "depth": 1 - L / H if H else 0.0,
            "secs_high": self.bt[i] - th, "high_t": th - (self.bt[i] - self.t[i]),
            "cn": i + 1, "cv": self.cum_vol[i], "cw": self.cum_w[i], "top1": self.top_seller_share[i],
            "notcrash": self.not_crash(i), "steady": self.steady(i), "dev_drop": self.last_cre_sell[i] >= th if self.creator else False,
            "creator_hold": self.creator_hold[i], "bundle_hold": self.bundle_hold[i], "uniq60": self.uniq60(i),
            "nw30": w30["new_buyers"], "n15": w15["n"], "v15": w15["vol"],
            "n10": w10["n"], "b10": w10["buy_usd"], "s10": w10["sell_usd"],
            "swing30": self.swing_low(i, 30),
            # Research-only context; these fields do not participate in decide()/guards().
            "ret5": ret(5), "ret10": ret(10), "ret20": ret(20), "ret30": ret(30),
            "buy_usd_10s_prev": prev10["buy_usd"], "sell_usd_10s_prev": prev10["sell_usd"],
            "buy_volume_accel_10s": w10["buy_usd"] - prev10["buy_usd"],
            "sell_volume_accel_10s": w10["sell_usd"] - prev10["sell_usd"],
            "large_sells_10s": large_sells,
        }
