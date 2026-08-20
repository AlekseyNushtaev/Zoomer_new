from __future__ import annotations

import re
from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot import x3
from keyboard import (
    BTN_BACK,
    create_kb,
    keyboard_devices_confirm,
    keyboard_devices_list,
)
from logging_config import logger
from utils.menu_ui import edit_or_send_photo

router = Router()

_DEFAULT_SLOT = "main"

_DEV_SUB_RE = re.compile(r"^dev_sub_main$")
_DEV_RM_RE = re.compile(r"^dev_rm_main_(\d+)$")
_DEV_RM_YES_RE = re.compile(r"^dev_rm_yes_main_(\d+)$")


def _device_display_name(device: dict[str, Any]) -> str:
    model = (device.get("deviceModel") or "").strip()
    platform = (device.get("platform") or "").strip()
    os_version = (device.get("osVersion") or "").strip()

    if model and platform:
        name = f"{model} · {platform}"
    elif model:
        name = model
    elif platform:
        name = platform
    else:
        hwid = (device.get("hwid") or "устройство")[:12]
        name = f"Устройство {hwid}"

    if os_version:
        name = f"{name} {os_version}"

    return name


def _device_button_label(device: dict[str, Any]) -> str:
    return f"📱 {_device_display_name(device)}"[:64]


def _device_line(device: dict[str, Any], index: int) -> str:
    model = (device.get("deviceModel") or "").strip()
    platform = (device.get("platform") or "").strip()
    os_version = (device.get("osVersion") or "").strip()

    parts = [p for p in (model, platform, os_version) if p]
    if parts:
        return f"{index}. {escape(' · '.join(parts))}"
    hwid = escape((device.get("hwid") or "—")[:16])
    return f"{index}. Устройство <code>{hwid}</code>"


async def _active_slots(telegram_id: int) -> list[tuple[str, str, str]]:
    slots = await x3.active_subscription_slots(telegram_id)
    return [(slot_key, label, user_uuid) for slot_key, label, user_uuid, _username in slots]


async def _slot_context(telegram_id: int, slot_key: str) -> tuple[str, str, str] | None:
    for sk, label, user_uuid, username in await x3.active_subscription_slots(telegram_id):
        if sk == slot_key:
            return label, user_uuid, username
    return None


def _no_subscriptions_text() -> str:
    return "У вас нет активных подписок"


def _no_subscriptions_markup() -> InlineKeyboardMarkup:
    return create_kb(1, back_to_main=BTN_BACK)


async def _edit_devices_screen(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    await edit_or_send_photo(callback, "manage_devices", text, reply_markup)


async def _devices_screen_text(
    label: str,
    user_uuid: str,
    username: str,
) -> tuple[str, list[dict[str, Any]], list[tuple[int, str]]]:
    user_data = await x3.get_user_by_username(username)
    user = x3._panel_user_from_response(user_data)
    device_limit = (user or {}).get("hwidDeviceLimit")

    devices, total = await x3.get_user_hwid_devices(user_uuid)

    lines = [f"<b>{escape(label)}</b>", ""]
    if total == 0:
        lines.append("Нет подключённых устройств.")
    else:
        limit_suffix = ""
        if device_limit is not None:
            limit_suffix = f" ({total}/{device_limit})"
        lines.append(f"Подключённые устройства{limit_suffix}:")
        lines.append("")
        for idx, device in enumerate(devices, start=1):
            lines.append(_device_line(device, idx))

    lines.append("")
    lines.append(
        "Выберите устройство для удаления (❗️❗️❗️ обязательно удалите подписку "
        "из приложения на старом устройстве иначе в течении часа старое устройство "
        "повторно добавится в личный кабинет):"
    )

    btn_rows = [
        (idx, _device_button_label(device))
        for idx, device in enumerate(devices)
    ]
    return "\n".join(lines), devices, btn_rows


async def _show_devices(callback: CallbackQuery, slot_key: str = _DEFAULT_SLOT) -> None:
    ctx = await _slot_context(callback.from_user.id, slot_key)
    if not ctx:
        await callback.answer("Подписка не найдена или истекла", show_alert=True)
        await _edit_devices_screen(
            callback,
            _no_subscriptions_text(),
            _no_subscriptions_markup(),
        )
        return

    label, user_uuid, username = ctx
    text, devices, btn_rows = await _devices_screen_text(label, user_uuid, username)

    if not devices:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=BTN_BACK, callback_data="back_to_main")]
            ]
        )
        await _edit_devices_screen(callback, text, markup)
        return

    await _edit_devices_screen(
        callback,
        text,
        keyboard_devices_list(slot_key, btn_rows),
    )


@router.callback_query(F.data == "manage_devices")
async def manage_devices_entry(callback: CallbackQuery) -> None:
    await callback.answer()
    slots = await _active_slots(callback.from_user.id)
    if not slots:
        await edit_or_send_photo(
            callback,
            "manage_devices",
            _no_subscriptions_text(),
            _no_subscriptions_markup(),
        )
        return

    await _show_devices(callback, _DEFAULT_SLOT)


@router.callback_query(F.data == "dev_back_subs")
async def devices_back_to_subscriptions(callback: CallbackQuery) -> None:
    """Старые сообщения — сразу список устройств."""
    await callback.answer()
    await _show_devices(callback, _DEFAULT_SLOT)


@router.callback_query(F.data.regexp(_DEV_SUB_RE))
async def devices_pick_subscription(callback: CallbackQuery) -> None:
    """Старые сообщения с выбором подписки — сразу список устройств."""
    await callback.answer()
    await _show_devices(callback, _DEFAULT_SLOT)


def _delete_confirm_text(label: str, device_name: str) -> str:
    return (
        f"<b>{escape(label)}</b>\n\n"
        f"⚠️ Вы точно хотите удалить <b>{escape(device_name)}</b> из подписки?"
    )


async def _resolve_device_removal(
    callback: CallbackQuery,
    slot_key: str,
    device_idx: int,
) -> tuple[str, str, str, dict[str, Any]] | None:
    ctx = await _slot_context(callback.from_user.id, slot_key)
    if not ctx:
        await callback.answer("Подписка не найдена или истекла", show_alert=True)
        await _edit_devices_screen(
            callback,
            _no_subscriptions_text(),
            _no_subscriptions_markup(),
        )
        return None

    label, user_uuid, username = ctx
    devices, _total = await x3.get_user_hwid_devices(user_uuid)

    if device_idx < 0 or device_idx >= len(devices):
        await callback.answer("Устройство уже удалено", show_alert=True)
        await _show_devices(callback, slot_key)
        return None

    return label, user_uuid, username, devices[device_idx]


@router.callback_query(F.data.regexp(_DEV_RM_RE))
async def devices_delete_confirm(callback: CallbackQuery) -> None:
    match = _DEV_RM_RE.match(callback.data or "")
    if not match:
        await callback.answer()
        return

    slot_key = "main"
    device_idx = int(match.group(1))

    resolved = await _resolve_device_removal(callback, slot_key, device_idx)
    if not resolved:
        return

    label, _user_uuid, _username, device = resolved
    device_name = _device_display_name(device)

    await callback.answer()
    await _edit_devices_screen(
        callback,
        _delete_confirm_text(label, device_name),
        keyboard_devices_confirm(slot_key, device_idx),
    )


@router.callback_query(F.data.regexp(_DEV_RM_YES_RE))
async def devices_delete_device(callback: CallbackQuery) -> None:
    match = _DEV_RM_YES_RE.match(callback.data or "")
    if not match:
        await callback.answer()
        return

    slot_key = "main"
    device_idx = int(match.group(1))

    resolved = await _resolve_device_removal(callback, slot_key, device_idx)
    if not resolved:
        return

    label, user_uuid, username, device = resolved
    hwid = device.get("hwid")
    if not hwid:
        await callback.answer("Не удалось определить устройство", show_alert=True)
        return

    ok = await x3.delete_user_hwid_device(user_uuid, hwid)
    if not ok:
        await callback.answer("Не удалось удалить устройство. Попробуйте позже.", show_alert=True)
        logger.error(
            "devices_delete_device: user=%s slot=%s hwid=%s",
            callback.from_user.id,
            slot_key,
            hwid,
        )
    else:
        await callback.answer("Устройство удалено")

    text, fresh_devices, btn_rows = await _devices_screen_text(label, user_uuid, username)
    if not fresh_devices:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=BTN_BACK, callback_data="back_to_main")]
            ]
        )
        await _edit_devices_screen(callback, text, markup)
        return

    await _edit_devices_screen(
        callback,
        text,
        keyboard_devices_list(slot_key, btn_rows),
    )
