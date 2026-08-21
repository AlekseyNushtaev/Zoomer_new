import aiohttp
from typing import Dict, Optional

from aiogram import Router, F
from aiogram.types import CallbackQuery

from bot import sql
from config import (
    ADMIN_IDS,
    BOT_URL,
    PAYMENT_MAX_PENDING_PER_USER,
    PLATEGA_API_KEY,
    PLATEGA_MERCHANT_ID,
)
from keyboard import keyboard_payment_sbp, create_kb
from lexicon import dct_desc, dct_price, lexicon
from logging_config import logger
from payments.payment_limits import payment_creation_allowed
from payments.payload_source import BOT, SITE
from payments.tariff_gate import is_mobile_tariff_key, normalize_tariff_duration_key
from utils.menu_ui import edit_or_send_screen

PLATEGA_SBP_METHOD = 2
PLATEGA_CARD_METHOD = 11
PLATEGA_MIN_AMOUNT_SBP_RUB = 1
PLATEGA_MIN_AMOUNT_CARD_RUB = 1

router = Router()


class PlategaPayment:
    """Класс для работы с Platega.io API"""

    def __init__(self, api_key: str, merchant_id: str):
        self.api_key = api_key
        self.merchant_id = merchant_id
        self.base_url = "https://app.platega.io"
        self.headers = {
            "X-Secret": api_key,
            "X-MerchantId": merchant_id,
            "Content-Type": "application/json",
        }

    async def create_payment(
        self,
        amount: float,
        description: str,
        payment_method: int = 2,
        return_url: str = BOT_URL,
        failed_url: str = BOT_URL,
        payload: Optional[str] = None,
    ) -> Dict:
        """Создание платежа через Platega.io"""
        url = f"{self.base_url}/transaction/process"

        data = {
            "paymentMethod": payment_method,
            "paymentDetails": {
                "amount": float(amount),
                "currency": "RUB",
            },
            "description": description,
            "return": return_url,
            "failedUrl": failed_url,
        }

        if payload:
            data["payload"] = payload

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data, headers=self.headers) as response:
                    response_text = await response.text()

                    if response.status == 200:
                        result = await response.json()

                        return {
                            "status": result.get("status", "PENDING").lower(),
                            "url": result.get("redirect", ""),
                            "id": result.get("transactionId", ""),
                            "payment_method": result.get("paymentMethod", "UNKNOWN"),
                        }
                    logger.error(f"Platega API error {response.status}: {response_text}")
                    raise Exception(f"Ошибка создания платежа: {response.status}")

        except Exception as e:
            logger.error(f"Error creating Platega payment: {e}")
            raise

    async def check_payment(self, transaction_id: str) -> Dict:
        url = f"{self.base_url}/transaction/{transaction_id}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers) as response:
                    response_text = await response.text()
                    if response.status == 200:
                        return await response.json()
                    logger.error(f"Platega API check error {response.status}: {response_text}")
                    raise Exception(f"Ошибка проверки платежа: {response.status}")
        except Exception as e:
            logger.error(f"Error checking Platega payment: {e}")
            raise


def _platega_amount_rub(val: str, payment_method: int) -> int:
    if payment_method == PLATEGA_CARD_METHOD:
        return max(PLATEGA_MIN_AMOUNT_CARD_RUB, int(val))
    if payment_method == PLATEGA_SBP_METHOD:
        return max(PLATEGA_MIN_AMOUNT_SBP_RUB, int(val))
    return int(val)


def _platega_card_amount_rub(val: str) -> int:
    return _platega_amount_rub(val, PLATEGA_CARD_METHOD)


def _platega_method_name(payment_method: int) -> str:
    if payment_method == PLATEGA_SBP_METHOD:
        return "sbp"
    if payment_method == PLATEGA_CARD_METHOD:
        return "card"
    return "crypto"


def _build_payload(
    user_id: str,
    duration: str,
    white: bool,
    gift: bool,
    method: str,
    amount_rub: int,
    *,
    source: Optional[str] = None,
    payload_suffix: str = "",
) -> str:
    payload = (
        f"user_id:{user_id},duration:{duration},white:{white},gift:{gift},"
        f"method:{method},amount:{amount_rub}"
    )
    if source:
        payload += f",source:{source}"
    payload += payload_suffix
    return payload


async def pay(
    val: str,
    des: str,
    user_id: str,
    duration: str,
    white: bool,
    payment_method: int = 2,
    telegram_username: Optional[str] = None,
    *,
    source: Optional[str] = None,
    payload_suffix: str = "",
) -> Dict:
    """Создание платежа Platega (СБП — method 2, карта — method 11)."""
    if not await payment_creation_allowed(int(user_id), telegram_username):
        return {"status": "rate_limited", "url": "", "id": ""}
    if not PLATEGA_API_KEY or not PLATEGA_MERCHANT_ID:
        logger.error("Platega: не заданы PLATEGA_API_KEY или PLATEGA_MERCHANT_ID")
        return {"status": "error", "url": "", "id": ""}

    method = _platega_method_name(payment_method)
    amount_rub = _platega_amount_rub(val, payment_method)
    payload = _build_payload(
        user_id, duration, white, False, method, amount_rub,
        source=source or BOT, payload_suffix=payload_suffix,
    )

    platega = PlategaPayment(PLATEGA_API_KEY, PLATEGA_MERCHANT_ID)
    try:
        result = await platega.create_payment(
            amount=float(amount_rub),
            description=des,
            payment_method=payment_method,
            payload=payload,
        )

        if payment_method == PLATEGA_SBP_METHOD:
            await sql.add_platega_payment(
                int(user_id), amount_rub, result["status"], result["id"], payload, is_gift=False,
            )
        elif payment_method == PLATEGA_CARD_METHOD:
            await sql.add_platega_card_payment(
                int(user_id), amount_rub, result["status"], result["id"], payload, is_gift=False,
            )
        else:
            await sql.add_platega_crypto_payment(
                int(user_id), amount_rub, result["status"], result["id"], payload, is_gift=False,
            )

        logger.info(f"✅ Platega payment created (method={payment_method}): {result['status']}")
        logger.info(f"🔗 Payment URL: {result['url']}")
        logger.info(f"🆔 Transaction ID: {result['id']}")
        return result
    except Exception as e:
        logger.error(f"❌ Error creating Platega payment: {e}")
        return {"status": "error", "url": "", "id": ""}


async def pay_for_gift(
    val: str,
    des: str,
    user_id: str,
    duration: str,
    white: bool,
    payment_method: int = 2,
    telegram_username: Optional[str] = None,
    *,
    source: Optional[str] = None,
) -> Dict:
    """Создание подарочного платежа Platega."""
    if not await payment_creation_allowed(int(user_id), telegram_username):
        return {"status": "rate_limited", "url": "", "id": ""}
    if not PLATEGA_API_KEY or not PLATEGA_MERCHANT_ID:
        logger.error("Platega: не заданы PLATEGA_API_KEY или PLATEGA_MERCHANT_ID")
        return {"status": "error", "url": "", "id": ""}

    method = _platega_method_name(payment_method)
    amount_rub = _platega_amount_rub(val, payment_method)
    payload = _build_payload(
        user_id, duration, white, True, method, amount_rub,
        source=source or BOT,
    )

    platega = PlategaPayment(PLATEGA_API_KEY, PLATEGA_MERCHANT_ID)
    try:
        result = await platega.create_payment(
            amount=float(amount_rub),
            description=des,
            payment_method=payment_method,
            payload=payload,
        )

        if payment_method == PLATEGA_SBP_METHOD:
            await sql.add_platega_payment(
                int(user_id), amount_rub, result["status"], result["id"], payload, is_gift=True,
            )
        elif payment_method == PLATEGA_CARD_METHOD:
            await sql.add_platega_card_payment(
                int(user_id), amount_rub, result["status"], result["id"], payload, is_gift=True,
            )
        else:
            await sql.add_platega_crypto_payment(
                int(user_id), amount_rub, result["status"], result["id"], payload, is_gift=True,
            )

        logger.info(f"✅ Platega payment for gift created (method={payment_method}): {result['status']}")
        return result
    except Exception as e:
        logger.error(f"❌ Error creating Platega payment: {e}")
        return {"status": "error", "url": "", "id": ""}


async def _pay_site(
    val: str,
    des: str,
    payload_user: str,
    billing_user_id: int,
    duration: str,
    white: bool,
    is_gift: bool,
    payment_method: int,
    telegram_username: Optional[str] = None,
    payload_source: str = SITE,
) -> Dict:
    if not await payment_creation_allowed(int(billing_user_id), telegram_username):
        return {"status": "rate_limited", "url": "", "id": ""}
    if not PLATEGA_API_KEY or not PLATEGA_MERCHANT_ID:
        logger.error("Platega site: не заданы PLATEGA_API_KEY или PLATEGA_MERCHANT_ID")
        return {"status": "error", "url": "", "id": ""}

    if billing_user_id in ADMIN_IDS:
        val = "1"

    method = _platega_method_name(payment_method)
    amount_rub = _platega_amount_rub(str(val), payment_method)
    gift_str = "True" if is_gift else "False"
    payload = (
        f"user_id:{payload_user},duration:{duration},white:{white},gift:{gift_str},"
        f"method:{method},amount:{amount_rub},source:{payload_source}"
    )

    platega = PlategaPayment(PLATEGA_API_KEY, PLATEGA_MERCHANT_ID)
    try:
        result = await platega.create_payment(
            amount=float(amount_rub),
            description=des,
            payment_method=payment_method,
            payload=payload,
        )
        if payment_method == PLATEGA_SBP_METHOD:
            await sql.add_platega_payment(
                billing_user_id,
                amount_rub,
                result["status"],
                result["id"],
                payload,
                is_gift=is_gift,
            )
        else:
            await sql.add_platega_card_payment(
                billing_user_id,
                amount_rub,
                result["status"],
                result["id"],
                payload,
                is_gift=is_gift,
            )
        logger.info(f"✅ Platega site {method}: transactionId={result['id']}, amount={amount_rub}")
        return {"status": "pending", "url": result.get("url") or "", "id": result.get("id") or ""}
    except Exception as e:
        logger.error(f"❌ Platega site {method} create_payment: {e}")
        return {"status": "error", "url": "", "id": ""}


async def pay_site_sbp(
    val: str,
    des: str,
    payload_user: str,
    billing_user_id: int,
    duration: str,
    white: bool,
    is_gift: bool,
    telegram_username: Optional[str] = None,
    payload_source: str = SITE,
) -> Dict:
    """Оплата СБП Platega с сайта / subscription page."""
    return await _pay_site(
        val, des, payload_user, billing_user_id, duration, white, is_gift,
        PLATEGA_SBP_METHOD, telegram_username, payload_source,
    )


async def pay_site_card(
    val: str,
    des: str,
    payload_user: str,
    billing_user_id: int,
    duration: str,
    white: bool,
    is_gift: bool,
    telegram_username: Optional[str] = None,
    payload_source: str = SITE,
) -> Dict:
    """Оплата картой Platega с сайта / subscription page."""
    return await _pay_site(
        val, des, payload_user, billing_user_id, duration, white, is_gift,
        PLATEGA_CARD_METHOD, telegram_username, payload_source,
    )


def _duration_from_callback(data: str, prefix: str, gift_prefix: str) -> tuple[str, bool]:
    gift_flag = False
    if data.startswith(gift_prefix):
        gift_flag = True
        duration = data[len(gift_prefix):]
    else:
        duration = data[len(prefix):]
    return duration, gift_flag


@router.callback_query(F.data.startswith("wata_sbp_"))
async def process_payment_platega_from_sbp_button(callback: CallbackQuery):
    await _handle_platega_button_callback(callback, "sbp")


@router.callback_query(F.data.startswith("wata_card_"))
async def process_payment_platega_from_card_button(callback: CallbackQuery):
    await _handle_platega_button_callback(callback, "card")


async def _handle_platega_button_callback(callback: CallbackQuery, ui_kind: str) -> None:
    data = callback.data or ""
    if is_mobile_tariff_key(data):
        await callback.answer(lexicon["mobile_purchase_disabled"], show_alert=True)
        return
    await callback.answer()
    if ui_kind == "sbp":
        prefix, gift_prefix = "wata_sbp_r_", "wata_sbp_gift_r_"
        payment_method = PLATEGA_SBP_METHOD
        btn = "⚡ Оплатить СБП"
        log_label = "Platega (кнопка СБП)"
    else:
        prefix, gift_prefix = "wata_card_r_", "wata_card_gift_r_"
        payment_method = PLATEGA_CARD_METHOD
        btn = "💳 Оплатить картой РФ"
        log_label = "Platega (кнопка карта)"
    duration, gift_flag = _duration_from_callback(data, prefix, gift_prefix)
    desc_key = duration
    rub_amount = dct_price[duration]
    if callback.from_user.id in ADMIN_IDS:
        rub_amount = 1
    user_id = str(callback.from_user.id)
    duration = normalize_tariff_duration_key(duration)
    tg_uname = callback.from_user.username

    if gift_flag:
        payment_info = await pay_for_gift(
            val=str(rub_amount),
            des=f"Подписка в подарок {dct_desc[desc_key]}",
            user_id=user_id,
            duration=duration,
            white=False,
            payment_method=payment_method,
            telegram_username=tg_uname,
        )
    else:
        payment_info = await pay(
            val=str(rub_amount),
            des=dct_desc[desc_key],
            user_id=user_id,
            duration=duration,
            white=False,
            payment_method=payment_method,
            telegram_username=tg_uname,
        )

    if payment_info["status"] == "pending":
        try:
            text = lexicon["payment_link"].format(wl_bonus="")
            if gift_flag:
                text += "\n\nДля оплаты <b>подарочной подписки</b> перейдите по ссылке:"
            else:
                text += "\n\nДля оплаты тарифа перейдите по ссылке:"
            await edit_or_send_screen(
                callback,
                text,
                keyboard_payment_sbp(btn, payment_info["url"]),
            )
            logger.info(
                f"Юзер {user_id} создал {log_label} "
                f"{_platega_amount_rub(str(rub_amount), payment_method)} руб (тариф в боте {rub_amount})"
            )
        except Exception as e:
            logger.error(f"Platega {ui_kind} UI: {e}")
            await callback.message.answer(lexicon["error_payment"], reply_markup=create_kb(1, back_to_main="🔙 Назад"))
    elif payment_info["status"] == "rate_limited":
        await callback.message.answer(
            lexicon["payment_too_many_pending"].format(PAYMENT_MAX_PENDING_PER_USER),
            reply_markup=create_kb(1, back_to_main="🔙 Назад"),
        )


@router.callback_query(F.data.startswith("card_"))
async def process_payment_card(callback: CallbackQuery):
    await callback.answer()
    gift_flag = False
    if "gift_" in callback.data:
        gift_flag = True
    duration = callback.data.replace("card_r_", "").replace("card_gift_r_", "")

    if is_mobile_tariff_key(duration):
        await callback.answer(lexicon["mobile_purchase_disabled"], show_alert=True)
        return

    desc_key = duration
    rub_amount = dct_price[duration]
    if callback.from_user.id in ADMIN_IDS:
        rub_amount = 1
    user_id = str(callback.from_user.id)
    duration = normalize_tariff_duration_key(duration)
    tg_uname = callback.from_user.username

    if gift_flag:
        payment_info = await pay_for_gift(
            val=str(rub_amount),
            des=f"Подписка в подарок {dct_desc[desc_key]}",
            user_id=user_id,
            duration=duration,
            white=False,
            payment_method=PLATEGA_CARD_METHOD,
            telegram_username=tg_uname,
        )
    else:
        payment_info = await pay(
            val=str(rub_amount),
            des=dct_desc[desc_key],
            user_id=user_id,
            duration=duration,
            white=False,
            payment_method=PLATEGA_CARD_METHOD,
            telegram_username=tg_uname,
        )

    if payment_info["status"] == "pending":
        try:
            text = lexicon["payment_link"].format(wl_bonus="")
            if gift_flag:
                text += "\n\nДля оплаты <b>подарочной подписки</b> перейдите по ссылке:"
            else:
                text += "\n\nДля оплаты тарифа перейдите по ссылке:"
            await edit_or_send_screen(
                callback,
                text,
                keyboard_payment_sbp("💳 Оплатить по карте", payment_info["url"]),
            )
            logger.info(
                f"Юзер {user_id} создал счет на оплату по карте "
                f"{'подарка' if gift_flag else ''} {rub_amount} руб"
            )
        except Exception as e:
            logger.error(f"Ошибка при создании счета: {e}")
            await callback.message.answer(lexicon["error_payment"], reply_markup=create_kb(1, back_to_main="🔙 Назад"))
    elif payment_info["status"] == "rate_limited":
        await callback.message.answer(
            lexicon["payment_too_many_pending"].format(PAYMENT_MAX_PENDING_PER_USER),
            reply_markup=create_kb(1, back_to_main="🔙 Назад"),
        )


# СБП и карта РФ переведены с FreeKassa на Platega; хендлеры FreeKassa отключены в pay_freekassa.py.

# @router.callback_query(F.data.startswith('sbp_'))
# async def process_payment_sbp(callback: CallbackQuery):
#     ...

# @router.callback_query(F.data.startswith('crypto_'))
# async def process_payment_crypto(callback: CallbackQuery):
#     ...
