#!/usr/bin/env python3
"""
Импорт БД из .xlsx в формате /export_full (без Telegram — обход лимита ~20 МБ на скачивание ботом).

Запуск на сервере из каталога проекта (нужен .env с Postgres):

  cd /root/zoomer && ./venv/bin/python scripts/import_export_xlsx.py /path/to/export.xlsx
"""
from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


async def _run(path: str) -> None:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))

    from config_bd.models import engine
    from config_bd.utils import AsyncSQL
    from handlers.handlers_excel_restore import _parse_workbook

    sql = AsyncSQL()
    bundles = _parse_workbook(path)
    stats = await sql.import_replace_all_from_export_workbook(**bundles)
    print("Импорт завершён:", stats)
    await engine.dispose()


def main() -> None:
    if len(sys.argv) < 2:
        print("Использование: python scripts/import_export_xlsx.py <файл.xlsx>", file=sys.stderr)
        sys.exit(1)
    path = os.path.abspath(sys.argv[1])
    if not os.path.isfile(path):
        print(f"Файл не найден: {path}", file=sys.stderr)
        sys.exit(1)
    asyncio.run(_run(path))


if __name__ == "__main__":
    main()
