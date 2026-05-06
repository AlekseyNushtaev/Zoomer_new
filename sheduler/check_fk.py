from datetime import datetime, timedelta
from typing import Any, List, Optional

from bot import bot, sql
from config import API_FREEKASSA, SHOP_ID_FREEKASSA
from config_bd.models import PaymentsFkSBP
from keyboard import keyboard_payment_cancel
from lexicon import lexicon
from logging_config import logger
from payments.pay_freekassa import FreekassaPayment
from payments.process_payload import process_confirmed_payment

# Локально закрываем «зависшие» pending, если FreeKassa всё ещё не подтвердила оплату.
FK_PENDING_MAX_AGE = timedelta(hours=8)


def _fk_payment_timed_out(payment: PaymentsFkSBP) -> bool:
    tc = payment.time_created
    if tc is None:
        return False
    return datetime.now() - tc >= FK_PENDING_MAX_AGE


def _resolve_fk_status_after_api(
    payment: PaymentsFkSBP,
    row: Optional[dict],
) -> Optional[str]:
    """Итоговый статус: confirmed/canceled из API; иначе pending или canceled по таймауту 8 ч."""
    if row:
        api_status = _coerce_fk_api_status(row.get("status"))
        api_local = _fk_status_to_local(api_status)
        if api_local == "confirmed":
            return "confirmed"
        if api_local == "canceled":
            return "canceled"
    if _fk_payment_timed_out(payment):
        return "canceled"
    return "pending"


def _coerce_fk_api_status(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    if isinstance(raw, str):
        s = raw.strip()
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return int(s)
    return None


def _fk_status_to_local(api_status: Optional[int]) -> Optional[str]:
    if api_status is None:
        return None
    if api_status == 1:
        return "confirmed"
    if api_status in (8, 9, 6):
        return "canceled"
    if api_status == 0:
        return "pending"
    return "pending"


def _pick_fk_order_row(orders: List[dict], payment: PaymentsFkSBP) -> Optional[dict]:
    if not orders:
        return None
    tid = (payment.transaction_id or "").strip()
    fk_oid = payment.fk_order_id
    for o in orders:
        if tid and str(o.get("merchant_order_id", "")) == tid:
            return o
        if tid and str(o.get("paymentId", "")) == tid:
            return o
        if fk_oid is not None and o.get("fk_order_id") == fk_oid:
            return o
        if fk_oid is not None and o.get("id") == fk_oid:
            return o
    if len(orders) == 1:
        return orders[0]
    return None


async def check_fk_sbp():
    """Проверка заказов FreeKassa (API orders)."""
    if not API_FREEKASSA or SHOP_ID_FREEKASSA is None:
        return

    fk = FreekassaPayment(API_FREEKASSA, SHOP_ID_FREEKASSA)

    try:
        pending_payments = await sql.get_pending_fk_sbp_payments()
        if not pending_payments:
            logger.info("✅ Нет платежей FreeKassa со статусом pending")
            return

        logger.info(f"🔍 FreeKassa: проверка {len(pending_payments)} pending")

        processed_count = 0
        confirmed_count = 0
        canceled_count = 0

        for payment in pending_payments:
            try:
                payment_id = payment.transaction_id
                if not payment_id:
                    continue

                nonce = await sql.alloc_fk_api_nonce()
                result = await fk.get_orders(nonce=nonce, payment_id=payment_id)
                orders_list = result.get("orders") or []
                row = _pick_fk_order_row(orders_list, payment)

                if row is None and payment.fk_order_id is not None:
                    nonce2 = await sql.alloc_fk_api_nonce()
                    result = await fk.get_orders(nonce=nonce2, order_id=payment.fk_order_id)
                    orders_list = result.get("orders") or []
                    row = _pick_fk_order_row(orders_list, payment)

                new_status = _resolve_fk_status_after_api(payment, row)

                if row is None:
                    logger.info(
                        f"FreeKassa {payment_id}: нет заказа в ответе API "
                        f"(fk_order_id={payment.fk_order_id}, orders={len(orders_list)})"
                    )

                if new_status != payment.status and new_status:
                    await sql.update_fk_sbp_payment_status(payment_id, new_status)
                    api_status = _coerce_fk_api_status(row.get("status")) if row else None
                    logger.info(
                        f"🔄 FreeKassa {payment_id}: {payment.status} → {new_status} (api={api_status})")

                    if new_status == "confirmed":
                        await _process_confirmed_fk(payment)
                        confirmed_count += 1
                    elif new_status == "canceled":
                        canceled_count += 1
                        cancel_text = lexicon['payment_cancel']
                        await bot.send_message(payment.user_id, cancel_text, reply_markup=keyboard_payment_cancel())

                processed_count += 1

            except Exception as e:
                logger.error(f"❌ Ошибка проверки FreeKassa {payment.transaction_id}: {e}")

        logger.info(
            f"✅ FreeKassa: проверено {processed_count}, подтверждено {confirmed_count}, отменено {canceled_count}")

    except Exception as e:
        logger.error(f"❌ check_fk_sbp: {e}")


async def _process_confirmed_fk(payment):
    payload = payment.payload
    if not payload:
        logger.error(f"❌ FreeKassa: нет payload у {payment.transaction_id}")
        return
    await process_confirmed_payment(payload)


check_fk = check_fk_sbp
