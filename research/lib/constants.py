"""Shared constants for Pump.fun study."""
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_SWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
WSOL = "So11111111111111111111111111111111111111112"
SYSTEM = "11111111111111111111111111111111"

# Official Pump.fun bonding-curve fee (docs updated 16 Nov 2026)
CURVE_FEE_BPS = 125  # 1.25% total
CURVE_PROTOCOL_FEE_BPS = 95
CURVE_CREATOR_FEE_BPS = 30

# Classic SOL-pair virtual reserves / graduation (1e9 tokens, 6 decimals)
INITIAL_VIRTUAL_SOL = 30_000_000_000
INITIAL_VIRTUAL_TOKEN = 1_073_000_191_000_000
INITIAL_REAL_TOKEN = 793_100_000_000_000
TOTAL_SUPPLY_UI = 1_000_000_000
TOKEN_DECIMALS = 6
GRADUATION_SOL = 85.0  # used for curve progress %; also report raw real SOL

# Known Jito tip accounts
JITO_TIPS = {
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i6wDX8v8JmSSt1bjwccmEzy",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfU4u3TNqgsRVYhDR4sPAa2p5SGw1oA7hM",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "3AVi9Tg9Uo68YzkMaExM2QeS2p16rUNDfLNaoTWpT4v",
}

# Anchor event discriminators: sha256("event:Name")[:8]
TRADE_EVENT_DISC = bytes.fromhex("bddb7fd34ee661ee")
CREATE_EVENT_DISC = bytes.fromhex("1b72a94ddeeb6376")
COMPLETE_EVENT_DISC = bytes.fromhex("5f72619cd42e9808")

# Retail $1 scalp assumptions (overridden if we can measure typical tips)
DEFAULT_PRIORITY_SOL = 0.0002  # per tx; stated in reports
DEFAULT_BASE_FEE_SOL = 0.000005

WINDOW_SEC = 30 * 60
PREFERRED_SEC = 5 * 60
SNIPER_SLOTS = 3  # creation slot + next 3? we use slot0..slot0+3 inclusive = 4 slots; report both 0-2 and 0-3
SNIPER_SECONDS = 5

# Volume-still-active definition used for dip entries
ACTIVE_LOOKBACK_SEC = 15
ACTIVE_MIN_TRADES = 8
ACTIVE_MIN_USD = 200.0
