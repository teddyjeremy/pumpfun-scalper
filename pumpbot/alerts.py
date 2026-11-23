"""Optional Telegram alerts: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID. Silently disabled otherwise."""
from __future__ import annotations

import os

import aiohttp


async def telegram(text: str):
    tok = os.environ.get("TELEGRAM_BOT_TOKEN"); chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
            await s.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": chat, "text": text[:3500]})
    except Exception:
        pass
