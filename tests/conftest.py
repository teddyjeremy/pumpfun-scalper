import glob
import gzip
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research_ref"))
FIX = os.path.join(ROOT, "fixtures")


def fixture_tapes():
    """Research tapes for equivalence tests: TAPES_DIR (full cache) or ./fixtures. Returns list of (path, kind)."""
    d = os.environ.get("TAPES_DIR")
    ps = sorted(glob.glob(os.path.join(d, "*", "*.json.gz"))) if d else []
    if not ps:
        ps = sorted(p for p in glob.glob(os.path.join(FIX, "*.json.gz")) if not os.path.basename(p).startswith("paper_"))
    return ps


def load(p):
    with gzip.open(p, "rt") as f:
        return json.load(f)


def synth_trades(t0=1_000_000, n_sec=200, start_spot=5.5e-6, pattern=None, creator="CRE", slot0=100):
    """Deterministic synthetic tape: lots of fresh buyers, steady inflow; pattern(sec)-> spot multiplier."""
    tr = []
    k = 0
    for s in range(n_sec):
        mult = pattern(s) if pattern else 1.0 + 0.0005 * s
        for q in range(4):
            side = "buy" if q < 3 else "sell"
            k += 1
            tr.append({"bt": t0 + s, "t": s, "side": side, "usd": 20.0 if side == "buy" else 10.0,
                       "spot": start_spot * mult, "trader": f"W{k}", "tok": 1000.0, "sol": 0.1, "slot": slot0 + 3 + s * 2})
    tr.insert(0, {"bt": t0, "t": 0, "side": "buy", "usd": 5.0, "spot": start_spot, "trader": creator, "tok": 1000.0, "sol": 0.05, "slot": slot0})
    return tr
