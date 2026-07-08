"""Очистка профиля только что созданного managed bot."""
from __future__ import annotations

from aiogram import Bot

from logging_config import logger


async def strip_managed_bot_profile(token: str) -> None:
    """
    Убирает всё, что можно убрать через Bot API у дочернего managed bot:
    description, short description, аватар, команды.
    Системные блоки Telegram («готов», «создан и поддерживается …») API не снимает.
    """
    child = Bot(token=token.strip())
    try:
        for action, coro in (
            ("description", child.set_my_description(description="")),
            ("short_description", child.set_my_short_description(short_description="")),
            ("profile_photo", child.remove_my_profile_photo()),
            ("commands", child.delete_my_commands()),
        ):
            try:
                await coro
            except Exception as e:
                logger.debug("managed bot strip {} skipped: {}", action, e)
    finally:
        await child.session.close()
