"""Оплата дополнительного трафика Антиглушилка."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice

from bot import bot
from utils.menu_ui import edit_or_send_screen
from config import ADMIN_IDS, PAYMENT_MAX_PENDING_PER_USER
from keyboard import BTN_BACK, create_kb, keyboard_payment_sbp, keyboard_payment_stars
from lexicon import lexicon
from logging_config import logger
from payments.pay_cryptobot import create_cryptobot_payment
from payments.pay_platega import pay as pay_platega, PLATEGA_CARD_METHOD, PLATEGA_SBP_METHOD
from payments.payload_source import BOT
from wl_traffic.constants import WL_TRAFFIC_TARIFFS

router = Router()


def _traffic_duration(gb: str) -> str:
    return f"traffic{gb}"


def _traffic_price(gb: str, user_id: int) -> int:
    price = WL_TRAFFIC_TARIFFS.get(gb, 0)
    if user_id in ADMIN_IDS:
        return 10
    return price


@router.callback_query(F.data.startswith("wl_traffic_sbp_"))
async def wl_traffic_pay_sbp(callback: CallbackQuery):
    await _pay_rub(callback, "sbp")


@router.callback_query(F.data.startswith("wl_traffic_card_"))
async def wl_traffic_pay_card(callback: CallbackQuery):
    await _pay_rub(callback, "card")


async def _pay_rub(callback: CallbackQuery, ui_kind: str) -> None:
    await callback.answer()
    gb = (callback.data or "").rsplit("_", 1)[-1]
    if gb not in WL_TRAFFIC_TARIFFS:
        return

    user_id = str(callback.from_user.id)
    price = _traffic_price(gb, callback.from_user.id)
    duration = _traffic_duration(gb)

    payment_info = await pay_platega(
        val=str(price),
        des=f"Трафик Антиглушилка {gb} GB",
        user_id=user_id,
        duration=duration,
        white=False,
        payment_method=PLATEGA_CARD_METHOD if ui_kind == "card" else PLATEGA_SBP_METHOD,
    )

    btn = "⚡ Оплатить СБП" if ui_kind == "sbp" else "💳 Оплатить картой РФ"
    if payment_info["status"] == "pending":
        await edit_or_send_screen(
            callback,
            lexicon["wl_traffic_payment_link"].format(gb=gb),
            keyboard_payment_sbp(btn, payment_info["url"]),
            photo_key="buy_traffic",
        )
    elif payment_info["status"] == "rate_limited":
        await callback.message.answer(
            lexicon["payment_too_many_pending"].format(PAYMENT_MAX_PENDING_PER_USER),
            reply_markup=create_kb(1, back_to_main=BTN_BACK),
        )
    else:
        await callback.message.answer(
            lexicon["error_payment"],
            reply_markup=create_kb(1, back_to_main=BTN_BACK),
        )


@router.callback_query(F.data.startswith("wl_traffic_stars_"))
async def wl_traffic_pay_stars(callback: CallbackQuery):
    await callback.answer()
    gb = (callback.data or "").replace("wl_traffic_stars_", "")
    if gb not in WL_TRAFFIC_TARIFFS:
        return

    user_id = str(callback.from_user.id)
    stars_amount = _traffic_price(gb, callback.from_user.id)
    duration = _traffic_duration(gb)
    payload = (
        f"user_id:{user_id},duration:{duration},white:False,gift:False,"
        f"method:stars,amount:{stars_amount},source:{BOT}"
    )

    await bot.send_invoice(
        callback.from_user.id,
        title=f"Трафик Антиглушилка {gb} GB",
        description=lexicon["wl_traffic_payment_intro"].format(gb=gb, price=stars_amount),
        prices=[LabeledPrice(label="XTR", amount=stars_amount)],
        provider_token="",
        payload=payload,
        currency="XTR",
        reply_markup=keyboard_payment_stars(stars_amount),
    )


@router.callback_query(F.data.startswith("wl_traffic_crypto_"))
async def wl_traffic_pay_crypto(callback: CallbackQuery):
    await callback.answer()
    gb = (callback.data or "").replace("wl_traffic_crypto_", "")
    if gb not in WL_TRAFFIC_TARIFFS:
        return

    user_id = callback.from_user.id
    rub_amount = _traffic_price(gb, user_id)
    duration = _traffic_duration(gb)

    result = await create_cryptobot_payment(
        rub_amount=rub_amount,
        description=f"Трафик Антиглушилка {gb} GB",
        user_id=user_id,
        duration=duration,
        white=False,
        is_gift=False,
        payload_source=BOT,
    )

    if result["status"] == "pending":
        pay_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"💎 Оплатить криптой · {rub_amount} ₽",
                url=result["url"],
            )]
        ])
        await edit_or_send_screen(
            callback,
            lexicon["wl_traffic_payment_link"].format(gb=gb),
            pay_keyboard,
            photo_key="buy_traffic",
        )
    else:
        await callback.message.answer(
            lexicon.get("error_payment", "Произошла ошибка при создании счета."),
            reply_markup=create_kb(1, back_to_main=BTN_BACK),
        )
