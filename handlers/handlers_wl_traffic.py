"""Обработчики профиля и покупки трафика Антиглушилка."""
from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot import sql, x3
from keyboard import (
    BTN_BACK,
    create_kb,
    keyboard_profile,
    keyboard_wl_traffic_payment_method,
    keyboard_wl_traffic_tariffs,
)
from lexicon import lexicon
from wl_traffic.constants import (
    PROFILE_CB,
    WL_TRAFFIC_BUY_CB,
    WL_TRAFFIC_BUY_SUB_CB,
    WL_TRAFFIC_TARIFFS,
)
from wl_traffic.service import get_wl_used_gb_for_user

router = Router()


def _format_sub_end(user_data: tuple) -> str:
    from wl_traffic.service import is_forever_end_date

    sub_end = user_data[9] if len(user_data) > 9 else None
    if sub_end is None:
        return "—"
    if is_forever_end_date(sub_end):
        return "Навсегда ♾️"
    if sub_end.tzinfo is None:
        aware = sub_end.replace(tzinfo=timezone.utc)
    else:
        aware = sub_end.astimezone(timezone.utc)
    if aware <= datetime.now(timezone.utc):
        return "истекла"
    return aware.strftime("%d.%m.%Y")


async def _send_user_profile(callback: CallbackQuery) -> None:
    uid = callback.from_user.id
    user_data = await sql.get_user(uid)
    if not user_data:
        await callback.message.answer(
            "❌ Профиль не найден.",
            reply_markup=create_kb(1, back_to_main=BTN_BACK),
        )
        return

    used_gb, limit_gb = await sql.get_wl_limits(uid)
    used_gb = await get_wl_used_gb_for_user(x3, uid, used_gb, sql=sql)
    remaining_gb = max(0.0, round(limit_gb - used_gb, 2))

    await callback.message.answer(
        text=lexicon["user_profile"].format(
            sub_end=_format_sub_end(user_data),
            limit_gb=limit_gb,
            used_gb=used_gb,
            remaining_gb=remaining_gb,
        ),
        parse_mode="HTML",
        reply_markup=keyboard_profile(),
    )


@router.callback_query(F.data == PROFILE_CB)
async def user_profile_cb(callback: CallbackQuery):
    await callback.answer()
    await _send_user_profile(callback)


@router.callback_query(F.data == "back_to_profile")
async def back_to_profile_cb(callback: CallbackQuery):
    await callback.answer()
    await _send_user_profile(callback)


@router.callback_query(F.data.in_({WL_TRAFFIC_BUY_CB, WL_TRAFFIC_BUY_SUB_CB}))
async def wl_traffic_buy_cb(callback: CallbackQuery):
    back_callback = PROFILE_CB if callback.data == WL_TRAFFIC_BUY_CB else "buy_vpn_self"
    await callback.answer()
    await callback.message.answer(
        text="📦 Выберите пакет трафика для сервера <b>Антиглушилка</b>:",
        parse_mode="HTML",
        reply_markup=keyboard_wl_traffic_tariffs(back_callback=back_callback),
    )


@router.callback_query(F.data.regexp(r"^wl_traffic(_sub)?_\d+$"))
async def wl_traffic_tariff_cb(callback: CallbackQuery):
    await callback.answer()
    data = callback.data or ""
    from_sub = data.startswith("wl_traffic_sub_")
    gb = data.rsplit("_", 1)[-1]
    if gb not in WL_TRAFFIC_TARIFFS:
        return

    price = WL_TRAFFIC_TARIFFS[gb]
    back_cb = WL_TRAFFIC_BUY_SUB_CB if from_sub else WL_TRAFFIC_BUY_CB
    await callback.message.answer(
        text=lexicon["wl_traffic_payment_intro"].format(gb=gb, price=price),
        parse_mode="HTML",
        reply_markup=keyboard_wl_traffic_payment_method(gb, back_callback=back_cb),
    )
