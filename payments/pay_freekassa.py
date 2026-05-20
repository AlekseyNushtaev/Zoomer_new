import hashlib
import hmac
import json
from typing import Any, Dict, Literal, Optional

import aiohttp
from aiogram import Router, F
from aiogram.types import CallbackQuery

from bot import sql
from config import (
    ADMIN_IDS,
    API_FREEKASSA,
    FREEKASSA_SERVER_IP,
    PAYMENT_MAX_PENDING_PER_USER,
    SHOP_ID_FREEKASSA,
)
from keyboard import keyboard_payment_sbp, create_kb
from lexicon import dct_price, dct_desc, lexicon
from logging_config import logger
from payments.payment_limits import payment_creation_allowed
from payments.payload_source import BOT, SITE

router = Router()

FK_API_BASE = "https://api.fk.life/v1"
FK_PAYMENT_SBP_QR = 44
FK_PAYMENT_CARD_QR = 36
FK_MIN_AMOUNT_SBP_RUB = 10
FK_MIN_AMOUNT_CARD_RUB = 50

UiKind = Literal["sbp", "card"]

SITE_SUCCESS_URL = "https://4zoomer.top/success"
SITE_FAIL_URL = "https://4zoomer.top/pricing"


def _fk_payment_system_id(ui_kind: UiKind) -> int:
    return FK_PAYMENT_SBP_QR if ui_kind == "sbp" else FK_PAYMENT_CARD_QR


def _fk_amount_rub(val: str, ui_kind: UiKind) -> int:
    minimum = FK_MIN_AMOUNT_SBP_RUB if ui_kind == "sbp" else FK_MIN_AMOUNT_CARD_RUB
    return max(minimum, int(val))


def _fk_scalar_for_signature(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        s = format(v, ".10g")
        return s
    return str(v)


def fk_build_signature(body: Dict[str, Any], api_key: str) -> str:
    sign_data = {k: v for k, v in body.items() if k != "signature"}
    keys = sorted(sign_data.keys())
    message = "|".join(_fk_scalar_for_signature(sign_data[k]) for k in keys)
    return hmac.new(
        api_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class FreekassaPayment:
    def __init__(self, api_key: str, shop_id: int):
        self.api_key = api_key
        self.shop_id = shop_id

    async def _raw_post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{FK_API_BASE}/{path.lstrip('/')}"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body) as response:
                text = await response.text()
                try:
                    data = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    logger.error(f"FreeKassa {path} не JSON: {text[:500]}")
                    raise
                if response.status != 200:
                    logger.error(f"FreeKassa {path} {response.status}: {text[:800]}")
                    raise RuntimeError(f"FreeKassa HTTP {response.status}: {text[:200]}")
                typ = data.get("type")
                if typ and typ != "success":
                    logger.error(f"FreeKassa {path}: {text[:800]}")
                    raise RuntimeError(f"FreeKassa API: {data.get('message', typ)}")
                return data

    async def create_order(
        self,
        nonce: int,
        payment_id: str,
        amount: float,
        email: str,
        ip: str,
        payment_system_id: int = FK_PAYMENT_SBP_QR,
    ) -> tuple[Dict[str, Any], str]:
        amt = float(amount)
        amount_field: Any = int(amt) if amt.is_integer() else amt
        body: Dict[str, Any] = {
            "shopId": self.shop_id,
            "nonce": nonce,
            "paymentId": payment_id,
            "amount": amount_field,
            "currency": "RUB",
            "email": email,
            "ip": ip,
            "i": payment_system_id,
        }
        signature = fk_build_signature(body, self.api_key)
        body["signature"] = signature
        result = await self._raw_post("orders/create", body)
        return result, signature

    async def get_orders(
        self,
        nonce: int,
        *,
        payment_id: Optional[str] = None,
        order_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        if payment_id is None and order_id is None:
            raise ValueError("get_orders: укажите payment_id или order_id")
        body: Dict[str, Any] = {
            "shopId": self.shop_id,
            "nonce": nonce,
        }
        if payment_id is not None:
            body["paymentId"] = payment_id
        if order_id is not None:
            body["orderId"] = order_id
        body["signature"] = fk_build_signature(body, self.api_key)
        return await self._raw_post("orders", body)


def _payment_url_from_create(data: Dict[str, Any]) -> str:
    return (data.get("location") or data.get("Location") or "").strip()


def _payload_method(ui_kind: UiKind) -> str:
    return "fk_sbp" if ui_kind == "sbp" else "fk_card"


def _db_method(ui_kind: UiKind) -> str:
    return "fk_qr_sbp" if ui_kind == "sbp" else "fk_qr_card"


async def pay(
    val: str,
    des: str,
    user_id: str,
    duration: str,
    white: bool,
    ui_kind: UiKind,
) -> Dict[str, Any]:
    if not await payment_creation_allowed(int(user_id)):
        return {"status": "rate_limited", "url": "", "id": ""}
    if not API_FREEKASSA or SHOP_ID_FREEKASSA is None:
        logger.error("FreeKassa: не заданы API_FREEKASSA или SHOP_ID_FREEKASSA")
        return {"status": "error", "url": "", "id": ""}

    pm = _payload_method(ui_kind)
    amount_rub = _fk_amount_rub(val, ui_kind)
    payload = (
        f"user_id:{user_id},duration:{duration},white:{white},gift:False,method:{pm},amount:{amount_rub},source:{BOT}"
    )
    fk = FreekassaPayment(API_FREEKASSA, SHOP_ID_FREEKASSA)
    nonce = await sql.alloc_fk_api_nonce()
    payment_id = f"fk{user_id}n{nonce}"
    email = f"{user_id}@telegram.org"

    fk_i = _fk_payment_system_id(ui_kind)
    try:
        data, signature = await fk.create_order(
            nonce=nonce,
            payment_id=payment_id,
            amount=float(amount_rub),
            email=email,
            ip=FREEKASSA_SERVER_IP,
            payment_system_id=fk_i,
        )
        url = _payment_url_from_create(data)
        fk_oid = data.get("orderId")
        await sql.add_fk_sbp_payment(
            int(user_id),
            amount_rub,
            "pending",
            payment_id,
            int(fk_oid) if fk_oid is not None else None,
            payload,
            nonce,
            signature,
            is_gift=False,
            method=_db_method(ui_kind),
        )
        logger.info(f"✅ FreeKassa заказ ({pm}, i={fk_i}): paymentId={payment_id}, orderId={fk_oid}")
        return {"status": "pending", "url": url, "id": payment_id}
    except Exception as e:
        logger.error(f"❌ FreeKassa create_order: {e}")
        return {"status": "error", "url": "", "id": ""}


async def pay_for_gift(
    val: str,
    des: str,
    user_id: str,
    duration: str,
    white: bool,
    ui_kind: UiKind,
) -> Dict[str, Any]:
    if not await payment_creation_allowed(int(user_id)):
        return {"status": "rate_limited", "url": "", "id": ""}
    if not API_FREEKASSA or SHOP_ID_FREEKASSA is None:
        logger.error("FreeKassa: не заданы API_FREEKASSA или SHOP_ID_FREEKASSA")
        return {"status": "error", "url": "", "id": ""}

    pm = _payload_method(ui_kind)
    amount_rub = _fk_amount_rub(val, ui_kind)
    payload = (
        f"user_id:{user_id},duration:{duration},white:{white},gift:True,method:{pm},amount:{amount_rub},source:{BOT}"
    )
    fk = FreekassaPayment(API_FREEKASSA, SHOP_ID_FREEKASSA)
    nonce = await sql.alloc_fk_api_nonce()
    payment_id = f"fk{user_id}n{nonce}"
    email = f"{user_id}@telegram.org"

    fk_i = _fk_payment_system_id(ui_kind)
    try:
        data, signature = await fk.create_order(
            nonce=nonce,
            payment_id=payment_id,
            amount=float(amount_rub),
            email=email,
            ip=FREEKASSA_SERVER_IP,
            payment_system_id=fk_i,
        )
        url = _payment_url_from_create(data)
        fk_oid = data.get("orderId")
        await sql.add_fk_sbp_payment(
            int(user_id),
            amount_rub,
            "pending",
            payment_id,
            int(fk_oid) if fk_oid is not None else None,
            payload,
            nonce,
            signature,
            is_gift=True,
            method=_db_method(ui_kind),
        )
        logger.info(f"✅ FreeKassa подарок ({pm}, i={fk_i}): paymentId={payment_id}, orderId={fk_oid}")
        return {"status": "pending", "url": url, "id": payment_id}
    except Exception as e:
        logger.error(f"❌ FreeKassa create_order (gift): {e}")
        return {"status": "error", "url": "", "id": ""}


async def pay_site(
    val: str,
    des: str,
    payload_user: str,
    billing_user_id: int,
    duration: str,
    white: bool,
    is_gift: bool,
    kind: UiKind,
    telegram_username: Optional[str] = None,
    payload_source: str = SITE,
) -> Dict[str, Any]:
    """Оплата с сайта (web API): payload с user_id/email, method fk_sbp/fk_card, source в payload_source."""
    if not await payment_creation_allowed(int(billing_user_id), telegram_username):
        return {"status": "rate_limited", "url": "", "id": ""}
    if not API_FREEKASSA or SHOP_ID_FREEKASSA is None:
        logger.error("FreeKassa site: не заданы API_FREEKASSA или SHOP_ID_FREEKASSA")
        return {"status": "error", "url": "", "id": ""}

    if billing_user_id in ADMIN_IDS:
        val = "1"

    ui_kind: UiKind = kind
    pm = _payload_method(ui_kind)
    gift_str = "True" if is_gift else "False"
    payload = (
        f"user_id:{payload_user},duration:{duration},white:{white},gift:{gift_str},"
        f"method:{pm},amount:{int(float(val))},source:{payload_source}"
    )
    fk = FreekassaPayment(API_FREEKASSA, SHOP_ID_FREEKASSA)
    nonce = await sql.alloc_fk_api_nonce()
    payment_id = f"fk{billing_user_id}n{nonce}"
    email = f"{billing_user_id}@telegram.org"

    amount_rub = _fk_amount_rub(str(val), ui_kind)
    fk_i = _fk_payment_system_id(ui_kind)
    try:
        data, signature = await fk.create_order(
            nonce=nonce,
            payment_id=payment_id,
            amount=float(amount_rub),
            email=email,
            ip=FREEKASSA_SERVER_IP,
            payment_system_id=fk_i,
        )
        url = _payment_url_from_create(data)
        fk_oid = data.get("orderId")
        await sql.add_fk_sbp_payment(
            billing_user_id,
            amount_rub,
            "pending",
            payment_id,
            int(fk_oid) if fk_oid is not None else None,
            payload,
            nonce,
            signature,
            is_gift=is_gift,
            method=_db_method(ui_kind),
        )
        logger.info(f"✅ FreeKassa site ({pm}, i={fk_i}): paymentId={payment_id}, orderId={fk_oid}")
        return {"status": "pending", "url": url, "id": payment_id}
    except Exception as e:
        logger.error(f"❌ FreeKassa site create_order: {e}")
        return {"status": "error", "url": "", "id": ""}


def _duration_from_callback(data: str, prefix: str, gift_prefix: str) -> tuple[str, bool]:
    gift_flag = False
    if data.startswith(gift_prefix):
        gift_flag = True
        duration = data[len(gift_prefix) :]
    else:
        duration = data[len(prefix) :]
    return duration, gift_flag


async def _handle_wata_style_callback(callback: CallbackQuery, ui_kind: UiKind) -> None:
    await callback.answer()
    data = callback.data or ""
    prefix = "wata_sbp_r_" if ui_kind == "sbp" else "wata_card_r_"
    gift_prefix = "wata_sbp_gift_r_" if ui_kind == "sbp" else "wata_card_gift_r_"
    duration, gift_flag = _duration_from_callback(data, prefix, gift_prefix)
    desc_key = duration
    rub_amount = dct_price[duration]
    if callback.from_user.id in ADMIN_IDS:
        rub_amount = 10 if ui_kind == "sbp" else 1
    user_id = str(callback.from_user.id)
    white_flag = False
    if "white" in duration:
        duration = duration.replace("white_", "")
        white_flag = True
    if "old" in duration:
        duration = duration.replace("old", "")

    if gift_flag:
        payment_info = await pay_for_gift(
            val=str(rub_amount),
            des=f"Подписка в подарок {dct_desc[desc_key]}",
            user_id=user_id,
            duration=duration,
            white=white_flag,
            ui_kind=ui_kind,
        )
    else:
        payment_info = await pay(
            val=str(rub_amount),
            des=dct_desc[desc_key],
            user_id=user_id,
            duration=duration,
            white=white_flag,
            ui_kind=ui_kind,
        )

    btn = "⚡ Оплатить СБП" if ui_kind == "sbp" else "💳 Оплатить картой РФ"
    log_label = "FreeKassa (кнопка СБП)" if ui_kind == "sbp" else "FreeKassa (кнопка карта)"

    if payment_info["status"] == "pending":
        try:
            text = lexicon["payment_link"]
            if white_flag:
                text = lexicon["payment_link_white"]
            if gift_flag:
                text += "\n\nДля оплаты <b>подарочной подписки</b> перейдите по ссылке:"
            else:
                text += "\n\nДля оплаты тарифа перейдите по ссылке:"
            await callback.message.edit_text(
                text=text,
                reply_markup=keyboard_payment_sbp(btn, payment_info["url"]),
            )
            logger.info(
                f"Юзер {user_id} создал {log_label} {_fk_amount_rub(str(rub_amount), ui_kind)} руб "
                f"(тариф в боте {rub_amount})"
            )
        except Exception as e:
            logger.error(f"FreeKassa UI: {e}")
            await callback.message.answer(
                lexicon["error_payment"],
                reply_markup=create_kb(1, back_to_main="🔙 Назад"),
            )
    elif payment_info["status"] == "rate_limited":
        await callback.message.answer(
            lexicon["payment_too_many_pending"].format(PAYMENT_MAX_PENDING_PER_USER),
            reply_markup=create_kb(1, back_to_main="🔙 Назад"),
        )


@router.callback_query(F.data.startswith("wata_sbp_"))
async def process_payment_fk_from_sbp_button(callback: CallbackQuery):
    await _handle_wata_style_callback(callback, "sbp")


@router.callback_query(F.data.startswith("wata_card_"))
async def process_payment_fk_from_card_button(callback: CallbackQuery):
    await _handle_wata_style_callback(callback, "card")
