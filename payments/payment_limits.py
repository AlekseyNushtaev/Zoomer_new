"""Лимиты на создание платежей (антифлуд незавершёнными счетами). Включает WATA, FreeKassa, Platega, Cryptobot."""

import time
from typing import Optional

from bot import bot, sql
from config import ADMIN_IDS, CHECKER_ID, PAYMENT_MAX_PENDING_PER_USER
from logging_config import logger

_CHECKER_NOTIFY_COOLDOWN_SEC = 90.0
_payment_limit_checker_last: dict[int, float] = {}


async def _maybe_notify_checker_payment_limit(
    user_id: int,
    telegram_username: Optional[str],
    open_count: int,
) -> None:
    if not CHECKER_ID or user_id == CHECKER_ID:
        return
    now = time.monotonic()
    last = _payment_limit_checker_last.get(user_id, 0.0)
    if now - last < _CHECKER_NOTIFY_COOLDOWN_SEC:
        return
    _payment_limit_checker_last[user_id] = now
    uname = (telegram_username or "").strip()
    label = f"@{uname.lstrip('@')}" if uname else "без username"
    text = (
        "⚠️ <b>Лимит висящих платежей</b>: попытка создать ещё один счёт.\n"
        f"Пользователь: {label}\n"
        f"Telegram ID: <code>{user_id}</code>\n"
        f"Незавершённых в БД: <code>{open_count}</code> (лимит {PAYMENT_MAX_PENDING_PER_USER})"
    )
    try:
        await bot.send_message(CHECKER_ID, text, parse_mode="HTML")
    except Exception as e:
        logger.error("Не удалось уведомить CHECKER_ID о лимите платежей: {}", e)


async def payment_creation_allowed(
    user_id: int,
    telegram_username: Optional[str] = None,
) -> bool:
    if user_id in ADMIN_IDS:
        return True
    n = await sql.count_open_payment_slots_for_user(user_id)
    if n >= PAYMENT_MAX_PENDING_PER_USER:
        logger.warning(
            "Лимит незавершённых оплат: user_id={} count={} max={}",
            user_id,
            n,
            PAYMENT_MAX_PENDING_PER_USER,
        )
        await _maybe_notify_checker_payment_limit(user_id, telegram_username, n)
        return False
    return True
