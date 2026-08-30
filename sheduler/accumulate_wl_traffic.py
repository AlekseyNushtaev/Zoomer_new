"""Ежедневное накопление trafic_wl с WL-ноды (02:57 МСК)."""
from __future__ import annotations

from bot import sql, x3
from logging_config import logger
from wl_traffic.service import (
    billing_uid_from_panel_username,
    fetch_wl_traffic_gb_for_day,
    wl_traffic_day,
)


async def _resolve_billing_uid(username: str):
    uid = billing_uid_from_panel_username(username)
    if uid is not None:
        return uid
    if username.startswith("gift_"):
        return await sql.get_user_id_by_field_str_2(username)
    return None


async def accumulate_wl_traffic_cron() -> None:
    """
    Закрывающий WL-день (до 03:00 — вчера по календарю): legacy bulk по всем белым нодам,
    сумма GB и прибавление к trafic_wl. Три retry при пустом ответе.
    Повтор за уже закрытый день пропускается (wl_traffic_meta.last_closed_date).
    """
    try:
        day = wl_traffic_day()
        last_closed = await sql.get_wl_traffic_last_closed_date()
        if last_closed is not None and last_closed >= day:
            logger.info(
                f"accumulate_wl_traffic: WL-день {day.isoformat()} уже закрыт "
                f"(last_closed={last_closed.isoformat()}), пропуск"
            )
            return

        by_username, by_uuid = await fetch_wl_traffic_gb_for_day(x3, day)
        if not by_username and not by_uuid:
            logger.warning(
                f"accumulate_wl_traffic: нет данных legacy за {day.isoformat()}, пропуск"
            )
            return

        gb_by_username = dict(by_username)
        updated = 0
        skipped = 0

        for username, gb in gb_by_username.items():
            if gb <= 0:
                continue
            billing_uid = await _resolve_billing_uid(username)
            if billing_uid is None:
                skipped += 1
                continue
            await sql.add_trafic_wl(billing_uid, gb)
            updated += 1

        await sql.set_wl_traffic_last_closed_date(day)

        logger.info(
            f"accumulate_wl_traffic: день={day.isoformat()} закрыт, "
            f"обновлено={updated} пропущено_username={skipped} "
            f"bulk={len(by_username)} username / {len(by_uuid)} uuid"
        )
    except Exception as e:
        logger.error(f"accumulate_wl_traffic_cron: {e}")
