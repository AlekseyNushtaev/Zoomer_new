import aiohttp
from typing import Dict, Optional

from aiogram import Router, F
from aiogram.types import CallbackQuery

from bot import sql
from config import PLATEGA_API_KEY, PLATEGA_MERCHANT_ID, ADMIN_IDS, PAYMENT_MAX_PENDING_PER_USER
from utils.menu_ui import edit_or_send_screen
from keyboard import keyboard_payment_sbp, create_kb
from lexicon import dct_price, dct_desc, lexicon
from logging_config import logger
from payments.payment_limits import payment_creation_allowed
from payments.payload_source import BOT
from payments.tariff_gate import is_mobile_tariff_key, normalize_tariff_duration_key

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
            "Content-Type": "application/json"
        }

    async def create_payment(
            self,
            amount: float,
            description: str,
            payment_method: int = 2,
            return_url: str = "https://t.me/zoomerskyvpn_bot",
            failed_url: str = "https://t.me/zoomerskyvpn_bot",
            payload: Optional[str] = None
    ) -> Dict:
        """Создание платежа через Platega.io"""
        url = f"{self.base_url}/transaction/process"

        data = {
            "paymentMethod": payment_method,
            "paymentDetails": {
                "amount": float(amount),
                "currency": "RUB"
            },
            "description": description,
            "return": return_url,
            "failedUrl": failed_url
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
                            'status': result.get('status', 'PENDING').lower(),
                            'url': result.get('redirect', ''),
                            'id': result.get('transactionId', ''),
                            'payment_method': result.get('paymentMethod', 'UNKNOWN')
                        }
                    else:
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
                    else:
                        logger.error(f"Platega API check error {response.status}: {response_text}")
                        raise Exception(f"Ошибка проверки платежа: {response.status}")
        except Exception as e:
            logger.error(f"Error checking Platega payment: {e}")
            raise


async def pay(
    val: str,
    des: str,
    user_id: str,
    duration: str,
    white: bool,
    payment_method: int = 2,
    telegram_username: Optional[str] = None,
) -> Dict:
    """Создание платежа для совместимости с pay_yoo.py"""
    if not await payment_creation_allowed(int(user_id), telegram_username):
        return {"status": "rate_limited", "url": "", "id": ""}

    platega = PlategaPayment(PLATEGA_API_KEY, PLATEGA_MERCHANT_ID)
    if payment_method == 2:
        method = 'sbp'
    elif payment_method == 11:
        method = 'card'
    else:
        method = 'crypto'
    payload = (
        f"user_id:{user_id},duration:{duration},white:{white},gift:False,"
        f"method:{method},amount:{int(val)},source:{BOT}"
    )

    try:
        result = await platega.create_payment(
            amount=float(val),
            description=des,
            payment_method=payment_method,
            payload=payload
        )

        # Асинхронная запись платежа
        if payment_method == 2:
            await sql.add_platega_payment(int(user_id), int(val), result['status'], result['id'], payload, is_gift=False)
        elif payment_method == 11:
            await sql.add_platega_card_payment(int(user_id), int(val), result['status'], result['id'], payload, is_gift=False)
        else:
            await sql.add_platega_crypto_payment(int(user_id), int(val), result['status'], result['id'], payload,
                                               is_gift=False)

        logger.info(f"✅ Platega payment created (method={payment_method}): {result['status']}")
        logger.info(f"🔗 Payment URL: {result['url']}")
        logger.info(f"🆔 Transaction ID: {result['id']}")

        return result

    except Exception as e:
        logger.error(f"❌ Error creating Platega payment: {e}")
        return {
            'status': 'error',
            'url': '',
            'id': ''
        }


async def pay_for_gift(
    val: str,
    des: str,
    user_id: str,
    duration: str,
    white: bool,
    payment_method: int = 2,
    telegram_username: Optional[str] = None,
) -> Dict:
    """Создание платежа для совместимости с pay_yoo.py"""
    if not await payment_creation_allowed(int(user_id), telegram_username):
        return {"status": "rate_limited", "url": "", "id": ""}

    platega = PlategaPayment(PLATEGA_API_KEY, PLATEGA_MERCHANT_ID)
    if payment_method == 2:
        method = 'sbp'
    elif payment_method == 11:
        method = 'card'
    else:
        method = 'crypto'
    payload = (
        f"user_id:{user_id},duration:{duration},white:{white},gift:True,"
        f"method:{method},amount:{int(val)},source:{BOT}"
    )

    try:
        result = await platega.create_payment(
            amount=float(val),
            description=des,
            payment_method=payment_method,
            payload=payload
        )

        # Асинхронная запись платежа с флагом подарка
        if payment_method == 2:
            await sql.add_platega_payment(int(user_id), int(val), result['status'], result['id'], payload, is_gift=True)
        elif payment_method == 11:
            await sql.add_platega_card_payment(int(user_id), int(val), result['status'], result['id'], payload, is_gift=True)
        else:
            await sql.add_platega_crypto_payment(int(user_id), int(val), result['status'], result['id'], payload, is_gift=True)

        logger.info(f"✅ Platega payment for gift created (method={payment_method}): {result['status']}")
        logger.info(f"🔗 Payment URL for gift: {result['url']}")
        logger.info(f"🆔 Transaction ID for gift: {result['id']}")

        return result

    except Exception as e:
        logger.error(f"❌ Error creating Platega payment: {e}")
        return {
            'status': 'error',
            'url': '',
            'id': ''
        }


@router.callback_query(F.data.startswith('sbp_'))
async def process_payment_sbp(callback: CallbackQuery):
    await callback.answer()
    gift_flag = False
    if 'gift_' in callback.data:
        gift_flag = True
    duration = callback.data.replace('sbp_r_', '').replace('sbp_gift_r_', '')

    if is_mobile_tariff_key(duration):
        await callback.answer(lexicon['mobile_purchase_disabled'], show_alert=True)
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
            payment_method=2,  # 2 = СБП QR
            telegram_username=tg_uname,
        )
    else:
        payment_info = await pay(
            val=str(rub_amount),
            des=dct_desc[desc_key],
            user_id=user_id,
            duration=duration,
            white=False,
            payment_method=2,  # 2 = СБП QR
            telegram_username=tg_uname,
        )

    if payment_info['status'] == 'pending':
        try:
            text = lexicon['payment_link'].format(wl_bonus='')
            if 'gift' in callback.data:
                text += '\n\nДля оплаты <b>подарочной подписки</b> перейдите по ссылке:'
            else:
                text += '\n\nДля оплаты тарифа перейдите по ссылке:'
            await edit_or_send_screen(
                callback,
                text,
                keyboard_payment_sbp("💳 Оплатить через СБП", payment_info['url']),
            )
            logger.info(f"Юзер {user_id} создал счет на оплату {'подарка' if gift_flag else ''} {rub_amount} руб")

        except Exception as e:
            error_message = f"Ошибка при создании счета: {str(e)}"
            logger.error(error_message)
            await callback.message.answer(lexicon['error_payment'], reply_markup=create_kb(1, back_to_main='🔙 Назад'))
    elif payment_info['status'] == 'rate_limited':
        await callback.message.answer(
            lexicon['payment_too_many_pending'].format(PAYMENT_MAX_PENDING_PER_USER),
            reply_markup=create_kb(1, back_to_main='🔙 Назад'),
        )


@router.callback_query(F.data.startswith('card_'))
async def process_payment_card(callback: CallbackQuery):
    await callback.answer()
    gift_flag = False
    if 'gift_' in callback.data:
        gift_flag = True
    duration = callback.data.replace('card_r_', '').replace('card_gift_r_', '')

    if is_mobile_tariff_key(duration):
        await callback.answer(lexicon['mobile_purchase_disabled'], show_alert=True)
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
            payment_method=11,
            telegram_username=tg_uname,
        )
    else:
        payment_info = await pay(
            val=str(rub_amount),
            des=dct_desc[desc_key],
            user_id=user_id,
            duration=duration,
            white=False,
            payment_method=11,
            telegram_username=tg_uname,
        )

    if payment_info['status'] == 'pending':
        try:
            text = lexicon['payment_link'].format(wl_bonus='')
            if 'gift' in callback.data:
                text += '\n\nДля оплаты <b>подарочной подписки</b> перейдите по ссылке:'
            else:
                text += '\n\nДля оплаты тарифа перейдите по ссылке:'
            await edit_or_send_screen(
                callback,
                text,
                keyboard_payment_sbp("💳 Оплатить по карте", payment_info['url']),
            )
            logger.info(f"Юзер {user_id} создал счет на оплату по карте {'подарка' if gift_flag else ''} {rub_amount} руб")

        except Exception as e:
            error_message = f"Ошибка при создании счета: {str(e)}"
            logger.error(error_message)
            await callback.message.answer(lexicon['error_payment'], reply_markup=create_kb(1, back_to_main='🔙 Назад'))
    elif payment_info['status'] == 'rate_limited':
        await callback.message.answer(
            lexicon['payment_too_many_pending'].format(PAYMENT_MAX_PENDING_PER_USER),
            reply_markup=create_kb(1, back_to_main='🔙 Назад'),
        )


# @router.callback_query(F.data.startswith('crypto_'))
# async def process_payment_crypto(callback: CallbackQuery):
#     gift_flag = False
#     white_flag = False
#     data = callback.data
#     user_id = str(callback.from_user.id)
#
#     if 'gift_' in data:
#         gift_flag = True
#
#     if gift_flag:
#         duration = data.replace(f'crypto_gift_r_', '')
#     else:
#         duration = data.replace(f'crypto_r_', '')
#
#     rub_amount = dct_price[duration]
#     desc_key = duration
#
#     if 'white' in duration:
#         white_flag = True
#         duration = duration.replace('white_', '')
#     if 'old' in duration:
#         duration = duration.replace('old', '')
#
#     if callback.from_user.id in ADMIN_IDS:
#         rub_amount = 1
#
#     if gift_flag:
#         payment_info = await pay_for_gift(
#             val=str(rub_amount),
#             des=f"Подписка в подарок {dct_desc[desc_key]}",
#             user_id=user_id,
#             duration=duration,
#             white=white_flag,
#             payment_method=13,
#         )
#     else:
#         payment_info = await pay(
#             val=str(rub_amount),
#             des=dct_desc[desc_key],
#             user_id=user_id,
#             duration=duration,
#             white=white_flag,
#             payment_method=13
#         )
#
#     if payment_info['status'] == 'pending':
#         try:
#             text = lexicon['payment_link']
#             if white_flag:
#                 text = lexicon['payment_link_white']
#             if 'gift' in callback.data:
#                 text += '\n\nДля оплаты <b>подарочной подписки</b> перейдите по ссылке:'
#             else:
#                 text += '\n\nДля оплаты тарифа перейдите по ссылке:'
#             await callback.message.edit_text(
#                 text=text,
#                 reply_markup=keyboard_payment_sbp("💎 Оплатить криптовалютой", payment_info['url'])
#             )
#             logger.info(f"Юзер {user_id} создал счет на оплату криптой {'подарка' if gift_flag else ''} {rub_amount} руб")
#
#         except Exception as e:
#             error_message = f"Ошибка при создании счета: {str(e)}"
#             logger.error(error_message)
#             await callback.message.answer(lexicon['error_payment'], reply_markup=create_kb(1, back_to_main='🔙 Назад'))
