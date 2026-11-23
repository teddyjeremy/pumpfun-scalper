#!/usr/bin/env python3
"""Full scan of Pump.fun bonding curves (dataSize 166) via Helius getProgramAccountsV2 (paginated, 10k/page),
dataSlice 48..115: complete flag (48), creator (49..81), quote mint (83..115; System Program = SOL).
Writes curves_scan.jsonl.gz-style lines: curve, complete, quote ('SOL' or mint). Throttled. Resumable."""
import base64, gzip, json, sys, time
from pathlib import Path
import base58
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import qrpc
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
OUT = HERE / "curves_scan.jsonl"; STATE = HERE / "curves_scan.state.json"
st = json.loads(STATE.read_text()) if STATE.exists() else {"key": None, "pages": 0, "n": 0, "done": False}
qrpc.MIN_GAP = 0.6
t = time.time()
with open(OUT, "a") as f:
    while not st["done"]:
        p = {"encoding": "base64", "limit": 10000, "dataSlice": {"offset": 48, "length": 67}, "filters": [{"dataSize": 166}]}
        if st["key"]: p["paginationKey"] = st["key"]
        r = qrpc.call("getProgramAccountsV2", [PUMP, p], kind=["helius3", "helius"], timeout=180)
        lines = []
        for a in r.get("accounts") or []:
            d = base64.b64decode(a["account"]["data"][0])
            q = base58.b58encode(d[35:67]).decode()
            lines.append(json.dumps({"c": a["pubkey"], "x": d[0], "q": "SOL" if q == "11111111111111111111111111111111" else q}))
        if lines: f.write("\n".join(lines) + "\n"); f.flush()
        st["key"] = r.get("paginationKey"); st["pages"] += 1; st["n"] += len(lines)
        if not st["key"]: st["done"] = True
        STATE.write_text(json.dumps(st))
        if st["pages"] % 50 == 0: print("pages", st["pages"], "curves", st["n"], f"{time.time()-t:.0f}s", flush=True)
print("DONE", st, flush=True)
