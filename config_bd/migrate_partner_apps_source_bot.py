"""
Добавляет source_bot_id в partner_bot_applications.

Запуск из корня Zoomer:
  python -m config_bd.migrate_partner_apps_source_bot
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
                "ALTER TABLE partner_bot_applications "
                "ADD COLUMN IF NOT EXISTS source_bot_id INTEGER"
            )
        )
    print("OK: partner_bot_applications.source_bot_id")


def main() -> None:
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
