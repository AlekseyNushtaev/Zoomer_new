"""
Создание таблицы second_site для landing-сайта.

Запуск из корня проекта:
  python -m config_bd.migrate_second_site
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from config_bd.models import SecondSite, engine


async def migrate() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: SecondSite.__table__.create(sync_conn, checkfirst=True))
    print("OK: таблица second_site создана (или уже существовала).")


if __name__ == "__main__":
    asyncio.run(migrate())
