"""
Миграция таблицы users — поля партнёрской программы:
- partner VARCHAR(100) NULL
- partner_balance INTEGER DEFAULT 0
- partner_pay INTEGER DEFAULT 0
- partner_flag BOOLEAN DEFAULT FALSE

Запуск из корня проекта (нужны переменные .env для Postgres):
  python -m config_bd.migrate_users_partner_fields

  Либо напрямую:
  python config_bd/migrate_users_partner_fields.py
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
            text("ALTER TABLE users ADD COLUMN IF NOT EXISTS partner VARCHAR(100)")
        )
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS partner_balance INTEGER DEFAULT 0"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS partner_pay INTEGER DEFAULT 0"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS partner_flag BOOLEAN DEFAULT FALSE"
            )
        )
        await conn.execute(
            text(
                "UPDATE users SET partner_balance = 0 WHERE partner_balance IS NULL"
            )
        )
        await conn.execute(
            text("UPDATE users SET partner_pay = 0 WHERE partner_pay IS NULL")
        )
        await conn.execute(
            text("UPDATE users SET partner_flag = FALSE WHERE partner_flag IS NULL")
        )

    print(
        "OK: users partner, partner_balance, partner_pay, partner_flag."
    )


def main() -> None:
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
