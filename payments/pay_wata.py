import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

import aiohttp
from aiogram import Router, F
from aiogram.types import CallbackQuery

from bot import sql
from config import ADMIN_IDS, BOT_URL, PAYMENT_MAX_PENDING_PER_USER, WATA_API_BASE, WATA_API_CARD_KEY, WATA_API_SBP_KEY
from payments.payment_limits import payment_creation_allowed
from payments.payload_source import BOT, SITE
from payments.tariff_gate import is_mobile_tariff_key, normalize_tariff_duration_key
from utils.menu_ui import edit_or_send_screen
from keyboard import keyboard_payment_sbp, create_kb
from lexicon import dct_price, dct_desc, lexicon
from logging_config import logger

router = Router()
# Роутер в main.py не подключается: wata_sbp_* и wata_card_* — Platega.

WataKind = Literal["sbp", "card"]


class WataPayment:
    """Клиент WATA H2H API (платёжные ссылки)."""

    def __init__(self, access_token: str, base_url: str = WATA_API_BASE):
        self.access_token = access_token
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def create_payment_link(
        self,
        amount: float,
        currency: str,
        description: str,
        order_id: str,
        success_redirect_url: str,
        fail_redirect_url: str,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/links"
        body = {
            "type": "OneTime",
            "amount": round(float(amount), 2),
            "currency": currency,
            "description": description[:500] if description else "",
            "orderId": order_id,
            "successRedirectUrl": success_redirect_url,
            "failRedirectUrl": fail_redirect_url,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body, headers=self.headers) as response:
                text = await response.text()
                if response.status != 200:
                    logger.error("WATA create link {}: {}", response.status, text)
                    raise RuntimeError(f"WATA create link HTTP {response.status}")
                return await response.json()

    async def search_transactions_by_order_id(self, order_id: str) -> list:
        url = f"{self.base_url}/v2/transactions"
        params = {"orderId": order_id, "maxResultCount": 100}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self.headers, params=params) as response:
                text = await response.text()
                if response.status == 429:
                    logger.warning("WATA transactions rate limit for orderId={}: {}", order_id, text)
                    raise RuntimeError("WATA rate limit")
                if response.status != 200:
                    logger.error("WATA transactions {}: {}", response.status, text)
                    raise RuntimeError(f"WATA transactions HTTP {response.status}")
                data = await response.json()
        return data.get("items") or data.get("Items") or []


def _wata_amount_rub(val: str) -> float:
    x = float(val)
    if x < 10:
        return 10.0
    return round(x, 2)


# Статусы транзакции WATA API: Created, Pending, Paid, Declined (регистр в JSON может быть любой).
_WATA_ST_PAID = "paid"
_WATA_ST_DECLINED = "declined"
_WATA_ST_OPEN = frozenset({"created", "pending"})

# Типы: CardCrypto, SBP, TPay, SberPay (док wata.pro/api).
_WATA_TYPE_SBP = "SBP"
_WATA_TYPES_CARD_FLOW = frozenset({"CardCrypto", "TPay", "SberPay"})

# Created/Pending старше этого срока не блокируют итог «все попытки Declined» (зависшие транзакции в WATA).
_WATA_STALE_OPEN_MAX_AGE = timedelta(hours=72)

# Не ставим canceled в БД по «только declined» в API раньше этого срока с момента создания ссылки:
# у WATA иногда сначала приходит Declined, а оплата появляется через короткое время.
WATA_DECLINED_CANCEL_GRACE_AFTER_LINK = timedelta(minutes=30)


def _wata_norm_status(x: dict) -> str:
    return (x.get("status") or x.get("Status") or "").strip().lower()


def _wata_norm_kind(x: dict) -> str:
    return (x.get("kind") or x.get("Kind") or "").strip().lower()


def _wata_creation_utc(p: dict) -> datetime | None:
    raw = (p.get("creationTime") or p.get("CreationTime") or "").strip()
    if not raw or raw.startswith("0001-01-01"):
        return None
    s = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _wata_open_is_still_blocking(p: dict) -> bool:
    """True, если Created/Pending ещё считаем «живым» ожиданием (не протух по времени)."""
    st = _wata_norm_status(p)
    if st not in _WATA_ST_OPEN:
        return False
    created = _wata_creation_utc(p)
    if created is None:
        return True
    return datetime.now(timezone.utc) - created <= _WATA_STALE_OPEN_MAX_AGE


def _wata_canonical_transaction_type(x: dict) -> str:
    t = (x.get("type") or x.get("Type") or "").strip()
    tl = t.lower().replace("-", "").replace("_", "")
    if tl == "sbp":
        return _WATA_TYPE_SBP
    if tl == "cardcrypto" or tl == "card":
        return "CardCrypto"
    if tl == "tpay":
        return "TPay"
    if tl == "sberpay":
        return "SberPay"
    return t


def _wata_type_matches_expect(expect_type: str, canonical: str) -> bool:
    expect = (expect_type or "").strip()
    if expect == _WATA_TYPE_SBP:
        return canonical == _WATA_TYPE_SBP
    if expect == "CardCrypto":
        return canonical in _WATA_TYPES_CARD_FLOW
    return canonical == expect


def wata_payment_rows(items: list) -> List[dict]:
    """Только оплаты kind=Payment (без Refund), kind без учёта регистра."""
    return [i for i in items if _wata_norm_kind(i) == "payment"]


def wata_transactions_status_counts(items: list) -> Dict[str, int]:
    """Сводка для логов без PII (статус после нормализации к нижнему регистру)."""
    c: Counter[str] = Counter()
    for p in wata_payment_rows(items):
        s = _wata_norm_status(p) or "?"
        c[s] += 1
    return dict(c)


def wata_order_payment_state(items: list, expect_type: str) -> str:
    """
    Ответ GET /transactions?orderId=… → pending | paid | declined | wrong_paid.

    Статусы WATA: Created, Pending, Paid, Declined — сравниваются без учёта регистра.
    Одна «зависшая» Pending на фоне нескольких Declined не держит заказ вечно (см. _WATA_STALE_OPEN_MAX_AGE).
    """
    payments = wata_payment_rows(items)
    if not payments:
        return "pending"

    expect = expect_type.strip()

    if any(
        _wata_norm_status(p) == _WATA_ST_PAID and _wata_type_matches_expect(expect, _wata_canonical_transaction_type(p))
        for p in payments
    ):
        return "paid"

    if any(
        _wata_norm_status(p) == _WATA_ST_PAID and not _wata_type_matches_expect(expect, _wata_canonical_transaction_type(p))
        for p in payments
    ):
        return "wrong_paid"

    if any(_wata_open_is_still_blocking(p) for p in payments):
        return "pending"

    if any(_wata_norm_status(p) == _WATA_ST_DECLINED for p in payments):
        return "declined"

    return "pending"


async def pay(
    val: str,
    des: str,
    user_id: str,
    duration: str,
    white: bool,
    kind: WataKind,
    telegram_username: Optional[str] = None,
) -> Dict[str, Any]:
    if not await payment_creation_allowed(int(user_id), telegram_username):
        return {"status": "rate_limited", "url": "", "id": ""}
    token = WATA_API_SBP_KEY if kind == "sbp" else WATA_API_CARD_KEY
    if not token:
        logger.error("WATA: отсутствует токен для {}", kind)
        return {"status": "error", "url": "", "id": ""}

    method = "wata_sbp" if kind == "sbp" else "wata_card"
    payload = f"user_id:{user_id},duration:{duration},white:{white},gift:False,method:{method},amount:{int(val)},source:{BOT}"
    order_id = f"{method}-{uuid.uuid4().hex}"
    amount_api = _wata_amount_rub(val)

    client = WataPayment(token)
    try:
        result = await client.create_payment_link(
            amount=amount_api,
            currency="RUB",
            description=des,
            order_id=order_id,
            success_redirect_url=BOT_URL,
            fail_redirect_url=BOT_URL,
        )
        pay_url = result.get("url") or result.get("Url") or ""
        if not pay_url:
            logger.error("WATA: пустая ссылка в ответе {}", result)
            return {"status": "error", "url": "", "id": ""}

        if kind == "sbp":
            await sql.add_wata_sbp_payment(
                int(user_id), int(val), "pending", order_id, payload, is_gift=False
            )
        else:
            await sql.add_wata_card_payment(
                int(user_id), int(val), "pending", order_id, payload, is_gift=False
            )

        logger.info("WATA {}: ссылка создана orderId={}", method, order_id)
        return {"status": "pending", "url": pay_url, "id": order_id}
    except Exception as e:
        logger.error("WATA create payment: {}", e)
        return {"status": "error", "url": "", "id": ""}


async def pay_for_gift(
    val: str,
    des: str,
    user_id: str,
    duration: str,
    white: bool,
    kind: WataKind,
    telegram_username: Optional[str] = None,
) -> Dict[str, Any]:
    if not await payment_creation_allowed(int(user_id), telegram_username):
        return {"status": "rate_limited", "url": "", "id": ""}
    token = WATA_API_SBP_KEY if kind == "sbp" else WATA_API_CARD_KEY
    if not token:
        logger.error("WATA: отсутствует токен для {}", kind)
        return {"status": "error", "url": "", "id": ""}

    method = "wata_sbp" if kind == "sbp" else "wata_card"
    payload = f"user_id:{user_id},duration:{duration},white:{white},gift:True,method:{method},amount:{int(val)},source:{BOT}"
    order_id = f"{method}-gift-{uuid.uuid4().hex}"
    amount_api = _wata_amount_rub(val)

    client = WataPayment(token)
    try:
        result = await client.create_payment_link(
            amount=amount_api,
            currency="RUB",
            description=des,
            order_id=order_id,
            success_redirect_url=BOT_URL,
            fail_redirect_url=BOT_URL,
        )
        pay_url = result.get("url") or result.get("Url") or ""
        if not pay_url:
            return {"status": "error", "url": "", "id": ""}

        if kind == "sbp":
            await sql.add_wata_sbp_payment(
                int(user_id), int(val), "pending", order_id, payload, is_gift=True
            )
        else:
            await sql.add_wata_card_payment(
                int(user_id), int(val), "pending", order_id, payload, is_gift=True
            )

        return {"status": "pending", "url": pay_url, "id": order_id}
    except Exception as e:
        logger.error("WATA gift payment: {}", e)
        return {"status": "error", "url": "", "id": ""}


SITE_SUCCESS_URL = "https://4zoomer.top/success"
SITE_FAIL_URL = "https://4zoomer.top/pricing"


async def pay_site(
    val: str,
    des: str,
    payload_user: str,
    billing_user_id: int,
    duration: str,
    white: bool,
    is_gift: bool,
    kind: WataKind,
    telegram_username: Optional[str] = None,
    payload_source: str = SITE,
) -> Dict[str, Any]:
    """Оплата с сайта (web API): payload с user_id/email, method wata_sbp/wata_card, source в payload_source."""
    if not await payment_creation_allowed(int(billing_user_id), telegram_username):
        return {"status": "rate_limited", "url": "", "id": ""}
    token = WATA_API_SBP_KEY if kind == "sbp" else WATA_API_CARD_KEY
    if not token:
        logger.error("WATA site: отсутствует токен для {}", kind)
        return {"status": "error", "url": "", "id": ""}

    method = "wata_sbp" if kind == "sbp" else "wata_card"
    gift_str = "True" if is_gift else "False"
    payload = (
        f"user_id:{payload_user},duration:{duration},white:{white},gift:{gift_str},"
        f"method:{method},amount:{int(val)},source:{payload_source}"
    )
    order_id = f"{method}-site-{uuid.uuid4().hex}"
    amount_api = _wata_amount_rub(val)

    client = WataPayment(token)
    try:
        result = await client.create_payment_link(
            amount=amount_api,
            currency="RUB",
            description=des,
            order_id=order_id,
            success_redirect_url=SITE_SUCCESS_URL,
            fail_redirect_url=SITE_FAIL_URL,
        )
        pay_url = result.get("url") or result.get("Url") or ""
        if not pay_url:
            logger.error("WATA site: пустая ссылка в ответе {}", result)
            return {"status": "error", "url": "", "id": ""}

        if kind == "sbp":
            await sql.add_wata_sbp_payment(
                billing_user_id, int(val), "pending", order_id, payload, is_gift=is_gift
            )
        else:
            await sql.add_wata_card_payment(
                billing_user_id, int(val), "pending", order_id, payload, is_gift=is_gift
            )

        logger.info("WATA site {}: ссылка создана orderId={}", method, order_id)
        return {"status": "pending", "url": pay_url, "id": order_id}
    except Exception as e:
        logger.error("WATA site create payment: {}", e)
        return {"status": "error", "url": "", "id": ""}


def _duration_from_wata_callback(data: str, prefix: str, gift_prefix: str) -> tuple[str, bool]:
    gift_flag = False
    if data.startswith(gift_prefix):
        gift_flag = True
        duration = data[len(gift_prefix) :]
    else:
        duration = data[len(prefix) :]
    return duration, gift_flag


@router.callback_query(F.data.startswith("wata_sbp_"))
async def process_payment_wata_sbp(callback: CallbackQuery):
    await callback.answer()
    data = callback.data
    duration, gift_flag = _duration_from_wata_callback(data, "wata_sbp_r_", "wata_sbp_gift_r_")
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
            kind="sbp",
            telegram_username=tg_uname,
        )
    else:
        payment_info = await pay(
            val=str(rub_amount),
            des=dct_desc[desc_key],
            user_id=user_id,
            duration=duration,
            white=False,
            kind="sbp",
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
                keyboard_payment_sbp("⚡ Оплатить СБП", payment_info["url"]),
            )
            logger.info("Юзер {} создал WATA СБП {} руб", user_id, rub_amount)
        except Exception as e:
            logger.error("WATA СБП UI: {}", e)
            await callback.message.answer(lexicon["error_payment"], reply_markup=create_kb(1, back_to_main="🔙 Назад"))
    elif payment_info["status"] == "rate_limited":
        await callback.message.answer(
            lexicon["payment_too_many_pending"].format(PAYMENT_MAX_PENDING_PER_USER),
            reply_markup=create_kb(1, back_to_main="🔙 Назад"),
        )


@router.callback_query(F.data.startswith("wata_card_"))
async def process_payment_wata_card(callback: CallbackQuery):
    await callback.answer()
    data = callback.data
    duration, gift_flag = _duration_from_wata_callback(data, "wata_card_r_", "wata_card_gift_r_")
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
            kind="card",
            telegram_username=tg_uname,
        )
    else:
        payment_info = await pay(
            val=str(rub_amount),
            des=dct_desc[desc_key],
            user_id=user_id,
            duration=duration,
            white=False,
            kind="card",
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
                keyboard_payment_sbp("💳 Оплатить картой РФ", payment_info["url"]),
            )
            logger.info("Юзер {} создал WATA Карта {} руб", user_id, rub_amount)
        except Exception as e:
            logger.error("WATA Card UI: {}", e)
            await callback.message.answer(lexicon["error_payment"], reply_markup=create_kb(1, back_to_main="🔙 Назад"))
    elif payment_info["status"] == "rate_limited":
        await callback.message.answer(
            lexicon["payment_too_many_pending"].format(PAYMENT_MAX_PENDING_PER_USER),
            reply_markup=create_kb(1, back_to_main="🔙 Назад"),
        )
