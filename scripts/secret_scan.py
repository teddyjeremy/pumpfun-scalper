#!/usr/bin/env python3
"""Scan a directory for secret-looking strings before pushing. Prints file:line and the rule only (never the value).
Also checks that no env var VALUE (for names that look secret) appears in any file. Exit 1 if anything is found."""
import os, re, sys

root = sys.argv[1] if len(sys.argv) > 1 else "."
RULES = {
    "api-key-in-url": re.compile(r"api[-_]?key=[A-Za-z0-9_\-]{8,}", re.I),
    "token-in-path": re.compile(r"(quiknode\.pro|solana-mainnet\.core\.chainstack\.com|g\.alchemy\.com/v2)/[A-Za-z0-9_\-]{16,}", re.I),
    "bearer": re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{20,}"),
    "bitquery-token": re.compile(r"ory_at_[A-Za-z0-9_\-\.]{10,}"),
    "uuid-like-key": re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),
    "private-key-b58-87-88": re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{86,90}\b"),
    "json-keypair": re.compile(r"\[\s*(\d{1,3}\s*,\s*){63}\d{1,3}\s*\]"),
    "telegram-token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b"),
}
# known public 88-char strings allowed (Solana tx signatures in docs are public, but we flag them anyway for review)
secret_vals = []
for k, v in os.environ.items():
    if re.search(r"KEY|TOKEN|SECRET|URL|PRIVATE|PASS", k) and v and len(v) >= 12:
        secret_vals.append((k, v))
hits = 0
for dp, dn, fn in os.walk(root):
    if ".git" in dp.split(os.sep):
        continue
    for f in fn:
        p = os.path.join(dp, f)
        try:
            txt = open(p, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        for k, v in secret_vals:
            if v in txt:
                print(f"{p}: contains the VALUE of env var {k}"); hits += 1
        for ln, line in enumerate(txt.splitlines(), 1):
            for name, rx in RULES.items():
                if rx.search(line):
                    print(f"{p}:{ln}: {name}"); hits += 1
print(f"secret_scan: {hits} finding(s)")
sys.exit(1 if hits else 0)
