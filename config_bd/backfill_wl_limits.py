"""
Backfill trafic_wl и limit_wl для всех пользователей (PostgreSQL).

- trafic_wl = 0 (GB)
- limit_wl = 10 GB — subscription_end_date на текущую дату или позже
- limit_wl = 0 — подписки нет или она уже истекла (раньше начала текущего дня)

«Текущая дата» — календарный день по Europe/Moscow (как WL-учёт в боте).

Запуск из корня проекта на VPS:
  python -m config_bd.backfill_wl_limits
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from sqlalchemy import text

from config_bd.models import engine
from wl_traffic.constants import WL_GB_PER_MONTH, WL_TIMEZONE

_ACTIVE_LIMIT_GB = float(WL_GB_PER_MONTH)


def _today_start_moscow() -> datetime:
    now = datetime.now(WL_TIMEZONE)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)


async def _ensure_wl_columns(conn) -> None:
    for name, col_type in (
        ("trafic_wl", "DOUBLE PRECISION DEFAULT 0"),
        ("limit_wl", "DOUBLE PRECISION DEFAULT 0"),
    ):
        exists = await conn.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = :name
                """
            ),
            {"name": name},
        )
        if exists.fetchone():
            continue
        await conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {col_type}"))


async def backfill() -> None:
    today_start = _today_start_moscow()
    today_label = today_start.date().isoformat()

    async with engine.begin() as conn:
        table_check = await conn.execute(
            text(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_name = 'users'
                """
            )
        )
        if not table_check.fetchone():
            print("ERROR: таблица users не найдена.")
            return

        await _ensure_wl_columns(conn)

        await conn.execute(text("UPDATE users SET trafic_wl = 0"))

        await conn.execute(
            text(
                """
                UPDATE users
                SET limit_wl = :active_limit
                WHERE subscription_end_date IS NOT NULL
                  AND subscription_end_date >= :today_start
                """
            ),
            {"active_limit": _ACTIVE_LIMIT_GB, "today_start": today_start},
        )

        await conn.execute(
            text(
                """
                UPDATE users
                SET limit_wl = 0
                WHERE subscription_end_date IS NULL
                   OR subscription_end_date < :today_start
                """
            ),
            {"today_start": today_start},
        )

        total = (await conn.execute(text("SELECT COUNT(*) FROM users"))).scalar_one()
        active = (
            await conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM users
                    WHERE subscription_end_date IS NOT NULL
                      AND subscription_end_date >= :today_start
                    """
                ),
                {"today_start": today_start},
            )
        ).scalar_one()
        inactive = total - active

    print(
        f"OK: backfill WL limits (today={today_label} MSK, active_limit={_ACTIVE_LIMIT_GB:g} GB).\n"
        f"  users total={total}\n"
        f"  limit_wl={_ACTIVE_LIMIT_GB:g} GB (active sub): {active}\n"
        f"  limit_wl=0 (no/expired sub): {inactive}\n"
        f"  trafic_wl=0: all"
    )


def main() -> None:
    asyncio.run(backfill())


if __name__ == "__main__":
    main()
