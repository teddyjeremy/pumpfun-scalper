import hashlib
import struct

from solders.pubkey import Pubkey

from pumpbot import pumpix as P
from pumpbot.events import TRADE_EVENT_DISC, decode_trade

MINT = "2u2py6mMFuMs9GdxuWMB24WSDrKS9HAwuc3GSvrTpump"
USER = "D9gQ6RhKEpnobPBUdWY5bPQt2p3zGk3iVz6ChpUi2ArA"


def test_discriminators_are_anchor_sighashes():
    for n, d in P.DISC.items():
        assert hashlib.sha256(b"global:" + n.encode()).digest()[:8] == d


def test_fee_config_pda_matches_docs():
    assert str(P.fee_config_pda()) == P.FEE_CONFIG_ADDR


def test_v3_has_17_accounts_in_doc_order():
    ix = P.buy_exact_quote_in_v3(USER, MINT, 10_000_000, 1)
    a = ix.accounts
    assert len(a) == 17
    assert a[0].pubkey == P.global_pda() and a[1].pubkey == Pubkey.from_string(MINT) and a[2].pubkey == P.WSOL
    assert a[5].pubkey == P.bonding_curve_pda(MINT) and a[5].is_writable
    assert a[8].pubkey == Pubkey.from_string(USER) and a[8].is_signer
    assert a[12].pubkey == P.fee_config_pda() and a[13].pubkey == Pubkey.from_string(P.BUYBACK_FEE_RECIPIENTS[0])
    assert a[16].pubkey == P.PUMP_PROGRAM
    assert bytes(ix.data)[:8] == P.DISC["buy_exact_quote_in_v3"] and struct.unpack("<QQ", bytes(ix.data)[8:24]) == (10_000_000, 1)


def test_sell_v3_data():
    ix = P.sell_v3(USER, MINT, 123, 45)
    assert bytes(ix.data) == P.DISC["sell_v3"] + struct.pack("<QQ", 123, 45)


def test_parse_bonding_curve_quote_mint_at_83():
    buf = bytearray(8 + 40 + 1 + 32 + 1 + 1 + 32 + 8)
    struct.pack_into("<QQQQQ", buf, 8, 1_000_000, 30_000_000_000, 5, 6, 10 ** 15)
    buf[48] = 0
    buf[81] = 0; buf[82] = 1
    usdc = Pubkey.from_string("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
    buf[83:115] = bytes(usdc)
    c = P.parse_bonding_curve(bytes(buf))
    assert c["quote_mint"] == str(usdc) and c["is_cashback_coin"] and c["virtual_quote_reserves"] == 30_000_000_000


def test_curve_math_roundtrip_loses_two_fees():
    vq, vt = 40 * 10 ** 9, 800_000_000 * 10 ** 6
    tok = P.expected_tokens_out(10 ** 8, vq, vt, 125)
    back = P.expected_quote_out(tok, vq + 10 ** 8 * 10_000 // 10_125, vt - tok, 125)
    assert 0.97 < back / 10 ** 8 < 0.98


def _trade_event(quote_mint=None, vsol=40 * 10 ** 9, qamt=0, vq=0):
    b = bytearray(TRADE_EVENT_DISC)
    b += bytes(Pubkey.from_string(MINT)); b += struct.pack("<QQ", 10 ** 8 if vsol else 0, 10 ** 12); b += b"\x01"
    b += bytes(Pubkey.from_string(USER)); b += struct.pack("<q", 1_795_000_000)
    b += struct.pack("<QQQQ", vsol, 800 * 10 ** 12, 1, 1); b += bytes(32); b += struct.pack("<QQ", 95, 1)
    b += bytes(32); b += struct.pack("<QQ", 30, 1)
    b += b"\x00" + struct.pack("<QQQq", 0, 0, 0, 0)
    name = b"buy_exact_quote_in_v3"; b += struct.pack("<I", len(name)) + name
    b += b"\x00" + struct.pack("<QQQQ", 0, 0, 0, 0) + struct.pack("<I", 0)
    b += bytes(Pubkey.from_string(quote_mint)) if quote_mint else bytes(32)
    b += struct.pack("<QQQ", qamt, vq, 0) + struct.pack("<QQQ", 0, 0, 0) + bytes(8)
    return bytes(b)


def test_decode_sol_trade():
    ev = decode_trade(_trade_event())
    assert ev["quote_mint"] == "SOL" and ev["is_buy"] and ev["ix_name"] == "buy_exact_quote_in_v3"


def test_decode_quote_trade_name_at_258_quote_at_plus37():
    raw = _trade_event("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", vsol=0, qamt=5_000_000, vq=7_000_000_000)
    assert struct.unpack_from("<I", raw, 258)[0] == len("buy_exact_quote_in_v3")
    o = 262 + 21
    assert raw[o + 37:o + 69] == bytes(Pubkey.from_string("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"))
    ev = decode_trade(raw)
    assert ev["quote_mint"].startswith("EPjF") and ev["quote_amount"] == 5_000_000 and ev["virtual_quote_reserves"] == 7_000_000_000
