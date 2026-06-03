"""Периодический pg_dump и отправка дампа в CHECKER_ID."""
from __future__ import annotations

import asyncio
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile

from config import (
    CHECKER_ID,
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
)
from logging_config import logger

_BACKUP_LOCK = asyncio.Lock()
_BACKUP_DIR = Path(__file__).resolve().parent.parent / "pg_backups"
# Лимит отправки документа ботом (Telegram Bot API — до 50 МБ).
_TELEGRAM_DOC_MAX_BYTES = 49 * 1024 * 1024


def _dump_path() -> Path:
    """Один файл на диске — каждый pg_dump перезаписывает его."""
    return _BACKUP_DIR / f"{POSTGRES_DB}.dump"


def _pg_dump_bin() -> str:
    custom = (os.environ.get("PG_DUMP_BIN") or "").strip()
    if custom:
        return custom
    found = shutil.which("pg_dump")
    return found or "pg_dump"


async def _notify_checker(bot: Bot, text: str) -> None:
    if CHECKER_ID is None:
        return
    try:
        await bot.send_message(CHECKER_ID, text)
    except Exception as e:
        logger.error("Не удалось отправить CHECKER_ID о бекапе: {}", e)


async def pg_dump_backup_cron(bot: Bot) -> None:
    if not all([POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB]):
        logger.warning("pg_dump backup: POSTGRES_* не заданы, пропуск")
        return

    async with _BACKUP_LOCK:
        t0 = time.perf_counter()
        await _notify_checker(bot, "⏳ Бекап: начат")
        logger.info("pg_dump backup: начат")

        dump_path = _dump_path()
        try:
            _BACKUP_DIR.mkdir(parents=True, exist_ok=True)

            env = {**os.environ, "PGPASSWORD": POSTGRES_PASSWORD}
            proc = await asyncio.create_subprocess_exec(
                _pg_dump_bin(),
                "-h",
                POSTGRES_HOST,
                "-p",
                str(POSTGRES_PORT),
                "-U",
                POSTGRES_USER,
                "-d",
                POSTGRES_DB,
                "-Fc",
                "-f",
                str(dump_path),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await proc.communicate()
            dump_sec = time.perf_counter() - t0

            if proc.returncode != 0:
                err = (stderr or b"").decode(errors="replace").strip() or f"exit code {proc.returncode}"
                raise RuntimeError(err)

            if not dump_path.is_file():
                raise RuntimeError("pg_dump не создал файл дампа")

            size_bytes = dump_path.stat().st_size
            size_mb = size_bytes / (1024 * 1024)
            logger.info(
                "pg_dump backup: дамп готов за {:.2f} с, {:.2f} МБ, файл {}",
                dump_sec,
                size_mb,
                dump_path.name,
            )

            if CHECKER_ID is None:
                logger.warning("pg_dump backup: CHECKER_ID не задан, файл оставлен на диске")
                return

            if size_bytes > _TELEGRAM_DOC_MAX_BYTES:
                msg = (
                    f"⚠️ Бекап: готов ({dump_sec:.1f} с, {size_mb:.2f} МБ), "
                    f"но файл слишком большой для Telegram.\n"
                    f"Путь на сервере:\n{dump_path}"
                )
                await _notify_checker(bot, msg)
                logger.warning(
                    "pg_dump backup: файл {:.2f} МБ не отправлен в TG, оставлен {}",
                    size_mb,
                    dump_path,
                )
                return

            send_t0 = time.perf_counter()
            caption = (
                f"📦 pg_dump {POSTGRES_DB}\n"
                f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
                f"⏱ дамп: {dump_sec:.1f} с · {size_mb:.2f} МБ"
            )
            try:
                await bot.send_document(
                    CHECKER_ID,
                    document=FSInputFile(dump_path, filename=dump_path.name),
                    caption=caption,
                )
            except TelegramBadRequest as e:
                raise RuntimeError(f"Telegram отклонил файл: {e}") from e

            send_sec = time.perf_counter() - send_t0
            total_sec = time.perf_counter() - t0
            ready_msg = (
                f"✅ Бекап: готов\n"
                f"⏱ дамп {dump_sec:.1f} с, отправка {send_sec:.1f} с, всего {total_sec:.1f} с\n"
                f"📁 {size_mb:.2f} МБ"
            )
            await _notify_checker(bot, ready_msg)
            logger.info(
                "pg_dump backup: отправлен в CHECKER_ID за {:.2f} с (всего {:.2f} с), на диске {}",
                send_sec,
                total_sec,
                dump_path,
            )

        except FileNotFoundError:
            err_text = "❌ Бекап: ошибка — pg_dump не найден (установите postgresql-client или PG_DUMP_BIN)"
            logger.error("pg_dump backup: pg_dump не найден в PATH")
            await _notify_checker(bot, err_text)
        except Exception as e:
            elapsed = time.perf_counter() - t0
            err_short = str(e).replace("\n", " ")[:500]
            err_text = f"❌ Бекап: ошибка ({elapsed:.1f} с)\n{err_short}"
            logger.exception("pg_dump backup: ошибка за {:.2f} с: {}", elapsed, e)
            await _notify_checker(bot, err_text)
