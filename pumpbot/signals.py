"""Entry rules. Lane B = v2 volume quick-flip ('steady + fresh'); Lane A = v1.1-v1.3 quality dip.

See RULES.md. Every rule is causal (uses only trades up to the decision point).
"""
from __future__ import annotations


def in_window(f: dict, cfg) -> bool:
    return cfg.entry_min_t < f["t"] <= cfg.entry_max_t and cfg.mcap_min <= f["mc"] <= cfg.mcap_max


def guards(f: dict, cfg, rug_bundle: bool = True) -> str | None:
    """Standing guards (Jeremy's hard rules). Returns a skip reason or None."""
    if not in_window(f, cfg):
        return "window_or_band"
    if f["cn"] < cfg.min_cum_trades:
        return "cum_trades_lt_300"
    if not f["notcrash"]:
        return "crash"
    if f["dev_drop"]:
        return "v1.3_dev_sold_since_high"
    if not (f["top1"] <= 0.5 or f["dip"] < 0.1):
        return "v1.1_top_seller_gt_50pct"
    if f["creator_hold"] > cfg.creator_hold_max:
        return "rug_creator_hold"
    if rug_bundle and f["bundle_hold"] > cfg.bundle_hold_max:
        return "rug_create_slot_wallets"
    return None


def lane_b(f: dict, cfg) -> bool:
    """Steady curve + fresh buyers + anti-wash."""
    return bool(f["steady"]) and f["nw30"] >= 15 and f["uniq60"] >= cfg.uniq60_min


def lane_a(f: dict, cfg) -> bool:
    """Strict v0 mid-tape dip with v1.1/v1.2/v1.3 skips, restricted to the $5-10k entry band."""
    if f["high_t"] < 10:                       # the high must be mid-tape (after t+10s)
        return False
    if f["dip"] < 0.30:
        return False
    if f["n15"] < 8 or f["v15"] < 200:
        return False
    if f["cv"] < 10_000 or f["cn"] < 300 or f["cw"] < 150:
        return False
    if f["top1"] > 0.5:                        # v1.1
        return False
    if f["secs_high"] > 28:                    # v1.2
        return False
    if f["dev_drop"]:                          # v1.3
        return False
    return bool(f["notcrash"])


LANES = {"A": lane_a, "B": lane_b}


def decide(f: dict, cfg, lanes: list[str], rug_bundle: bool = True) -> tuple[str | None, str]:
    """Return (lane, reason). lane None = no entry."""
    why = guards(f, cfg, rug_bundle)
    if why:
        return None, why
    for ln in lanes:
        fn = LANES.get(ln)
        if fn and fn(f, cfg):
            return ln, "signal"
    return None, "no_signal"
