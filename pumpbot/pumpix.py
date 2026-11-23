"""Pump.fun bonding-curve instruction builders (v3 trades: buy_exact_quote_in_v3, buy_v3, sell_v3).

Source of truth: github.com/pump-fun/pump-public-docs, docs/instructions/TRADE_V3.md and idl/pump.json
(fetched Nov 23 2026; a copy of the IDL is in pump_idl.json). v3 takes 17 accounts and keeps protocol and creator
fees on the curve, so it does not need the rotating protocol fee recipients (rotation cut-off Nov 25 2026).
Cashback coins are NOT supported by v3 (error 6094): the bot skips them.

Nothing here signs or sends; it only builds instructions. scripts/verify_layout.py checks the derived accounts
against real mainnet v3 transactions (read-only).
"""
from __future__ import annotations

import struct

from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer

PUMP_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
PUMP_FEES_PROGRAM = Pubkey.from_string("pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ")
TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
TOKEN_2022 = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SYSTEM_PROGRAM = Pubkey.from_string("11111111111111111111111111111111")
COMPUTE_BUDGET = Pubkey.from_string("ComputeBudget111111111111111111111111111111")
WSOL = Pubkey.from_string("So11111111111111111111111111111111111111112")
FEE_CONFIG_ADDR = "8Wf5TiAheLUqBrKXeYg2JtAFFMWtKdG2BSFgqUcPVwTt"

DISC = {
    "buy_v3": bytes.fromhex("07051dc4f5176550"),
    "buy_exact_quote_in_v3": bytes.fromhex("e1f7501ed5b38488"),
    "sell_v3": bytes.fromhex("1c92de7726c469d5"),
}
BUYBACK_FEE_RECIPIENTS = [
    "5YxQFdt3Tr9zJLvkFccqXVUwhdTWJQc1fFg2YPbxvxeD", "9M4giFFMxmFGXtc3feFzRai56WbBqehoSeRE5GK7gf7",
    "GXPFM2caqTtQYC2cJ5yJRi9VDkpsYZXzYdwYpGnLmtDL", "3BpXnfJaUTiwXnJNe7Ej1rcbzqTTQUvLShZaWazebsVR",
    "5cjcW9wExnJJiqgLjq7DEG75Pm6JBgE1hNv4B2vHXUW6", "EHAAiTxcdDwQ3U4bU6YcMsQGaekdzLS3B5SmYo46kJtL",
    "5eHhjP8JaYkz83CWwvGU2uMUXefd3AazWGx4gpcuEEYD", "A7hAgCzFw14fejgCp387JUJRMNyz4j89JKnhtKU8piqW",
]
JITO_TIP_ACCOUNTS = [
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5", "HFqU5x63VTqvQss8hp11i6wDX8v8JmSSt1bjwccmEzy",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY", "ADaUMid9yfU4u3TNqgsRVYhDR4sPAa2p5SGw1oA7hM",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh", "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL", "3AVi9Tg9Uo68YzkMaExM2QeS2p16rUNDfLNaoTWpT4v",
]


def pk(x) -> Pubkey:
    return x if isinstance(x, Pubkey) else Pubkey.from_string(x)


def pda(seeds: list[bytes], program: Pubkey = PUMP_PROGRAM) -> Pubkey:
    return Pubkey.find_program_address(seeds, program)[0]


def global_pda() -> Pubkey: return pda([b"global"])
def bonding_curve_pda(mint) -> Pubkey: return pda([b"bonding-curve", bytes(pk(mint))])
def event_authority_pda() -> Pubkey: return pda([b"__event_authority"])
def user_volume_accumulator_pda(user) -> Pubkey: return pda([b"user_volume_accumulator", bytes(pk(user))])
def fee_config_pda() -> Pubkey: return pda([b"fee_config", bytes(PUMP_PROGRAM)], PUMP_FEES_PROGRAM)


def ata(owner, mint, token_program=TOKEN_PROGRAM) -> Pubkey:
    return pda([bytes(pk(owner)), bytes(pk(token_program)), bytes(pk(mint))], ATA_PROGRAM)


# ---------------------------------------------------------------- bonding curve account
def parse_bonding_curve(data: bytes) -> dict:
    """BondingCurve account (IDL order). quote_mint sits at byte 83 (matches the research decoder)."""
    if len(data) < 115:
        raise ValueError("bonding curve account too short")
    o = 8
    vt, vq, rt, rq, sup = struct.unpack_from("<QQQQQ", data, o); o += 40
    complete = data[o] == 1; o += 1
    creator = Pubkey.from_bytes(data[o:o + 32]); o += 32
    mayhem = data[o] == 1; o += 1
    cashback = data[o] == 1; o += 1
    qm = Pubkey.from_bytes(data[o:o + 32]); o += 32
    out = {"virtual_token_reserves": vt, "virtual_quote_reserves": vq, "real_token_reserves": rt,
           "real_quote_reserves": rq, "token_total_supply": sup, "complete": complete, "creator": str(creator),
           "is_mayhem_mode": mayhem, "is_cashback_coin": cashback,
           "quote_mint": "SOL" if qm in (Pubkey.default(), WSOL) else str(qm)}
    if len(data) >= o + 8:
        out["creator_fee_bps"] = struct.unpack_from("<Q", data, o)[0]
    return out


# ---------------------------------------------------------------- curve math (constant product on virtual reserves)
def expected_tokens_out(spend_quote: int, vq: int, vt: int, total_fee_bps: int) -> int:
    """Tokens for an exact-quote-in buy; fees are taken from the input."""
    net = spend_quote * 10_000 // (10_000 + total_fee_bps)
    return vt * net // (vq + net) if vq + net > 0 else 0


def expected_quote_out(tokens: int, vq: int, vt: int, total_fee_bps: int) -> int:
    gross = vq * tokens // (vt + tokens) if vt + tokens > 0 else 0
    return gross - (gross * total_fee_bps + 9_999) // 10_000


# ---------------------------------------------------------------- instructions
def _v3_accounts(user, mint, quote_mint, base_token_program, quote_token_program, buyback_recipient) -> list[AccountMeta]:
    user, mint = pk(user), pk(mint)
    qmint = WSOL if quote_mint in ("SOL", None) else pk(quote_mint)
    btp, qtp = pk(base_token_program), pk(quote_token_program)
    curve = bonding_curve_pda(mint)
    bb = pk(buyback_recipient)
    if qmint != WSOL:  # token-paired coins pay buyback into the recipient's ATA for the quote mint
        bb = ata(bb, qmint, qtp)
    return [
        AccountMeta(global_pda(), False, False),
        AccountMeta(mint, False, False),
        AccountMeta(qmint, False, False),
        AccountMeta(btp, False, False),
        AccountMeta(qtp, False, False),
        AccountMeta(curve, False, True),
        AccountMeta(ata(curve, mint, btp), False, True),
        AccountMeta(ata(curve, qmint, qtp), False, True),
        AccountMeta(user, True, True),
        AccountMeta(ata(user, mint, btp), False, True),
        AccountMeta(ata(user, qmint, qtp), False, True),
        AccountMeta(user_volume_accumulator_pda(user), False, True),
        AccountMeta(fee_config_pda(), False, False),
        AccountMeta(bb, False, True),
        AccountMeta(SYSTEM_PROGRAM, False, False),
        AccountMeta(event_authority_pda(), False, False),
        AccountMeta(PUMP_PROGRAM, False, False),
    ]


def buy_exact_quote_in_v3(user, mint, spend: int, min_tokens_out: int, quote_mint="SOL",
                          base_token_program=TOKEN_2022, quote_token_program=TOKEN_PROGRAM, buyback_index: int = 0) -> Instruction:
    data = DISC["buy_exact_quote_in_v3"] + struct.pack("<QQ", spend, min_tokens_out)
    return Instruction(PUMP_PROGRAM, data, _v3_accounts(user, mint, quote_mint, base_token_program, quote_token_program,
                                                        BUYBACK_FEE_RECIPIENTS[buyback_index % 8]))


def buy_v3(user, mint, amount: int, max_quote_cost: int, quote_mint="SOL", base_token_program=TOKEN_2022,
           quote_token_program=TOKEN_PROGRAM, buyback_index: int = 0) -> Instruction:
    data = DISC["buy_v3"] + struct.pack("<QQ", amount, max_quote_cost)
    return Instruction(PUMP_PROGRAM, data, _v3_accounts(user, mint, quote_mint, base_token_program, quote_token_program,
                                                        BUYBACK_FEE_RECIPIENTS[buyback_index % 8]))


def sell_v3(user, mint, amount: int, min_quote_out: int, quote_mint="SOL", base_token_program=TOKEN_2022,
            quote_token_program=TOKEN_PROGRAM, buyback_index: int = 0) -> Instruction:
    data = DISC["sell_v3"] + struct.pack("<QQ", amount, min_quote_out)
    return Instruction(PUMP_PROGRAM, data, _v3_accounts(user, mint, quote_mint, base_token_program, quote_token_program,
                                                        BUYBACK_FEE_RECIPIENTS[buyback_index % 8]))


def create_ata_idempotent(payer, owner, mint, token_program=TOKEN_2022) -> Instruction:
    payer, owner, mint, tp = pk(payer), pk(owner), pk(mint), pk(token_program)
    return Instruction(ATA_PROGRAM, bytes([1]), [
        AccountMeta(payer, True, True), AccountMeta(ata(owner, mint, tp), False, True), AccountMeta(owner, False, False),
        AccountMeta(mint, False, False), AccountMeta(SYSTEM_PROGRAM, False, False), AccountMeta(tp, False, False)])


def close_token_account(owner, mint, token_program=TOKEN_2022) -> Instruction:
    """Close the (empty) token account after selling everything: returns ~0.002 SOL rent to the wallet."""
    owner, tp = pk(owner), pk(token_program)
    acc = ata(owner, mint, tp)
    return Instruction(tp, bytes([9]), [AccountMeta(acc, False, True), AccountMeta(owner, False, True), AccountMeta(owner, True, False)])


def compute_budget(cu_limit: int, micro_lamports: int) -> list[Instruction]:
    return [Instruction(COMPUTE_BUDGET, bytes([2]) + struct.pack("<I", cu_limit), []),
            Instruction(COMPUTE_BUDGET, bytes([3]) + struct.pack("<Q", micro_lamports), [])]


def jito_tip(payer, lamports: int, idx: int = 0) -> Instruction:
    return transfer(TransferParams(from_pubkey=pk(payer), to_pubkey=pk(JITO_TIP_ACCOUNTS[idx % 8]), lamports=lamports))
