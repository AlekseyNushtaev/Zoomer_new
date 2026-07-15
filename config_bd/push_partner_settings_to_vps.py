"""
Пушит identity-поля из локальной БД мастера на partner_api (другой сервер).

Нужны PARTNER_VPS_IP и PARTNER_VPS_API_KEY в .env Zoomer.

Запуск из корня Zoomer:
  python -m config_bd.push_partner_settings_to_vps --dry-run
  python -m config_bd.push_partner_settings_to_vps
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from config_bd.partner_apps import PartnerAppSQL
from services.partner_vps_client import PartnerVpsError, sync_bot_settings


async def run(*, dry_run: bool) -> None:
    items = await PartnerAppSQL().list_settings_export()
    print(f"Из master БД: {len(items)} заявок")
    if not items:
        print("Нечего отправлять.")
        return
    try:
        result = await sync_bot_settings(items, dry_run=dry_run)
    except PartnerVpsError as e:
        raise SystemExit(f"VPS error: {e}") from e
    print(result)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
