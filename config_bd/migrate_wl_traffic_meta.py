"""
Таблица wl_traffic_meta — глобальная дата последнего закрытия WL-дня (PostgreSQL).

Запуск из корня проекта:
  python -m config_bd.migrate_wl_traffic_meta
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from sqlalchemy import text

from config_bd.models import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS wl_traffic_meta (
                    id INTEGER PRIMARY KEY,
                    last_closed_date DATE
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO wl_traffic_meta (id, last_closed_date)
                VALUES (1, NULL)
                ON CONFLICT (id) DO NOTHING
                """
            )
        )
    print("OK: wl_traffic_meta.")


def main() -> None:
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
