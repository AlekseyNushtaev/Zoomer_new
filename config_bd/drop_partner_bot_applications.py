"""
Удаляет таблицу partner_bot_applications (модель PartnerBotApplications).

Запуск из корня Zoomer:
  python -m config_bd.drop_partner_bot_applications
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
        await conn.execute(text("DROP TABLE IF EXISTS partner_bot_applications CASCADE"))
    print("OK: таблица partner_bot_applications удалена")


def main() -> None:
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
