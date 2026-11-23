#!/usr/bin/env python3
"""RPC helpers for Helius plus optional standard-method endpoints (Alchemy, Chainstack, QuickNode).

Never logs API keys or endpoint URLs.

Helius keys use a per-key token-bucket limiter (~9 req/s each, tunable via
HELIUS_RPS_PER_KEY). A 429 cools down only that key; other keys keep serving.

Standard JSON-RPC methods (getTransaction, getSignaturesForAddress, batches,
getBlock, …) may also use Alchemy and Chainstack when those endpoints are
configured and pass a getSlot health check. Helius-only methods
(getTransactionsForAddress, getAsset) and the enhanced API always stay on Helius.

Failing extra endpoints are skipped and re-probed about every 10 minutes so
they can join the pool once Jeremy enables or fixes them, without a restart.
"""
from __future__ import annotations

import os
import re
import time
import random
import threading
from typing import Any

import requests

_local = threading.local()

_HELIUS_ONLY_METHODS = frozenset({
    "getTransactionsForAddress",
    "getAsset",  # Helius DAS; not standard Solana JSON-RPC
})

# Alchemy Solana CU costs (docs: compute-unit-costs). Used only for the Alchemy bucket.
_ALCHEMY_CU = {
    "getAccountInfo": 10,
    "getBalance": 10,
    "getBlock": 40,
    "getBlockTime": 20,
    "getHealth": 20,
    "getLatestBlockhash": 20,
    "getMultipleAccounts": 20,
    "getSignatureStatuses": 20,
    "getSignaturesForAddress": 40,
    "getSlot": 20,
    "getTransaction": 40,
    "getTransactionCount": 20,
}
_ALCHEMY_CU_DEFAULT = 20


def _session() -> requests.Session:
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=4, pool_maxsize=8, max_retries=0, pool_block=False,
        )
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _local.session = s
    return s


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _safe_err(exc: object) -> str:
    """Exception text with URLs and key-like query params stripped."""
    s = str(exc)
    for name in (
        "QUICKNODE_TOKEN", "QUICKNODE_BASE_URL", "ALCHEMY_RPC_URL", "ALCHEMY_API_KEY",
        "CHAINSTACK_RPC_URL", "HELIUS_API_KEY", "HELIUS_API_KEY_2", "HELIUS_API_KEY_3",
    ):
        v = os.environ.get(name) or ""
        if len(v) >= 8:
            s = s.replace(v, "<redacted>")
    s = re.sub(r"https?://\S+", "https://<redacted>", s)
    s = re.sub(r"api-key=[^&\s]+", "api-key=<redacted>", s, flags=re.I)
    s = re.sub(r"/v2/[A-Za-z0-9_\-]+", "/v2/<redacted>", s)
    return s[:200]


# ~9 rps per key × 3 keys ≈ 27 rps, leaving a little headroom under Helius caps.
_RPS = _env_float("HELIUS_RPS_PER_KEY", 9.0)
_BURST = _env_float("HELIUS_BURST_PER_KEY", 12.0)
_COOLDOWN = _env_float("HELIUS_429_COOLDOWN_SEC", 3.0)

# Alchemy free tier: 30M CU/month, 300 CU/s (10s window = 3000 CU), 15 RPS.
# getTransaction / getSignaturesForAddress = 40 CU → ~7.5/s CU-bound.
# 240 CU/s and an 800 CU burst (one 20-tx batch) leave headroom.
_ALCHEMY_CUPS = _env_float("ALCHEMY_CUPS", 240.0)
_ALCHEMY_BURST_CU = _env_float("ALCHEMY_BURST_CU", 800.0)
_ALCHEMY_COOLDOWN = _env_float("ALCHEMY_429_COOLDOWN_SEC", 5.0)

# Chainstack Solana mainnet Developer plan is 5 RPS; stay under until calibrated.
_CHAINSTACK_RPS = _env_float("CHAINSTACK_RPS", 4.0)
_CHAINSTACK_BURST = _env_float("CHAINSTACK_BURST", 6.0)
_CHAINSTACK_COOLDOWN = _env_float("CHAINSTACK_429_COOLDOWN_SEC", 3.0)

# QuickNode free trial: 15 RPS, credit-based (Solana standard methods = 30 credits).
# 12 rps / burst 15 leaves headroom under the 15 RPS cap. Multi-call batches
# consume N tokens so a 20-wide batch cannot blow the free RPS or credit budget;
# those batches stay on Helius/Alchemy.
_QUICKNODE_RPS = _env_float("QUICKNODE_RPS", 12.0)
_QUICKNODE_BURST = _env_float("QUICKNODE_BURST", 15.0)
_QUICKNODE_COOLDOWN = _env_float("QUICKNODE_429_COOLDOWN_SEC", 4.0)

_REPROBE_SEC = _env_float("RPC_EXTRA_REPROBE_SEC", 600.0)

_KEYS = [k for k in (os.environ.get(n) for n in ("HELIUS_API_KEY", "HELIUS_API_KEY_2", "HELIUS_API_KEY_3")) if k]


class _KeyBucket:
    def __init__(
        self,
        key: str,
        rps: float,
        burst: float,
        provider: str = "helius",
        cooldown: float | None = None,
        unit: str = "req",
        batch_ok: bool = True,
        url: str | None = None,
        batch_counts_n: bool = False,
    ):
        self.key = key
        self.provider = provider
        self.unit = unit  # "req" or "cu"
        self.rps = max(1.0, rps) if unit == "cu" else max(0.5, rps)
        self.burst = max(1.0, burst)
        self.tokens = self.burst
        self.last = time.monotonic()
        self.cool_until = 0.0
        self.cooldown_sec = _COOLDOWN if cooldown is None else cooldown
        self.batch_ok = batch_ok
        self.batch_counts_n = batch_counts_n
        self._url = url  # never logged
        self.healthy = provider == "helius"

    def refill(self, now: float) -> None:
        if now > self.last:
            self.tokens = min(self.burst, self.tokens + (now - self.last) * self.rps)
            self.last = now

    def make_url(self, kind: str = "rpc") -> str:
        if self.provider == "helius":
            if kind == "enhanced":
                return f"https://api.helius.xyz/v0/transactions?api-key={self.key}"
            return f"https://mainnet.helius-rpc.com/?api-key={self.key}"
        if self._url:
            return self._url
        if self.provider == "alchemy":
            return f"https://solana-mainnet.g.alchemy.com/v2/{self.key}"
        raise RuntimeError(f"{self.provider} endpoint has no URL")

    def __repr__(self) -> str:
        return f"<rpc:{self.provider} healthy={self.healthy} unit={self.unit}>"


_buckets = [_KeyBucket(k, _RPS, _BURST) for k in _KEYS]
_extras: list[_KeyBucket] = []
_block = threading.Lock()
_cond = threading.Condition(_block)
_held = threading.local()
_health_thread_started = False
_chainstack_skip_logged = False


def _cost_for(bucket: _KeyBucket, method: str | None = None, methods: list[str] | None = None) -> float:
    if bucket.unit == "cu":
        if methods:
            return float(sum(_ALCHEMY_CU.get(m, _ALCHEMY_CU_DEFAULT) for m in methods))
        return float(_ALCHEMY_CU.get(method or "", _ALCHEMY_CU_DEFAULT))
    # QuickNode free 15 RPS / credits: a 20-wide batch must not count as one token.
    if bucket.batch_counts_n and methods and len(methods) > 1:
        return float(len(methods))
    return 1.0


def _acquire_from(
    select_helius_only: bool,
    method: str | None = None,
    methods: list[str] | None = None,
    prefer_batch: bool = False,
    max_wait: float = 30.0,
) -> _KeyBucket:
    if not _buckets:
        raise RuntimeError("HELIUS_API_KEY is not set")
    deadline = time.monotonic() + max_wait
    with _cond:
        while True:
            now = time.monotonic()
            ready: list[tuple[_KeyBucket, float]] = []
            soonest = None
            cands = list(_buckets) if select_helius_only else (list(_buckets) + [b for b in _extras if b.healthy])
            if prefer_batch:
                batched = [b for b in cands if b.batch_ok]
                if batched:
                    cands = batched
            for b in cands:
                b.refill(now)
                cost = _cost_for(b, method, methods)
                if now < b.cool_until:
                    wait = b.cool_until - now
                    soonest = wait if soonest is None else min(soonest, wait)
                    continue
                if b.tokens >= cost:
                    ready.append((b, cost))
                else:
                    wait = (cost - b.tokens) / b.rps
                    soonest = wait if soonest is None else min(soonest, wait)
            if ready:
                # Compare fullness as a fraction so CU buckets and req buckets share work.
                b, cost = max(ready, key=lambda x: x[0].tokens / x[0].burst)
                b.tokens -= cost
                return b
            if now >= deadline:
                raise RuntimeError("RPC key limiter: no endpoint available")
            wait = 0.05 if soonest is None else soonest
            wait = min(max(0.001, wait), max(0.001, deadline - now), 1.0)
            _cond.wait(timeout=wait)


def _acquire_bucket(max_wait: float = 30.0) -> _KeyBucket:
    """Helius-only acquire (enhanced API, api_key(), helius-only methods)."""
    return _acquire_from(True, max_wait=max_wait)


def _cooldown(bucket: _KeyBucket, seconds: float | None = None) -> None:
    sec = bucket.cooldown_sec if seconds is None else seconds
    sec = max(0.5, sec) + random.random() * 0.5
    with _cond:
        bucket.cool_until = max(bucket.cool_until, time.monotonic() + sec)
        leftover = 1.0 if bucket.unit != "cu" else min(bucket.burst, _ALCHEMY_CU_DEFAULT)
        bucket.tokens = min(bucket.tokens, leftover)
        _cond.notify_all()


def api_key() -> str:
    """Take one Helius token and return a Helius key. Prefer helius_request() so 429s isolate."""
    b = _acquire_bucket()
    _held.bucket = b
    return b.key


def rpc_url(key: str | None = None) -> str:
    return f"https://mainnet.helius-rpc.com/?api-key={key or api_key()}"


def enhanced_tx_url(key: str | None = None) -> str:
    return f"https://api.helius.xyz/v0/transactions?api-key={key or api_key()}"


def enhanced_addr_url(addr: str, key: str | None = None) -> str:
    return f"https://api.helius.xyz/v0/addresses/{addr}/transactions?api-key={key or api_key()}"


def cooldown_last_key(seconds: float | None = None) -> None:
    """Cool down the key issued by the last api_key()/url helper on this thread."""
    b = getattr(_held, "bucket", None)
    if b is not None:
        _cooldown(b, seconds)


def _endpoint_post(bucket: _KeyBucket, json_body: Any, timeout: int, kind: str = "rpc") -> requests.Response:
    """POST to one endpoint. Never includes the URL in raised errors. Caller closes the response."""
    url = bucket.make_url(kind)
    try:
        r = _session().post(url, json=json_body, timeout=timeout)
    except requests.RequestException as e:
        _cooldown(bucket, 1.0)
        raise RuntimeError(f"{bucket.provider} network error: {_safe_err(e)}") from None
    if r.status_code == 429:
        ra = r.headers.get("Retry-After")
        sec = None
        try:
            if ra is not None:
                sec = float(ra)
        except (TypeError, ValueError):
            sec = None
        r.close()
        _cooldown(bucket, sec)
        raise RuntimeError("HTTP 429")
    if r.status_code in (500, 502, 503, 504):
        r.close()
        _cooldown(bucket, 1.5)
        raise RuntimeError(f"HTTP {r.status_code}")
    if r.status_code in (401, 403):
        r.close()
        if bucket.provider != "helius":
            with _cond:
                bucket.healthy = False
                _cond.notify_all()
            print(
                f"rpc: {bucket.provider} returned HTTP {r.status_code}; "
                f"removed from pool until the next health re-probe",
                flush=True,
            )
        raise RuntimeError(f"HTTP {r.status_code}")
    return r


def helius_request(kind: str, json_body: Any, timeout: int = 30, retries: int = 6) -> requests.Response:
    """POST to Helius RPC or enhanced API with per-key limiting and isolated 429 cool-down.

    kind: 'rpc' or 'enhanced'
    """
    last = None
    for _ in range(retries):
        b = _acquire_bucket()
        if kind not in ("enhanced", "rpc"):
            raise ValueError(f"unknown Helius kind {kind}")
        try:
            return _endpoint_post(b, json_body, timeout, kind=kind)
        except RuntimeError as e:
            last = e
            if "HTTP 429" not in str(e) and "HTTP 50" not in str(e) and "network error" not in str(e):
                # Auth or unexpected errors: don't spin all retries on the same failure.
                if "HTTP 401" in str(e) or "HTTP 403" in str(e):
                    continue
            continue
    raise RuntimeError(f"Helius {kind} request failed after retries: {_safe_err(last)}")


def rpc_call(method: str, params: Any, timeout: int = 30, retries: int = 6) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    last = None
    helius_only = method in _HELIUS_ONLY_METHODS
    for _ in range(max(1, retries)):
        try:
            b = _acquire_from(helius_only, method=method)
            r = _endpoint_post(b, payload, timeout)
            try:
                r.raise_for_status()
                data = r.json()
            finally:
                r.close()
            if "error" in data:
                err = data["error"]
                msg = str(err)
                if any(s in msg.lower() for s in ("invalid", "not found", "could not", "-32602", "-32601")):
                    raise RuntimeError(f"RPC {method} error: {err}")
                last = RuntimeError(f"RPC {method} error: {err}")
                time.sleep(0.15)
                continue
            return data["result"]
        except (requests.RequestException, RuntimeError) as e:
            last = e
            if "RPC " in str(e) and "error:" in str(e) and "HTTP 429" not in str(e):
                if any(s in str(e).lower() for s in ("invalid", "not found", "-32602", "-32601")):
                    raise
            continue
    raise RuntimeError(f"RPC {method} failed after retries: {_safe_err(last)}")


def _parse_batch(data: Any, n: int) -> list[Any]:
    if not isinstance(data, list):
        raise RuntimeError("batch response is not a list")
    by_id = {item.get("id"): item for item in data if isinstance(item, dict)}
    out = []
    for j in range(n):
        item = by_id.get(j) or {}
        if "error" in item:
            out.append({"_error": item["error"]})
        else:
            out.append(item.get("result"))
    return out


def rpc_batch(calls: list[tuple[str, Any]], timeout: int = 45, retries: int = 6) -> list[Any]:
    payload = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)]
    methods = [m for m, _ in calls]
    helius_only = any(m in _HELIUS_ONLY_METHODS for m in methods)
    last = None
    for _ in range(retries):
        try:
            b = _acquire_from(helius_only, methods=methods, prefer_batch=True)
            if not b.batch_ok:
                # Provider rejected batches earlier; do singles (each call acquires its own token).
                return [rpc_call(m, p, timeout=timeout, retries=max(2, retries // 2)) for m, p in calls]
            r = _endpoint_post(b, payload, timeout)
            try:
                r.raise_for_status()
                data = r.json()
            finally:
                r.close()
            try:
                return _parse_batch(data, len(calls))
            except RuntimeError:
                if b.provider != "helius":
                    b.batch_ok = False
                    last = RuntimeError(f"{b.provider} does not support JSON-RPC batches; falling back to singles")
                    continue
                raise
        except (requests.RequestException, RuntimeError) as e:
            last = e
            continue
    raise RuntimeError(f"RPC batch failed after retries: {_safe_err(last)}")


# ---------------------------------------------------------------------------
# Extra endpoints (Alchemy, Chainstack, QuickNode): health check + periodic re-probe
# ---------------------------------------------------------------------------

def _https_env(name: str) -> str | None:
    v = os.environ.get(name) or ""
    return v if v.startswith("https://") else None


def _find_extra(provider: str) -> _KeyBucket | None:
    for b in _extras:
        if b.provider == provider:
            return b
    return None


def _alchemy_bucket_from_env() -> _KeyBucket | None:
    url = _https_env("ALCHEMY_RPC_URL")
    if url:
        return _KeyBucket(
            "", _ALCHEMY_CUPS, _ALCHEMY_BURST_CU,
            provider="alchemy", cooldown=_ALCHEMY_COOLDOWN, unit="cu", url=url,
        )
    key = os.environ.get("ALCHEMY_API_KEY") or ""
    if key:
        return _KeyBucket(
            key, _ALCHEMY_CUPS, _ALCHEMY_BURST_CU,
            provider="alchemy", cooldown=_ALCHEMY_COOLDOWN, unit="cu",
        )
    return None


_quicknode_skip_logged = False


def _quicknode_bucket_from_env() -> _KeyBucket | None:
    """Build the QuickNode bucket. URL is base.rstrip('/') + '/' + token + '/' (never logged)."""
    global _quicknode_skip_logged
    base = os.environ.get("QUICKNODE_BASE_URL") or ""
    token = os.environ.get("QUICKNODE_TOKEN") or ""
    if base.startswith("https://") and token:
        url = base.rstrip("/") + "/" + token + "/"
        return _KeyBucket(
            "", _QUICKNODE_RPS, _QUICKNODE_BURST,
            provider="quicknode", cooldown=_QUICKNODE_COOLDOWN, unit="req",
            url=url, batch_counts_n=True,
        )
    if (base or token) and not _quicknode_skip_logged:
        _quicknode_skip_logged = True
        why = "QUICKNODE_BASE_URL is not an https:// base" if not base.startswith("https://") else "QUICKNODE_TOKEN is missing"
        print(f"rpc: QuickNode skipped ({why}).", flush=True)
    return None


def _chainstack_bucket_from_env() -> _KeyBucket | None:
    global _chainstack_skip_logged
    url = _https_env("CHAINSTACK_RPC_URL")
    if url:
        return _KeyBucket(
            "", _CHAINSTACK_RPS, _CHAINSTACK_BURST,
            provider="chainstack", cooldown=_CHAINSTACK_COOLDOWN, unit="req", url=url,
        )
    raw = os.environ.get("CHAINSTACK_RPC_URL") or ""
    if raw and not _chainstack_skip_logged:
        _chainstack_skip_logged = True
        print(
            "rpc: CHAINSTACK_RPC_URL is not an https:// endpoint URL; skipped. "
            "Provide the full Solana mainnet HTTPS URL (a token alone is not enough; "
            "do not use a generic Chainstack host).",
            flush=True,
        )
    return None


def _probe_getslot(bucket: _KeyBucket, timeout: int = 8) -> tuple[bool, str]:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getSlot", "params": []}
    try:
        r = _session().post(bucket.make_url(), json=payload, timeout=timeout)
    except requests.RequestException as e:
        return False, f"network {type(e).__name__}"
    code = r.status_code
    try:
        data = r.json() if r.content else {}
    except Exception:
        data = {}
    finally:
        r.close()
    if code == 200 and isinstance(data, dict) and "result" in data and "error" not in data:
        return True, "getSlot ok"
    reason = f"HTTP {code}"
    if isinstance(data, dict):
        err = data.get("error")
        msg = ""
        if isinstance(err, dict):
            msg = str(err.get("message") or err.get("code") or "")
        elif data.get("message"):
            msg = str(data.get("message"))
        msg = _safe_err(msg)
        msg = msg.strip()[:80]
        if msg:
            reason = f"HTTP {code}: {msg}"
    return False, reason


def _probe_batch_support(bucket: _KeyBucket, timeout: int = 8) -> bool:
    payload = [
        {"jsonrpc": "2.0", "id": 0, "method": "getSlot", "params": []},
        {"jsonrpc": "2.0", "id": 1, "method": "getSlot", "params": []},
    ]
    try:
        r = _session().post(bucket.make_url(), json=payload, timeout=timeout)
        try:
            if r.status_code != 200:
                return False
            data = r.json()
        finally:
            r.close()
        return isinstance(data, list) and len(data) == 2 and all(
            isinstance(x, dict) and "result" in x for x in data
        )
    except Exception:
        return False


def _set_healthy(bucket: _KeyBucket, ok: bool) -> None:
    with _cond:
        bucket.healthy = ok
        if ok:
            bucket.tokens = bucket.burst
            bucket.cool_until = 0.0
        _cond.notify_all()


def _sync_extras_from_env() -> None:
    """Create extra buckets from the current env if they are not already in the list."""
    wanted = []
    a = _alchemy_bucket_from_env()
    if a:
        wanted.append(a)
    q = _quicknode_bucket_from_env()
    if q:
        wanted.append(q)
    c = _chainstack_bucket_from_env()
    if c:
        wanted.append(c)
    with _cond:
        have = {b.provider for b in _extras}
        for b in wanted:
            if b.provider not in have:
                b.healthy = False
                _extras.append(b)


def _probe_extras(reason: str = "probe") -> None:
    _sync_extras_from_env()
    for b in list(_extras):
        ok, detail = _probe_getslot(b)
        was = b.healthy
        if ok and b.batch_ok:
            # Confirm batch once when first becoming healthy (cheap: two getSlot).
            if not was:
                b.batch_ok = _probe_batch_support(b)
        _set_healthy(b, ok)
        if ok and not was:
            batch = "JSON-RPC batch ok" if b.batch_ok else "no batch; singles only"
            if b.unit == "cu":
                cap = f"{b.rps:.0f} CU/s burst={b.burst:.0f} CU"
            else:
                cap = f"{b.rps:.1f} req/s burst={b.burst:.0f}"
            print(f"rpc: {b.provider} healthy ({detail}); added to standard-method pool at {cap} ({batch}) [{reason}]", flush=True)
        elif not ok and was:
            print(f"rpc: {b.provider} failed health check ({detail}); removed from pool until re-probe [{reason}]", flush=True)
        elif not ok and reason in ("startup", "probe"):
            print(f"rpc: {b.provider} unhealthy ({detail}); skipped until re-probe [{reason}]", flush=True)


def _health_loop() -> None:
    while True:
        time.sleep(max(30.0, _REPROBE_SEC))
        try:
            _probe_extras(reason="reprobe")
        except Exception as e:
            print(f"rpc: extra-endpoint re-probe error: {type(e).__name__}: {_safe_err(e)}", flush=True)


def rpc_pool_status() -> list[dict[str, Any]]:
    """Secret-free snapshot of the RPC pool (for logs/reports)."""
    out = []
    with _cond:
        for b in list(_buckets) + list(_extras):
            out.append({
                "provider": b.provider,
                "healthy": b.healthy,
                "unit": b.unit,
                "rps": b.rps,
                "burst": b.burst,
                "batch_ok": b.batch_ok,
            })
    return out


def _start_extra_endpoints() -> None:
    global _health_thread_started
    _sync_extras_from_env()
    try:
        _probe_extras(reason="startup")
    except Exception as e:
        print(f"rpc: extra-endpoint startup probe error: {type(e).__name__}: {_safe_err(e)}", flush=True)
    if not _health_thread_started:
        t = threading.Thread(target=_health_loop, name="rpc-extra-health", daemon=True)
        t.start()
        _health_thread_started = True


_start_extra_endpoints()
