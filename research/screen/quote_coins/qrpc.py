"""Own throttled RPC client for quote-coin research (does not touch lib/rpc state). Never prints keys/URLs."""
import os, time, threading, requests, random
_lock = threading.Lock(); _last = [0.0]
MIN_GAP = float(os.environ.get("QC_MIN_GAP", "0.5"))  # <=2 calls/s total
def _url(kind):
    if kind == "quicknode" and os.environ.get("QUICKNODE_BASE_URL") and os.environ.get("QUICKNODE_TOKEN"):
        return os.environ["QUICKNODE_BASE_URL"].rstrip("/") + "/" + os.environ["QUICKNODE_TOKEN"] + "/"
    if kind == "helius3" and os.environ.get("HELIUS_API_KEY_3"):
        return "https://mainnet.helius-rpc.com/?api-key=" + os.environ["HELIUS_API_KEY_3"]
    if kind == "alchemy" and os.environ.get("ALCHEMY_RPC_URL"):
        return os.environ["ALCHEMY_RPC_URL"]
    k = os.environ.get("HELIUS_API_KEY_2") or os.environ.get("HELIUS_API_KEY_3") or os.environ["HELIUS_API_KEY"]
    return "https://mainnet.helius-rpc.com/?api-key=" + k
def _throttle():
    with _lock:
        now = time.time(); w = _last[0] + MIN_GAP - now
        if w > 0: time.sleep(w)
        _last[0] = time.time()
def call(method, params, kind="helius", timeout=60, retries=8):
    kinds = kind if isinstance(kind, (list, tuple)) else [kind]
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    back = 2.0
    for a in range(retries):
        _throttle()
        try:
            r = requests.post(_url(kinds[a % len(kinds)]), json=body, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                print(f"qrpc {method} {kinds[a % len(kinds)]} http {r.status_code} backoff {back:.0f}s", flush=True)
                time.sleep(back + random.random()); back = min(back * 2, 60); continue
            j = r.json()
            if "error" in j:
                msg = str(j["error"])[:200]
                if "rate" in msg.lower() or "429" in msg:
                    time.sleep(back); back = min(back * 2, 60); continue
                raise RuntimeError("rpc error: " + msg)
            return j.get("result")
        except (requests.RequestException, ValueError) as e:
            print(f"qrpc {method} exc {type(e).__name__} backoff {back:.0f}s", flush=True)
            time.sleep(back); back = min(back * 2, 60)
    raise RuntimeError(f"rpc failed {method}")
