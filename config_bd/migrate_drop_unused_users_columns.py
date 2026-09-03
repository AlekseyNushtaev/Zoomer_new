"""
Удаляет неиспользуемые колонки таблицы users:
- password
- white_subscription
- white_subscription_end_date
- field_str_3
- last_broadcast_status
- linked_telegram_id

Запуск из корня проекта (нужны переменные .env для Postgres):
  python -m config_bd.migrate_drop_unused_users_columns

  Либо напрямую:
  python config_bd/migrate_drop_unused_users_columns.py
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

_DROP_COLUMNS = (
    "password",
    "white_subscription",
    "white_subscription_end_date",
    "field_str_3",
    "last_broadcast_status",
    "linked_telegram_id",
)


async def migrate() -> None:
    drops = ",\n  ".join(f"DROP COLUMN IF EXISTS {name}" for name in _DROP_COLUMNS)
    sql = f"ALTER TABLE users\n  {drops}"
    async with engine.begin() as conn:
        await conn.execute(text(sql))
    print("OK: dropped unused users columns: " + ", ".join(_DROP_COLUMNS))


def main() -> None:
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
