#!/usr/bin/env python3
"""READ-ONLY check of pumpix's v3 account derivation against real mainnet transactions.

Fetches recent Pump program signatures, finds buy_v3 / buy_exact_quote_in_v3 / sell_v3 instructions, rebuilds the
17-account list with pumpix from (user, mint, quote mint, token programs, buyback recipient) and compares.
Uses RPC_HTTP_URL (default: public mainnet RPC). Sends nothing, signs nothing.
  python scripts/verify_layout.py [--max-tx 60]
"""
import argparse, base64, json, os, sys, time, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import base58
from pumpbot import pumpix as P

RPC = os.environ.get("RPC_HTTP_URL") or "https://api.mainnet-beta.solana.com"


def call(method, params):
    req = urllib.request.Request(RPC, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                 headers={"Content-Type": "application/json"})
    import time
    for k in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.loads(r.read())
            if "result" in j:
                return j["result"]
        except Exception:
            pass
        time.sleep(1.0 * (k + 1))
    return None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--max-tx", type=int, default=60); a = ap.parse_args()
    sigs = call("getSignaturesForAddress", [str(P.PUMP_PROGRAM), {"limit": 200}]) or []
    found = {k: 0 for k in P.DISC}; ok = {k: 0 for k in P.DISC}; bad = []
    inv = {v: k for k, v in P.DISC.items()}
    n = 0
    for s in sigs:
        if s.get("err") or n >= a.max_tx:
            continue
        n += 1
        time.sleep(0.25)
        tx = call("getTransaction", [s["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0}])
        if not tx:
            print(f"tx {n}: unavailable", file=sys.stderr, flush=True)
            continue
        msg = tx["transaction"]["message"]
        keys = msg["accountKeys"] + (tx["meta"].get("loadedAddresses") or {}).get("writable", []) + (tx["meta"].get("loadedAddresses") or {}).get("readonly", [])
        allix = list(msg["instructions"])
        for g in tx["meta"].get("innerInstructions") or []:
            allix += g["instructions"]
        for ix in allix:
            if keys[ix["programIdIndex"]] != str(P.PUMP_PROGRAM):
                continue
            data = base58.b58decode(ix["data"])
            name = inv.get(data[:8])
            if not name:
                continue
            found[name] += 1
            acc = [keys[i] for i in ix["accounts"]]
            user, mint, qm, btp, qtp = acc[8], acc[1], acc[2], acc[3], acc[4]
            qarg = "SOL" if qm == str(P.WSOL) else qm
            bb = acc[13]
            bidx = P.BUYBACK_FEE_RECIPIENTS.index(bb) if bb in P.BUYBACK_FEE_RECIPIENTS else None
            if bidx is None:  # token-paired: the ATA of one of the recipients
                for k, r in enumerate(P.BUYBACK_FEE_RECIPIENTS):
                    if str(P.ata(r, qm, qtp)) == bb:
                        bidx = k
            if bidx is None:
                bad.append((name, s["signature"][:12], "buyback recipient not in known list")); continue
            fn = {"buy_v3": P.buy_v3, "buy_exact_quote_in_v3": P.buy_exact_quote_in_v3, "sell_v3": P.sell_v3}[name]
            mine = fn(user, mint, 1, 1, qarg, btp, qtp, bidx)
            mk = [str(m.pubkey) for m in mine.accounts]
            if mk == acc[:17]:
                ok[name] += 1
            else:
                diff = [i for i in range(17) if i >= len(acc) or mk[i] != acc[i]]
                bad.append((name, s["signature"][:12], f"account index mismatch {diff}, onchain n={len(acc)}"))
    print(json.dumps({"rpc": RPC.split("?")[0].split("/")[2], "tx_checked": n, "found": found, "matched": ok, "mismatches": bad[:10]}, indent=1))
    sys.exit(0 if sum(found.values()) and not bad else 1)


if __name__ == "__main__":
    main()
