"""
Добавляет connect_panel BOOLEAN NULL в payments и payments_cards.
Существующие строки остаются NULL. Новые платежи пишутся с False.

Запуск из корня проекта:
  python -m config_bd.migrate_payments_connect_panel
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

_TABLES = ("payments", "payments_cards")
_COLUMN = "connect_panel"


async def migrate() -> None:
    async with engine.begin() as conn:
        for table in _TABLES:
            exists = await conn.execute(
                text(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = :table AND column_name = :col
                    """
                ),
                {"table": table, "col": _COLUMN},
            )
            if exists.fetchone():
                print(f"skip: {table}.{_COLUMN} already exists")
                continue
            await conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN {_COLUMN} BOOLEAN")
            )
            print(f"OK: {table}.{_COLUMN} added (NULL for existing rows)")

    print("OK: connect_panel on payments, payments_cards.")


def main() -> None:
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
