"""
Миграция таблицы users — лимит трафика Антиглушилка (PostgreSQL):
- trafic_wl FLOAT DEFAULT 0  (GB)
- limit_wl FLOAT DEFAULT 0   (GB)

Запуск из корня проекта:
  python -m config_bd.migrate_users_wl_fields
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

_COLUMNS = (
    ("trafic_wl", "DOUBLE PRECISION DEFAULT 0"),
    ("limit_wl", "DOUBLE PRECISION DEFAULT 0"),
)


async def migrate() -> None:
    async with engine.begin() as conn:
        for name, col_type in _COLUMNS:
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

        await conn.execute(text("UPDATE users SET trafic_wl = 0 WHERE trafic_wl IS NULL"))
        await conn.execute(text("UPDATE users SET limit_wl = 0 WHERE limit_wl IS NULL"))

    print("OK: users trafic_wl, limit_wl.")


def main() -> None:
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
