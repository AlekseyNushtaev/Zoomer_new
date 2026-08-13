import asyncio
import random

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InaccessibleMessage,
    InputMediaPhoto,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
)

from bot import bot, sql
from config import ADMIN_IDS, DISCOUNT_PUSH_PHOTO_ID, PAYMENT_MAX_PENDING_PER_USER
from keyboard import (
    STYLE_SUCCESS,
    create_kb,
    keyboard_discount_push_buy,
    keyboard_discount_push_payment,
    keyboard_discount_push_reveal,
    keyboard_discount_push_tariffs,
    keyboard_payment_stars,
)
from lexicon import dct_desc_discount_33, dct_price_discount_33, lexicon
from logging_config import logger
from payments.pay_cryptobot import create_cryptobot_payment
from payments.pay_freekassa import pay
from payments.payload_source import BOT
from telegram_ids import is_telegram_chat_id

router = Router()

_BROADCAST_USER_DELAY = 0.05
_VALID_DURATIONS = frozenset(dct_price_discount_33.keys())
_DISCOUNT_PAYLOAD_SUFFIX = ",discount"
# индекс field_bool_3 в кортеже get_user (_user_tuple)
_USER_TUPLE_FIELD_BOOL_3 = 26


def _initial_caption(tickets: int, winning_ticket: int) -> str:
    return (
        "<b>Поздравляем!</b> Вы выиграли в нашем последнем розыгрыше с главным призом — "
        "<b>Apple Vision Pro.</b>\n\n"
        f"Количество билетов: <b>{tickets}</b>\n"
        f"Победный билет: <b>#{winning_ticket}</b>\n\n"
        "Чтобы узнать, какой именно приз вам достался, нажмите на кнопку ниже."
    )


_REVEAL_CAPTION = (
    "Ваш приз: <b>персональная скидка 33%</b> на приобретение VPN\n\n"
    "Приз доступен в течение 24 ч. — воспользуйтесь скидкой до окончания срока действия."
)

_BUY_CAPTION = (
    "У вас <b>персональная скидка 33%</b>\n"
    "⬇️ Выберите тариф ⬇️"
)


def _tariff_payment_caption(duration: str) -> str:
    name = dct_desc_discount_33[duration]
    price = dct_price_discount_33[duration]
    return (
        f"Вы выбрали тариф {name} с <b>персональной скидкой 33%</b> всего за {price} руб.\n\n"
        "Выберите способ оплаты:"
    )


async def _try_delete_push_message(chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        pass


async def _replace_push_photo(
    chat_id: int,
    message_id: int,
    photo_id: str,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    await _try_delete_push_message(chat_id, message_id)
    await bot.send_photo(
        chat_id,
        photo=photo_id,
        caption=caption,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


async def _edit_push_photo(
    callback: CallbackQuery,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    message = callback.message
    chat_id = message.chat.id
    message_id = message.message_id

    if isinstance(message, InaccessibleMessage):
        await _replace_push_photo(
            chat_id,
            message_id,
            DISCOUNT_PUSH_PHOTO_ID,
            caption,
            reply_markup,
        )
        return

    photo_id = message.photo[-1].file_id if message.photo else DISCOUNT_PUSH_PHOTO_ID
    try:
        await message.edit_media(
            media=InputMediaPhoto(media=photo_id, caption=caption, parse_mode="HTML"),
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as e:
        logger.warning("dpush edit_media failed for chat_id={}, sending new photo: {}", chat_id, e)
        await _replace_push_photo(chat_id, message_id, photo_id, caption, reply_markup)


def _duration_from_callback(data: str, prefix: str) -> str | None:
    duration = data[len(prefix):]
    return duration if duration in _VALID_DURATIONS else None


async def _delete_and_answer(callback: CallbackQuery, text: str, reply_markup) -> None:
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


@router.callback_query(F.data == "dpush_reveal")
async def discount_push_reveal(callback: CallbackQuery):
    await callback.answer()
    try:
        await _edit_push_photo(callback, _REVEAL_CAPTION, keyboard_discount_push_buy())
    except Exception:
        logger.exception("dpush_reveal edit failed for user_id={}", callback.from_user.id)


@router.callback_query(F.data == "dpush_buy")
async def discount_push_buy(callback: CallbackQuery):
    uid = callback.from_user.id
    user_data = await sql.get_user(uid)
    if user_data is None:
        await sql.add_user(uid, False)
        user_data = await sql.get_user(uid)
    if user_data is not None and user_data[_USER_TUPLE_FIELD_BOOL_3]:
        await callback.answer("Вы уже воспользовались скидкой!", show_alert=True)
        return

    await callback.answer()
    try:
        await _edit_push_photo(callback, _BUY_CAPTION, keyboard_discount_push_tariffs())
    except Exception:
        logger.exception("dpush_buy edit failed for user_id={}", uid)


@router.callback_query(F.data == "dpush_back_tariffs")
async def discount_push_back_tariffs(callback: CallbackQuery):
    await callback.answer()
    try:
        await _edit_push_photo(callback, _BUY_CAPTION, keyboard_discount_push_tariffs())
    except Exception:
        logger.exception("dpush_back_tariffs edit failed for user_id={}", callback.from_user.id)


@router.callback_query(F.data.startswith("dpush_tariff_"))
async def discount_push_select_tariff(callback: CallbackQuery):
    duration = _duration_from_callback(callback.data or "", "dpush_tariff_")
    if not duration:
        await callback.answer("Неизвестный тариф.", show_alert=True)
        return
    await callback.answer()
    try:
        await _edit_push_photo(
            callback,
            _tariff_payment_caption(duration),
            keyboard_discount_push_payment(duration),
        )
    except Exception:
        logger.exception("dpush_tariff edit failed for user_id={}", callback.from_user.id)


async def _create_discount_fk_payment(
    callback: CallbackQuery,
    duration: str,
    ui_kind: str,
) -> None:
    rub_amount = dct_price_discount_33[duration]
    if callback.from_user.id in ADMIN_IDS:
        rub_amount = 10 if ui_kind == "sbp" else 1
    user_id = str(callback.from_user.id)
    description = f"{dct_desc_discount_33[duration]} (скидка 33%)"

    payment_info = await pay(
        val=str(rub_amount),
        des=description,
        user_id=user_id,
        duration=duration,
        white=False,
        ui_kind=ui_kind,
        payload_suffix=_DISCOUNT_PAYLOAD_SUFFIX,
    )

    btn = "⚡ Оплатить СБП" if ui_kind == "sbp" else "💳 Оплатить картой РФ"
    if payment_info["status"] == "pending":
        text = lexicon["payment_link"].format(wl_bonus="") + "\n\nДля оплаты тарифа перейдите по ссылке:"
        await _delete_and_answer(
            callback,
            text,
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=btn, url=payment_info["url"], style=STYLE_SUCCESS)]
            ]),
        )
        logger.info(
            "dpush: user %s created %s payment %s rub (tariff %s)",
            user_id, ui_kind, rub_amount, duration,
        )
    elif payment_info["status"] == "rate_limited":
        await _delete_and_answer(
            callback,
            lexicon["payment_too_many_pending"].format(PAYMENT_MAX_PENDING_PER_USER),
            create_kb(1, back_to_main="🔙 Назад"),
        )
    else:
        await _delete_and_answer(
            callback,
            lexicon.get("error_payment", "Произошла ошибка при создании счета."),
            create_kb(1, back_to_main="🔙 Назад"),
        )


@router.callback_query(F.data.startswith("dpush_wata_sbp_"))
async def discount_push_pay_sbp(callback: CallbackQuery):
    duration = _duration_from_callback(callback.data or "", "dpush_wata_sbp_")
    if not duration:
        await callback.answer("Неизвестный тариф.", show_alert=True)
        return
    await callback.answer()
    await _create_discount_fk_payment(callback, duration, "sbp")


@router.callback_query(F.data.startswith("dpush_wata_card_"))
async def discount_push_pay_card(callback: CallbackQuery):
    duration = _duration_from_callback(callback.data or "", "dpush_wata_card_")
    if not duration:
        await callback.answer("Неизвестный тариф.", show_alert=True)
        return
    await callback.answer()
    await _create_discount_fk_payment(callback, duration, "card")


@router.callback_query(F.data.startswith("dpush_crypto_"))
async def discount_push_pay_crypto(callback: CallbackQuery):
    duration = _duration_from_callback(callback.data or "", "dpush_crypto_")
    if not duration:
        await callback.answer("Неизвестный тариф.", show_alert=True)
        return
    await callback.answer()

    rub_amount = dct_price_discount_33[duration]
    if callback.from_user.id in ADMIN_IDS:
        rub_amount = 1
    description = f"{dct_desc_discount_33[duration]} (скидка 33%)"

    result = await create_cryptobot_payment(
        rub_amount=rub_amount,
        description=description,
        user_id=callback.from_user.id,
        duration=duration,
        white=False,
        is_gift=False,
        telegram_username=callback.from_user.username,
        payload_source=BOT,
        payload_suffix=_DISCOUNT_PAYLOAD_SUFFIX,
    )

    if result["status"] == "pending":
        text = lexicon["payment_link"].format(wl_bonus="") + "\n\nДля оплаты тарифа перейдите по ссылке:"
        pay_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"💎 Оплатить криптовалютой ({rub_amount} ₽)",
                url=result["url"],
                style=STYLE_SUCCESS,
            )]
        ])
        await _delete_and_answer(callback, text, pay_keyboard)
    elif result.get("status") == "rate_limited":
        await _delete_and_answer(
            callback,
            lexicon["payment_too_many_pending"].format(PAYMENT_MAX_PENDING_PER_USER),
            create_kb(1, back_to_main="🔙 Назад"),
        )
    else:
        await _delete_and_answer(
            callback,
            lexicon.get("error_payment", "Произошла ошибка при создании счета."),
            create_kb(1, back_to_main="🔙 Назад"),
        )


@router.callback_query(F.data.startswith("dpush_stars_"))
async def discount_push_pay_stars(callback: CallbackQuery):
    duration = _duration_from_callback(callback.data or "", "dpush_stars_")
    if not duration:
        await callback.answer("Неизвестный тариф.", show_alert=True)
        return
    await callback.answer()

    stars_amount = dct_price_discount_33[duration]
    if callback.from_user.id in ADMIN_IDS:
        stars_amount = 1
    user_id = str(callback.from_user.id)
    payload = (
        f"user_id:{user_id},duration:{duration},white:False,gift:False,"
        f"method:stars,amount:{stars_amount},source:{BOT}{_DISCOUNT_PAYLOAD_SUFFIX}"
    )
    prices = [LabeledPrice(label="XTR", amount=stars_amount)]
    title = f"Оплата подписки на {duration} дней (скидка 33%)."

    try:
        await callback.message.delete()
    except Exception:
        pass

    await bot.send_invoice(
        callback.from_user.id,
        title=title,
        description=lexicon["payment_link"].format(wl_bonus=""),
        prices=prices,
        provider_token="",
        payload=payload,
        currency="XTR",
        reply_markup=keyboard_payment_stars(stars_amount),
    )


@router.message(Command(commands=["discount_push"]))
async def discount_push_command(message: Message):
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        return

    if not DISCOUNT_PUSH_PHOTO_ID:
        await message.answer(
            "❌ Не задан DISCOUNT_PUSH_PHOTO_ID в config.py / .env. "
            "Укажите file_id картинки и повторите команду."
        )
        return

    user_ids = await sql.select_user_ids_for_broadcast("all_users", exclude_today=False)
    if not user_ids:
        await message.answer("❌ Нет незаблокированных пользователей в БД.")
        return

    total = len(user_ids)
    admin_chat_id = message.chat.id
    await message.answer(
        f"📣 Рассылка discount_push начата.\n"
        f"Получателей: <b>{total}</b> (is_delete=False).",
        parse_mode="HTML",
    )

    sent = 0
    failed = 0
    skipped_non_tg = 0

    for user_id in user_ids:
        if not is_telegram_chat_id(user_id):
            skipped_non_tg += 1
            continue
        tickets = random.randint(10, 20)
        winning_ticket = random.randint(10000, 20000)
        caption = _initial_caption(tickets, winning_ticket)
        try:
            await bot.send_photo(
                user_id,
                photo=DISCOUNT_PUSH_PHOTO_ID,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard_discount_push_reveal(),
            )
            sent += 1
            if sent % 1000 == 0:
                try:
                    await bot.send_message(
                        admin_chat_id,
                        f"discount_push: отправлено — {sent} / {total}",
                    )
                except Exception as notify_err:
                    logger.warning("discount_push: progress notify failed: {}", notify_err)
        except Exception as e:
            failed += 1
            logger.warning("discount_push: send failed user_id={}: {}", user_id, e)

        await asyncio.sleep(_BROADCAST_USER_DELAY)

    await message.answer(
        "✅ Рассылка discount_push завершена.\n"
        f"• В списке: {total}\n"
        f"• Отправлено: {sent}\n"
        f"• Ошибок: {failed}\n"
        f"• Пропущено (не Telegram user id): {skipped_non_tg}"
    )
    logger.success("discount_push: sent {} / {} users", sent, total)
