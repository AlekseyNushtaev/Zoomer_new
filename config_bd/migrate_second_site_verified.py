"""
Переименовывает second_site.field_bool_1 → verified.

Запуск из корня проекта:
  python -m config_bd.migrate_second_site_verified
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
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'second_site'
                          AND column_name = 'field_bool_1'
                    ) THEN
                        ALTER TABLE second_site RENAME COLUMN field_bool_1 TO verified;
                    END IF;
                END $$
                """
            )
        )
    print("OK: second_site.field_bool_1 -> verified")


def main() -> None:
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
