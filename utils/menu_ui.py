"""UI helpers for photo-based menu screens."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Union

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

from bot import bot, sql, x3
from logging_config import logger
from utils.menu_photos import PHOTO_KEYS, menu_photo
from wl_traffic.service import get_wl_used_gb_for_user, is_forever_end_date

MAIN_MENU_REPLY_TEXT = (
    "Кнопка <b>Главное меню</b> внизу — нажмите её, чтобы в любой момент вернуться в главное меню."
)
MAIN_MENU_BUTTON_TEXT = "Главное меню"

_USER_TUPLE_SUBSCRIPTION_END_DATE = 9


def reply_keyboard_main_menu() -> ReplyKeyboardMarkup:
    from keyboard import STYLE_PRIMARY

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MAIN_MENU_BUTTON_TEXT, style=STYLE_PRIMARY)],
        ],
        resize_keyboard=True,
    )


async def send_main_menu_hint(message: Message) -> None:
    await message.answer(
        MAIN_MENU_REPLY_TEXT,
        parse_mode="HTML",
        reply_markup=reply_keyboard_main_menu(),
    )


def _format_date_msk(dt: datetime) -> str:
    from datetime import timedelta

    if dt.tzinfo is None:
        aware = dt.replace(tzinfo=timezone.utc)
    else:
        aware = dt.astimezone(timezone.utc)
    return (aware + timedelta(hours=3)).strftime("%d.%m.%Y")


def subscription_status_text(user_data: Optional[tuple]) -> str:
    if not user_data or len(user_data) <= _USER_TUPLE_SUBSCRIPTION_END_DATE:
        return "Нет подписки"
    sub_end = user_data[_USER_TUPLE_SUBSCRIPTION_END_DATE]
    if sub_end is None:
        return "Нет подписки"
    if is_forever_end_date(sub_end):
        return "Активна навсегда ♾️"
    if sub_end.tzinfo is None:
        aware = sub_end.replace(tzinfo=timezone.utc)
    else:
        aware = sub_end.astimezone(timezone.utc)
    date_str = _format_date_msk(aware)
    if aware > datetime.now(timezone.utc):
        return f"Активна до {date_str}"
    return f"Истекла {date_str}"


def has_active_subscription(user_data: Optional[tuple]) -> bool:
    if not user_data or len(user_data) <= _USER_TUPLE_SUBSCRIPTION_END_DATE:
        return False
    sub_end = user_data[_USER_TUPLE_SUBSCRIPTION_END_DATE]
    if sub_end is None:
        return False
    if is_forever_end_date(sub_end):
        return True
    if sub_end.tzinfo is None:
        aware = sub_end.replace(tzinfo=timezone.utc)
    else:
        aware = sub_end.astimezone(timezone.utc)
    return aware > datetime.now(timezone.utc)


def profile_caption(fullname: str, user_data: Optional[tuple]) -> str:
    status = subscription_status_text(user_data)
    return f"👤 {fullname}\n📲 {status}"


async def sync_panel_user_to_db(uid: int) -> bool:
    """Sync in_panel, subscription end date and short uuid from VPN panel."""
    user_id_str = str(uid)
    panel_resp = await x3.get_user_by_username(user_id_str)
    user = x3._panel_user_from_response(panel_resp)
    if not user:
        return False

    expire_at_str = user.get("expireAt")
    if not expire_at_str:
        return False

    expire_at = datetime.fromisoformat(expire_at_str.replace("Z", "+00:00"))
    if expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)

    if await sql.get_user(uid) is None:
        await sql.add_user(uid, True)
    await sql.update_in_panel(uid)
    await sql.update_subscription_end_date(uid, expire_at)
    await x3._sync_shortuuid_to_db(user_id_str, uid, user)
    return True


async def subscription_end_display(uid: int) -> str:
    result = await x3.activ(str(uid))
    return result.get("time") or "—"


async def connect_screen_extra(uid: int, user_data: tuple) -> str:
    used_gb, limit_gb = await sql.get_wl_limits(uid)
    used_gb = await get_wl_used_gb_for_user(x3, uid, used_gb, sql=sql)

    wl_block = (
        f"📡 Антиглушилка: {used_gb:.2f} / {limit_gb:.2f} GB"
    )

    devices_count = 0
    device_limit = 5
    panel_resp = await x3.get_user_by_username(str(uid))
    panel_user = x3._panel_user_from_response(panel_resp)
    if panel_user:
        device_limit = panel_user.get("hwidDeviceLimit") or 5
        panel_user_id = x3._panel_user_id(panel_user)
        if panel_user_id is not None:
            _devices, devices_count = await x3.get_user_hwid_devices(str(panel_user_id))

    devices_block = f"📱 Устройства: {devices_count} / {device_limit}"
    return f"{wl_block}\n{devices_block}"


async def _replace_photo_message(
    chat_id: int,
    message_id: int,
    photo_key: str,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        pass
    await bot.send_photo(
        chat_id,
        photo=menu_photo(photo_key),
        caption=caption,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


async def edit_or_send_photo(
    source: Union[Message, CallbackQuery],
    photo_key: str,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    if isinstance(source, CallbackQuery):
        message = source.message
        chat_id = message.chat.id
        message_id = message.message_id
    else:
        message = source
        chat_id = message.chat.id
        message_id = message.message_id

    if message.photo:
        try:
            await message.edit_media(
                media=InputMediaPhoto(
                    media=menu_photo(photo_key),
                    caption=caption,
                    parse_mode="HTML",
                ),
                reply_markup=reply_markup,
            )
            return
        except TelegramBadRequest as e:
            logger.warning(
                "menu edit_media failed chat_id={} key={}: {}",
                chat_id,
                photo_key,
                e,
            )
            await _replace_photo_message(
                chat_id, message_id, photo_key, caption, reply_markup
            )
            return

    await bot.send_photo(
        chat_id,
        photo=menu_photo(photo_key),
        caption=caption,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


def trial_success_caption(end_time: str, sub_url: str) -> str:
    return (
        "🎉 <b>Тестовая подписка активирована!</b>\n"
        f"⏰ Доступ до: {end_time}\n\n"
        "🔗 Ваша ссылка для импорта в VPN приложение:\n"
        f"{sub_url}\n\n"
        "📱 Нажмите кнопку ниже, чтобы получить инструкцию по настройке VPN на вашем устройстве"
    )


def trial_existing_active_caption(end_time: str, sub_url: str) -> str:
    return (
        "<b>У вас уже есть подписка!</b>\n\n"
        f"⏰ Активна до: {end_time}\n\n"
        "🔗 Ваша ссылка для импорта в VPN приложение:\n"
        f"{sub_url}\n\n"
        "📱 Нажмите «Если страница не загружается», чтобы получить инструкцию по настройке VPN на вашем устройстве"
    )


def trial_existing_expired_caption(end_date: str) -> str:
    return (
        "<b>У вас уже есть подписка!</b>\n\n"
        f"Истекла {end_date}, необходимо продлить.\n"
        "Выйдите в главное меню и купите подписку."
    )


def connect_screen_caption(fullname: str, user_data: tuple, sub_url: str, extra: str) -> str:
    status = subscription_status_text(user_data)
    return (
        f"👤 {fullname}\n"
        f"📲 {status}\n"
        f"{extra}\n\n"
        "🔗 Ссылка для импорта:\n"
        f"{sub_url}\n\n"
        "📱 Нажмите «Если страница не загружается», чтобы получить инструкцию по настройке VPN"
    )


async def show_connect_screen(callback: CallbackQuery) -> bool:
    from keyboard import keyboard_subscription_manage
    from lexicon import lexicon

    uid = callback.from_user.id
    user_data = await sql.get_user(uid)
    if not user_data:
        user_data = tuple()
    sub_url = await x3.sublink(str(uid))
    if not sub_url:
        await callback.message.answer(lexicon["no_sub"])
        return False

    fullname = callback.from_user.full_name or callback.from_user.first_name or "Пользователь"
    extra = await connect_screen_extra(uid, user_data)
    caption = connect_screen_caption(fullname, user_data, sub_url, extra)
    await edit_or_send_photo(
        callback,
        "subscription_manage",
        caption,
        keyboard_subscription_manage(sub_url),
    )
    return True


async def edit_or_send_screen(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    *,
    photo_key: str = "buy_subscription",
) -> None:
    """Редактирует photo-caption или text-сообщение; при ошибке отправляет новое."""
    message = callback.message
    if message.photo:
        await edit_or_send_photo(callback, photo_key, text, reply_markup)
        return
    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except TelegramBadRequest:
        await message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
