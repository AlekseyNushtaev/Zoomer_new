"""
Постбеки в Lead Tracker (FastAPI на VPS): POST /users/, /users/trial, /users/connected, /payments/.
Включается, если заданы LEAD_TRACKER_BASE и LEAD_TRACKER_API_KEY.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

import aiohttp

from bot import bot, sql
from config import (
    LEAD_TRACKER_API_KEY,
    LEAD_TRACKER_BASE,
    LEAD_TRACKER_STAR_RUB_PER_STAR,
)
from logging_config import logger

_TRACKED_PAYMENT_METHODS = frozenset(
    {
        "stars",
        "cryptobot",
        "wata_sbp",
        "wata_card",
        "fk_sbp",
        "fk_card",
        "fksbp",
        "sbp",
        "card",
        "crypto",
        "ton",
        "usdt",
    }
)


def _post_body_log_summary(body: dict[str, Any]) -> str:
    parts = [f"user_id={body.get('user_id')}", f"bot_id={body.get('bot_id')}"]
    if "amount" in body:
        parts.append(f"amount={body.get('amount')}")
    if body.get("source") is not None:
        parts.append(f"source={body.get('source')!r}")
    if body.get("username") is not None:
        parts.append(f"username={body.get('username')!r}")
    return ", ".join(parts)


_cached_bot_id: Optional[int] = None
_cached_bot_username: Optional[str] = None


def is_enabled() -> bool:
    base = (LEAD_TRACKER_BASE or "").strip()
    key = (LEAD_TRACKER_API_KEY or "").strip()
    return bool(base and key)


async def _bot_meta() -> tuple[Optional[int], Optional[str]]:
    global _cached_bot_id, _cached_bot_username
    if _cached_bot_id is not None:
        return _cached_bot_id, _cached_bot_username
    try:
        me = await bot.get_me()
        _cached_bot_id = int(me.id)
        _cached_bot_username = me.username
        return _cached_bot_id, _cached_bot_username
    except Exception as e:
        logger.error(f"Lead Tracker: bot.get_me() failed: {e}")
        return None, None


def _base_url() -> str:
    return (LEAD_TRACKER_BASE or "").strip().rstrip("/")


async def _post_json(path: str, body: dict[str, Any], *, kind: str = "") -> bool:
    label = kind or path
    if not is_enabled():
        logger.debug(f"Lead Tracker [{label}]: пропуск, трекер не настроен (BASE/API_KEY)")
        return False
    bot_id, _ = await _bot_meta()
    if bot_id is None:
        logger.warning(f"Lead Tracker [{label}]: пропуск POST {path}, не удалось получить bot_id")
        return False
    url = f"{_base_url()}{path}"
    headers = {
        "X-API-Key": (LEAD_TRACKER_API_KEY or "").strip(),
        "Content-Type": "application/json",
    }
    logger.info(
        f"Lead Tracker [{label}]: отправка POST {path} → {_base_url()}… "
        f"({_post_body_log_summary(body)})"
    )
    timeout = aiohttp.ClientTimeout(total=10)
    connector = aiohttp.TCPConnector(ssl=False)
    try:
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.post(url, headers=headers, json=body) as resp:
                text = await resp.text()
                if resp.status not in (200, 201):
                    logger.warning(
                        f"Lead Tracker [{label}]: ответ POST {path} HTTP {resp.status}, "
                        f"тело ответа: {text[:800]}"
                    )
                    return False
                logger.info(
                    f"Lead Tracker [{label}]: успешно POST {path} HTTP {resp.status}, "
                    f"user_id={body.get('user_id')}"
                )
                return True
    except Exception as e:
        logger.error(f"Lead Tracker [{label}]: ошибка POST {path}: {e}")
        return False


TRACKER_SOURCE_REFERRAL = "referral"
TRACKER_SOURCE_PARTNER = "partner"


def _normalize_source_token(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, (datetime, date)):
        return None
    s = str(val).strip()
    return s or None


def tracker_source_from_ref_and_stamp(
    ref: Any,
    stamp: Any,
    partner: Any = None,
) -> Optional[str]:
    partner_n = _normalize_source_token(partner)
    if partner_n:
        return TRACKER_SOURCE_PARTNER
    ref_n = _normalize_source_token(ref)
    if ref_n:
        return TRACKER_SOURCE_REFERRAL
    return _normalize_source_token(stamp)


def _source_from_row(row: tuple) -> Optional[str]:
    ref = row[2] if len(row) > 2 else None
    stamp = row[14] if len(row) > 14 else None
    partner = row[29] if len(row) > 29 else None
    return tracker_source_from_ref_and_stamp(ref, stamp, partner)


async def sync_user_from_db(telegram_user_id: int) -> bool:
    if not is_enabled():
        return False
    row = await sql.get_user(telegram_user_id)
    if row is None:
        logger.debug(f"Lead Tracker [sync]: пользователь {telegram_user_id} не найден в локальной БД")
        return False
    bot_id, bot_name = await _bot_meta()
    if bot_id is None:
        return False
    body = {
        "user_id": telegram_user_id,
        "username": None,
        "full_name": None,
        "source": _source_from_row(row),
        "bot_id": bot_id,
        "bot_name": bot_name,
    }
    return await _post_json("/users/", body, kind="sync")


async def post_user_registered(
    telegram_user_id: int,
    username: Optional[str],
    full_name: Optional[str],
    source: Optional[str],
) -> None:
    if not is_enabled():
        logger.debug("Lead Tracker [register]: пропуск, трекер не настроен")
        return
    bot_id, bot_name = await _bot_meta()
    if bot_id is None:
        logger.warning("Lead Tracker [register]: пропуск, bot_id недоступен")
        return
    body = {
        "user_id": telegram_user_id,
        "username": username,
        "full_name": full_name,
        "source": source,
        "bot_id": bot_id,
        "bot_name": bot_name,
    }
    await _post_json("/users/", body, kind="register")


async def post_user_trial(telegram_user_id: int) -> None:
    if not is_enabled():
        logger.debug("Lead Tracker [trial]: пропуск, трекер не настроен")
        return
    await sync_user_from_db(telegram_user_id)
    bot_id, _ = await _bot_meta()
    if bot_id is None:
        logger.warning("Lead Tracker [trial]: пропуск после sync, bot_id недоступен")
        return
    await _post_json("/users/trial", {"user_id": telegram_user_id, "bot_id": bot_id}, kind="trial")


async def post_user_connected(telegram_user_id: int) -> None:
    if not is_enabled():
        logger.debug("Lead Tracker [connected]: пропуск, трекер не настроен")
        return
    await sync_user_from_db(telegram_user_id)
    bot_id, _ = await _bot_meta()
    if bot_id is None:
        logger.warning("Lead Tracker [connected]: пропуск после sync, bot_id недоступен")
        return
    await _post_json(
        "/users/connected",
        {"user_id": telegram_user_id, "bot_id": bot_id},
        kind="connected",
    )


def _payment_amount_rub(method: str, raw_amount: int | float) -> str:
    if method == "stars":
        stars = Decimal(str(raw_amount))
        rate = Decimal(str(LEAD_TRACKER_STAR_RUB_PER_STAR))
        rub = (stars * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return format(rub, "f")
    return format(Decimal(str(raw_amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


async def post_payment_success(
    telegram_user_id: int,
    method: str,
    raw_amount: int | float,
) -> None:
    if not is_enabled():
        logger.debug("Lead Tracker [payment]: пропуск, трекер не настроен")
        return
    if method not in _TRACKED_PAYMENT_METHODS:
        logger.debug(
            f"Lead Tracker [payment]: пропуск для method={method!r} "
            f"(отслеживаются: {', '.join(sorted(_TRACKED_PAYMENT_METHODS))})"
        )
        return
    await sync_user_from_db(telegram_user_id)
    bot_id, _ = await _bot_meta()
    if bot_id is None:
        logger.warning("Lead Tracker [payment]: пропуск после sync, bot_id недоступен")
        return
    body = {
        "user_id": telegram_user_id,
        "bot_id": bot_id,
        "amount": _payment_amount_rub(method, raw_amount),
    }
    await _post_json("/payments/", body, kind=f"payment:{method}")
