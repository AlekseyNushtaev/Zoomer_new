"""
Добавляет site_url в second_site.

Запуск из корня проекта:
  python -m config_bd.migrate_second_site_site_url
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
                "ALTER TABLE second_site "
                "ADD COLUMN IF NOT EXISTS site_url VARCHAR(512)"
            )
        )
    print("OK: second_site.site_url")


def main() -> None:
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
