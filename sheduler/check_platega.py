from datetime import datetime, timedelta

from bot import bot, sql
from config import CHECKER_ID, PLATEGA_API_KEY, PLATEGA_MERCHANT_ID
from keyboard import keyboard_payment_cancel
from lexicon import lexicon
from logging_config import logger
from payments.pay_platega import PlategaPayment
from payments.process_payload import process_confirmed_payment

# Младше 12 ч: confirmed в БД только после успешной панели; старше — confirmed до панели.
PLATEGA_PANEL_DEFER = timedelta(hours=12)


def _platega_defer_panel_confirm(payment) -> bool:
    """True, если платёж младше 12 ч — сначала панель, потом confirmed в БД."""
    tc = payment.time_created
    if tc is None:
        return True
    return datetime.now() - tc < PLATEGA_PANEL_DEFER


def _platega_api_status(result: dict) -> str:
    return (result.get("status") or "").strip().lower()


def _platega_api_confirmed(result: dict) -> bool:
    return _platega_api_status(result) in ("confirmed", "success", "paid", "completed")


def _platega_api_canceled(result: dict) -> bool:
    return _platega_api_status(result) in ("canceled", "cancelled", "failed", "expired", "declined")


def _notify_uid(uid) -> int | None:
    try:
        n = int(uid)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


async def _notify_checker_platega_panel_failed(payment, channel: str) -> None:
    if CHECKER_ID is None:
        return
    text = (
        f"⚠️ Platega ({channel}): у пользователя {payment.user_id} оплата прошла "
        f"({payment.amount} ₽, tx={payment.transaction_id}), но панель не ответила."
    )
    try:
        await bot.send_message(CHECKER_ID, text)
    except Exception as e:
        logger.error(f"Platega {channel}: не удалось уведомить CHECKER_ID: {e}")


async def _process_confirmed_platega(payment, *, is_card: bool) -> bool:
    payload = payment.payload
    if not payload:
        logger.error(f"❌ Platega: нет payload у {payment.transaction_id}")
        return False
    return await process_confirmed_payment(
        payload,
        transaction_id=payment.transaction_id,
        is_card=is_card,
    )


async def _handle_platega_api_confirmed(
    payment, result: dict, *, channel: str, update_status, is_card: bool,
) -> bool:
    """
    Platega подтвердила оплату.
    < 12 ч: панель → при успехе confirmed; при сбое панели остаётся pending.
    ≥ 12 ч: confirmed в БД до панели; при сбое панели — уведомление CHECKER_ID.
    """
    payment_id = payment.transaction_id
    if not payment_id:
        return False

    defer = _platega_defer_panel_confirm(payment)
    api_status = _platega_api_status(result)

    if not defer and payment.status == "pending":
        await update_status(payment_id, "confirmed")
        logger.info(
            f"🔄 Platega {channel} {payment_id}: pending → confirmed (≥12ч, до панели, api={api_status})"
        )

    ok = await _process_confirmed_platega(payment, is_card=is_card)

    if ok:
        if defer and payment.status == "pending":
            await update_status(payment_id, "confirmed")
            logger.info(
                f"🔄 Platega {channel} {payment_id}: pending → confirmed (после панели, api={api_status})"
            )
        return True

    if defer:
        logger.warning(
            f"Platega {channel} {payment_id}: панель не ответила, статус остаётся pending (api={api_status})"
        )
        return False

    await _notify_checker_platega_panel_failed(payment, channel)
    logger.error(
        f"Platega {channel} {payment_id}: панель не ответила (платёж уже confirmed в БД, ≥12ч, api={api_status})"
    )
    return True


async def check_platega():
    """Проверка статуса платежей Platega SBP."""
    if not PLATEGA_API_KEY or not PLATEGA_MERCHANT_ID:
        return

    platega = PlategaPayment(PLATEGA_API_KEY, PLATEGA_MERCHANT_ID)

    try:
        pending_payments = await sql.get_pending_platega_payments()

        if not pending_payments:
            logger.info("✅ Нет платежей Platega SBP со статусом 'pending' для проверки")
            return

        logger.info(f"🔍 Найдено {len(pending_payments)} платежей Platega SBP со статусом 'pending'")

        processed_count = 0
        confirmed_count = 0
        canceled_count = 0

        for payment in pending_payments:
            try:
                transaction_id = payment.transaction_id
                if not transaction_id:
                    continue

                result = await platega.check_payment(transaction_id)
                if not result:
                    continue

                if _platega_api_confirmed(result):
                    if await _handle_platega_api_confirmed(
                        payment,
                        result,
                        channel="СБП",
                        update_status=sql.update_payment_status,
                        is_card=False,
                    ):
                        confirmed_count += 1
                elif _platega_api_canceled(result) and payment.status == "pending":
                    await sql.update_payment_status(transaction_id, "canceled")
                    logger.info(
                        f"🔄 Platega SBP {transaction_id}: pending → canceled (api={_platega_api_status(result)})"
                    )
                    canceled_count += 1
                    uid = _notify_uid(payment.user_id)
                    if uid is not None:
                        cancel_text = lexicon["payment_cancel"]
                        await bot.send_message(uid, cancel_text, reply_markup=keyboard_payment_cancel())

                processed_count += 1

            except Exception as e:
                logger.error(f"❌ Ошибка при проверке платежа Platega SBP {payment.transaction_id}: {e}")

        logger.info(
            f"⚡⚡⚡✅ Проверено платежей Platega SBP: {processed_count}, "
            f"подтверждено: {confirmed_count}, отменено: {canceled_count}"
        )

    except Exception as e:
        logger.error(f"❌ Ошибка в функции check_platega: {e}")


async def check_platega_card():
    """Проверка статуса карточных платежей Platega."""
    if not PLATEGA_API_KEY or not PLATEGA_MERCHANT_ID:
        return

    platega = PlategaPayment(PLATEGA_API_KEY, PLATEGA_MERCHANT_ID)

    try:
        pending_payments = await sql.get_pending_platega_card_payments()

        if not pending_payments:
            logger.info("✅ Нет платежей PlategaCard со статусом 'pending' для проверки")
            return

        logger.info(f"🔍 Найдено {len(pending_payments)} платежей PlategaCard со статусом 'pending'")

        processed_count = 0
        confirmed_count = 0
        canceled_count = 0

        for payment in pending_payments:
            try:
                transaction_id = payment.transaction_id
                if not transaction_id:
                    continue

                result = await platega.check_payment(transaction_id)
                if not result:
                    continue

                if _platega_api_confirmed(result):
                    if await _handle_platega_api_confirmed(
                        payment,
                        result,
                        channel="карта",
                        update_status=sql.update_payment_card_status,
                        is_card=True,
                    ):
                        confirmed_count += 1
                elif _platega_api_canceled(result) and payment.status == "pending":
                    await sql.update_payment_card_status(transaction_id, "canceled")
                    logger.info(
                        f"🔄 Platega card {transaction_id}: pending → canceled (api={_platega_api_status(result)})"
                    )
                    canceled_count += 1
                    uid = _notify_uid(payment.user_id)
                    if uid is not None:
                        cancel_text = lexicon["payment_cancel"]
                        await bot.send_message(uid, cancel_text, reply_markup=keyboard_payment_cancel())

                processed_count += 1

            except Exception as e:
                logger.error(f"❌ Ошибка при проверке платежа PlategaCard {payment.transaction_id}: {e}")

        logger.info(
            f"💳💳💳✅ Проверено платежей PlategaCard: {processed_count}, "
            f"подтверждено: {confirmed_count}, отменено: {canceled_count}"
        )

    except Exception as e:
        logger.error(f"❌ Ошибка в функции check_platega_card: {e}")


async def check_platega_crypto():
    """Проверка статуса платежей Platega Crypto."""

    platega = PlategaPayment(PLATEGA_API_KEY, PLATEGA_MERCHANT_ID)

    try:
        pending_payments = await sql.get_pending_platega_crypto_payments()

        if not pending_payments:
            logger.info("✅ Нет платежей PlategaCrypto со статусом 'pending' для проверки")
            return

        logger.info(f"🔍 Найдено {len(pending_payments)} платежей PlategaCrypto со статусом 'pending'")

        processed_count = 0
        confirmed_count = 0
        canceled_count = 0

        for payment in pending_payments:
            try:
                transaction_id = payment.transaction_id
                result = await platega.check_payment(transaction_id)

                if result:
                    new_status = _platega_api_status(result)

                    if new_status != payment.status and new_status:
                        await sql.update_payment_platega_crypto_status(transaction_id, new_status)

                        logger.info(
                            f"❗️❗️❗️🔄 Статус платежа PlategaCrypto {transaction_id} обновлен: "
                            f"{payment.status} → {new_status}"
                        )

                        if new_status == "confirmed":
                            await process_confirmed_payment_platega(payment, result)
                            confirmed_count += 1
                        else:
                            canceled_count += 1
                            if new_status == "canceled":
                                uid = _notify_uid(payment.user_id)
                                if uid is not None:
                                    cancel_text = lexicon["payment_cancel"]
                                    await bot.send_message(uid, cancel_text, reply_markup=keyboard_payment_cancel())
                    else:
                        logger.debug(
                            f"ℹ️ Статус платежа PlategaCrypto {transaction_id} не изменился: {new_status}"
                        )
                    processed_count += 1

            except Exception as e:
                logger.error(f"❌ Ошибка при проверке платежа PlategaCrypto {payment.transaction_id}: {e}")

        logger.info(
            f"💎💎💎✅ Проверено платежей PlategaCrypto: {processed_count}, "
            f"подтверждено: {confirmed_count}, отменено: {canceled_count}"
        )

    except Exception as e:
        logger.error(f"❌ Ошибка в функции check_platega_crypto: {e}")


async def process_confirmed_payment_platega(payment, platega_data):
    """Обработка подтверждённого платежа Platega (crypto / совместимость)."""
    payload = payment.payload or platega_data.get("payload", "")
    if not payload:
        logger.error(f"❌ Нет payload в платеже {payment.transaction_id}")
        return
    await process_confirmed_payment(payload)
