"""
Проверка Unisender Go HTTP API из текущего .env.

Запуск из корня Zoomer:
  python scripts/test_smtp.py
  python scripts/test_smtp.py --send your@email.com
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root / ".env")

from config import SMTP_FROM, UNISENDER_API_KEY, UNISENDER_API_URL, UNISENDER_FROM_NAME
from services.unisender import is_configured, send_email


def main() -> int:
    parser = argparse.ArgumentParser(description="Unisender Go API diagnostic")
    parser.add_argument("--send", metavar="EMAIL", help="Send test message to this address")
    args = parser.parse_args()

    print("=== Unisender Go config ===")
    print(f"URL={UNISENDER_API_URL!r}")
    print(f"FROM={SMTP_FROM!r} NAME={UNISENDER_FROM_NAME!r}")
    print(f"API_KEY set: {bool(UNISENDER_API_KEY)}")

    if not is_configured():
        print("ERROR: UNISENDER_API_KEY or SMTP_FROM not set in .env")
        return 1

    if not args.send:
        print("\nConfig OK. Pass --send email@example.com to send a test letter.")
        return 0

    print(f"\nSending test to {args.send} ...")
    try:
        asyncio.run(
            send_email(
                to_email=args.send,
                subject="Unisender API test",
                text="Zoomer Unisender Go API test OK",
            )
        )
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}")
        return 1
    print("Send OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
