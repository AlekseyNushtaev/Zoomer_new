"""
Добавляет users_subscribed INTEGER NOT NULL DEFAULT 0 в таблицу online.
Существующие строки получают 0.

Запуск из корня проекта:
  python -m config_bd.migrate_online_users_subscribed
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

_TABLE = "online"
_COLUMN = "users_subscribed"


async def migrate() -> None:
    async with engine.begin() as conn:
        exists = await conn.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = :table AND column_name = :col
                """
            ),
            {"table": _TABLE, "col": _COLUMN},
        )
        if exists.fetchone():
            print(f"skip: {_TABLE}.{_COLUMN} already exists")
            return

        await conn.execute(
            text(
                f"ALTER TABLE {_TABLE} "
                f"ADD COLUMN {_COLUMN} INTEGER NOT NULL DEFAULT 0"
            )
        )
        print(f"OK: {_TABLE}.{_COLUMN} added (0 for existing rows)")


def main() -> None:
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
