"""Общая логика создания заявки на партнёрский VPN-бот."""
from __future__ import annotations

import re
from typing import Optional, Tuple

import aiohttp

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

from bot import bot
from config import ADMIN_PARTNER_IDS
from config_bd.partner_apps import PartnerAppSQL
from logging_config import logger
from utils.token_crypto import decrypt_token, encrypt_token, token_hash

TOKEN_PATTERN = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")

partner_sql = PartnerAppSQL()


async def validate_bot_token(token: str) -> Optional[dict]:
    url = f"https://api.telegram.org/bot{token.strip()}/getMe"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            data = await resp.json()
            if not data.get("ok"):
                return None
            return data["result"]


async def ensure_partner_draft_application(
    *,
    partner_tg_id: int,
    partner_username: Optional[str],
    partner_first_name: Optional[str],
    source_bot_id: Optional[int] = None,
):
    draft = await partner_sql.get_active_draft(partner_tg_id)
    if draft:
        return draft
    return await partner_sql.create_draft_application(
        partner_tg_id=partner_tg_id,
        partner_username=partner_username,
        partner_first_name=partner_first_name,
        source_bot_id=source_bot_id,
    )


async def submit_partner_application(
    *,
    partner_tg_id: int,
    partner_username: Optional[str],
    partner_first_name: Optional[str],
    token: str,
    source_bot_id: Optional[int] = None,
) -> Tuple[Optional[object], Optional[str]]:
    """
    Создаёт заявку на модерацию.
    Возвращает (application, error_message). При успехе error_message = None.
    """
    token = (token or "").strip()
    if not TOKEN_PATTERN.match(token):
        return None, "Неверный формат токена."

    th = token_hash(token)
    if await partner_sql.get_by_token_hash(th):
        return None, "Заявка с этим токеном уже существует."

    me = await validate_bot_token(token)
    if not me:
        return None, "Токен недействителен."

    bot_username = me.get("username", "")
    bot_display_name = me.get("first_name", "")

    for existing in await partner_sql.list_by_partner(partner_tg_id):
        if existing.bot_token_hash == th:
            return None, "Этот токен уже привязан к вашей заявке."

    other = await partner_sql.get_by_token_hash(th)
    if other and other.partner_tg_id != partner_tg_id:
        return None, "Этот токен уже привязан к другому партнёру."

    app = await partner_sql.create_application(
        partner_tg_id=partner_tg_id,
        partner_username=partner_username,
        partner_first_name=partner_first_name,
        bot_token_encrypted=encrypt_token(token),
        bot_token_hash=th,
        bot_username=bot_username,
        bot_display_name=bot_display_name,
        source_bot_id=source_bot_id,
    )
    return app, None


async def submit_managed_partner_application(
    *,
    partner_tg_id: int,
    partner_username: Optional[str],
    partner_first_name: Optional[str],
    token: str,
    source_bot_id: Optional[int] = None,
) -> Tuple[Optional[object], Optional[str]]:
    token = (token or "").strip()
    if not TOKEN_PATTERN.match(token):
        return None, "Неверный формат токена."

    th = token_hash(token)
    existing = await partner_sql.get_by_token_hash(th)
    if existing and existing.status != "draft":
        return None, "Заявка с этим токеном уже существует."

    me = await validate_bot_token(token)
    if not me:
        return None, "Токен недействителен."

    bot_username = me.get("username", "")
    bot_display_name = me.get("first_name", "")

    draft = await partner_sql.get_active_draft(partner_tg_id)
    if draft:
        other = await partner_sql.get_by_token_hash(th)
        if other and other.id != draft.id and other.partner_tg_id != partner_tg_id:
            return None, "Этот токен уже привязан к другому партнёру."
        app = await partner_sql.complete_draft_application(
            draft.id,
            bot_token_encrypted=encrypt_token(token),
            bot_token_hash=th,
            bot_username=bot_username,
            bot_display_name=bot_display_name,
        )
        if not app:
            return None, "Не удалось сохранить заявку."
        return app, None

    return await submit_partner_application(
        partner_tg_id=partner_tg_id,
        partner_username=partner_username,
        partner_first_name=partner_first_name,
        token=token,
        source_bot_id=source_bot_id,
    )


async def notify_partner_user(
    partner_tg_id: int,
    text: str,
    source_bot_id: Optional[int] = None,
) -> bool:
    """Уведомление партнёру через бот-источник заявки или мастер-бот (fallback)."""
    if source_bot_id:
        source_app = await partner_sql.get_by_id(source_bot_id)
        if source_app and source_app.bot_token_encrypted:
            try:
                token = decrypt_token(source_app.bot_token_encrypted)
                if not token.startswith("draft:"):
                    source_bot = Bot(
                        token=token,
                        default=DefaultBotProperties(parse_mode="HTML"),
                    )
                    try:
                        await source_bot.send_message(partner_tg_id, text)
                        logger.info(
                            "notify partner via source bot: tg_id={} source_bot_id={}",
                            partner_tg_id,
                            source_bot_id,
                        )
                        return True
                    finally:
                        await source_bot.session.close()
            except Exception as e:
                logger.error(
                    "notify partner via source bot failed: tg_id={} source_bot_id={} err={}",
                    partner_tg_id,
                    source_bot_id,
                    e,
                )

    try:
        await bot.send_message(partner_tg_id, text)
        logger.info("notify partner via master bot: tg_id={}", partner_tg_id)
        return True
    except Exception as e:
        logger.error("notify partner via master bot failed: tg_id={} err={}", partner_tg_id, e)
        return False


async def notify_admins_new_application(
    partner_tg_id: int,
    app_id: int,
    source_bot_id: Optional[int] = None,
) -> None:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    from keyboard import STYLE_PRIMARY

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Заявки на модерации", callback_data="pa_list_pending", style=STYLE_PRIMARY)],
    ])

    apps = await partner_sql.list_by_partner(partner_tg_id)
    history = "\n".join(f"• #{a.id} — {a.status} (@{a.bot_username})" for a in apps)
    source_line = f"\nБот-источник: <code>#{source_bot_id}</code>" if source_bot_id else ""
    text = (
        f"🆕 <b>Новая заявка #{app_id}</b>\n"
        f"Партнёр <code>{partner_tg_id}</code>{source_line}\n\n"
        f"<b>Все заявки партнёра:</b>\n{history}"
    )
    for admin_id in ADMIN_PARTNER_IDS:
        try:
            await bot.send_message(admin_id, text, reply_markup=admin_kb)
        except Exception as e:
            logger.error("notify admin {}: {}", admin_id, e)
