import asyncio
from datetime import datetime

from aiogram import Router, F
from aiogram.types import CallbackQuery

from bot import bot, sql, x3
from config import CHECKER_ID
from keyboard import (
    keyboard_start_prize_claim,
    keyboard_start_prize_hurry,
    keyboard_start_prize_reveal,
    keyboard_tariff,
    keyboard_tariff_bonus,
)
from lexicon import lexicon
from logging_config import logger
from telegram_ids import is_telegram_chat_id
from utils.menu_photos import menu_photo
from utils.menu_ui import edit_or_send_photo

router = Router()

_USER_TUPLE_IN_PANEL = 4
_USER_TUPLE_RESERVE_FIELD = 8
_START_PRIZE_DELAY_SEC = 10
_CLAIM_WATCH_SEC = 600
_claim_watchers: set[int] = set()


def schedule_start_prize(user_id: int) -> None:
    if not is_telegram_chat_id(user_id):
        return
    asyncio.create_task(_send_start_prize_later(user_id))


async def _notify_checker(text: str) -> None:
    if CHECKER_ID is None:
        return
    try:
        await bot.send_message(chat_id=CHECKER_ID, text=text)
    except Exception as e:
        logger.error("start_prize: не удалось отправить CHECKER_ID: {}", e)


def _fmt_pay_time(tc: datetime | None) -> str:
    if tc is None:
        return "—"
    return tc.strftime("%Y-%m-%d %H:%M")


async def _buy_self_keyboard(uid: int):
    user_data = await sql.get_user(uid)
    in_panel = bool(user_data and len(user_data) > _USER_TUPLE_IN_PANEL and user_data[_USER_TUPLE_IN_PANEL])
    result_active = await x3.activ(str(uid))
    if result_active.get("activ") == "🔎 - Не подключён" and not in_panel:
        return keyboard_tariff_bonus()
    return keyboard_tariff()


async def _show_buy_self(callback: CallbackQuery) -> None:
    kb = await _buy_self_keyboard(callback.from_user.id)
    await edit_or_send_photo(
        callback,
        "buy_subscription",
        lexicon["buy"],
        kb,
    )


async def _send_start_prize_later(user_id: int) -> None:
    try:
        await asyncio.sleep(_START_PRIZE_DELAY_SEC)
        user_data = await sql.get_user(user_id)
        if user_data and len(user_data) > _USER_TUPLE_RESERVE_FIELD and user_data[_USER_TUPLE_RESERVE_FIELD]:
            return
        await bot.send_photo(
            chat_id=user_id,
            photo=menu_photo("start_prize_win"),
            caption=lexicon["start_prize_win"],
            parse_mode="HTML",
            reply_markup=keyboard_start_prize_reveal(),
        )
        logger.info("start_prize: отправлено user_id={}", user_id)
    except Exception:
        logger.exception("start_prize: не удалось отправить user_id={}", user_id)


async def _watch_claim_purchase(user_id: int) -> None:
    try:
        await asyncio.sleep(_CLAIM_WATCH_SEC)
        user_data = await sql.get_user(user_id)
        bought = bool(
            user_data
            and len(user_data) > _USER_TUPLE_RESERVE_FIELD
            and user_data[_USER_TUPLE_RESERVE_FIELD]
        )
        if bought:
            pay_rows = await sql.get_user_subscription_payment_report(user_id)
            lines = [
                f"• {_fmt_pay_time(tc)} — {kind} — {method} — {detail}"
                for tc, kind, method, detail in pay_rows
            ]
            body = f"{user_id} купил подписку со стартого байта"
            if lines:
                body += "\n" + "\n".join(lines)
            else:
                body += "\nНет confirmed-платежей"
            await _notify_checker(body)
            return

        await bot.send_photo(
            chat_id=user_id,
            photo=menu_photo("start_prize_discount"),
            caption=lexicon["start_prize_hurry"],
            parse_mode="HTML",
            reply_markup=keyboard_start_prize_hurry(),
        )
        logger.info("start_prize: hurry-пуш user_id={}", user_id)
    except Exception:
        logger.exception("start_prize: ошибка проверки покупки user_id={}", user_id)
    finally:
        _claim_watchers.discard(user_id)


@router.callback_query(F.data == "start_prize_reveal")
async def start_prize_reveal(callback: CallbackQuery):
    uid = callback.from_user.id
    await callback.answer()
    try:
        await edit_or_send_photo(
            callback,
            "start_prize_discount",
            lexicon["start_prize_reveal"],
            keyboard_start_prize_claim(),
        )
    except Exception:
        logger.exception("start_prize_reveal failed user_id={}", uid)
        return

    await _notify_checker(f"{uid} нажал Узнать свой приз")
    if uid not in _claim_watchers:
        _claim_watchers.add(uid)
        asyncio.create_task(_watch_claim_purchase(uid))


@router.callback_query(F.data == "start_prize_claim")
async def start_prize_claim(callback: CallbackQuery):
    await callback.answer()
    try:
        await _show_buy_self(callback)
    except Exception:
        logger.exception("start_prize_claim edit failed user_id={}", callback.from_user.id)


@router.callback_query(F.data == "start_prize_hurry")
async def start_prize_hurry(callback: CallbackQuery):
    await callback.answer()
    try:
        await _show_buy_self(callback)
    except Exception:
        logger.exception("start_prize_hurry failed user_id={}", callback.from_user.id)
