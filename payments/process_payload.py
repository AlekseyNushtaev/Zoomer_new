from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple

from bot import x3, sql, bot

from config import PARTNER_PROCENT, LEAD_TRACKER_STAR_RUB_PER_STAR, CHECKER_ID
from config_bd.utils import _norm_email, _payload_duration_to_panel_days
from lead_tracker import post_payment_success
from X3 import panel_username_for_site_user
from keyboard import BTN_BACK, create_kb, keyboard_sub_after_buy
from lexicon import lexicon
from logging_config import logger
from wl_traffic.service import (
    fetch_panel_user,
    get_wl_used_gb_for_user,
    parse_traffic_duration,
    reassign_to_active_squad,
    subscription_bonus_gb,
    user_on_limited_squad,
)
from wl_traffic.texts import format_wl_checker_traffic_purchase


async def _mark_connect_panel(transaction_id: Optional[str], is_card: bool) -> None:
    if not transaction_id:
        return
    try:
        await sql.update_platega_connect_panel(transaction_id, True, is_card=is_card)
    except Exception as e:
        logger.error("connect_panel: не удалось обновить tx={}: {}", transaction_id, e)


def _payment_rub_for_partner(method: str, amount: int | float) -> int:
    """Сумма оплаты в рублях для расчёта партнёрского вознаграждения."""
    if method == "stars":
        stars = Decimal(str(amount))
        rate = Decimal(str(LEAD_TRACKER_STAR_RUB_PER_STAR))
        rub = (stars * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return int(rub)
    return int(Decimal(str(amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


async def _credit_partner_commission(payer_uid: int, method: str, amount: int | float) -> None:
    """Начисляет PARTNER_PROCENT% партнёру, если плательщик пришёл по partner-ссылке."""
    try:
        user_row = await sql.get_user(payer_uid)
        if not user_row or len(user_row) <= 29:
            return
        partner_str = user_row[29]
        if not partner_str:
            return
        partner_id = int(partner_str)
        if partner_id <= 0 or partner_id == payer_uid:
            return

        rub = _payment_rub_for_partner(method, amount)
        commission = rub * PARTNER_PROCENT // 100
        if commission <= 0:
            return

        credited = await sql.add_partner_balance(partner_id, commission)
        if not credited:
            logger.warning("Партнёр {} не найден, начисление {} ₽ пропущено", partner_id, commission)
            return

        try:
            await bot.send_message(
                chat_id=partner_id,
                text=lexicon["partner_success"].format(commission),
                reply_markup=create_kb(1, back_to_main="🔙 Назад"),
            )
            logger.info(
                "✅ Партнёру {} начислено {} ₽ за оплату пользователя {}",
                partner_id,
                commission,
                payer_uid,
            )
        except Exception as e:
            logger.error("❌ Ошибка уведомления партнёра {}: {}", partner_id, e)
    except (ValueError, TypeError) as e:
        logger.error("❌ Ошибка начисления партнёрского вознаграждения: {}", e)


async def _resolve_buyer_for_payment(
    raw_user_id: str, white_flag: bool
) -> Optional[Tuple[str, int, Optional[int], bool, Optional[str]]]:
    """
    Возвращает (
        user_id_str для панели,
        db_uid,
        notify_tg_id или None,
        use_add_client_site,
        site_email_norm — email для add_client_site или None,
    ).
    raw_user_id — telegram id, отрицательный billing id сайта или email.
    """
    raw = str(raw_user_id).strip()
    if "@" in raw:
        em = _norm_email(raw)
        row = await sql.get_user_by_email(em)
        if row is None:
            logger.error("Платёж: пользователь с email {} не найден в БД", em)
            return None
        db_uid = int(row[1])
        linked = row[28]
        email_norm = _norm_email(row[18] or em)

        if db_uid > 0:
            panel_base = str(db_uid)
            notify_tg = db_uid
        elif linked is not None and int(linked) > 0:
            tid = int(linked)
            panel_base = str(tid)
            notify_tg = tid
        else:
            panel_base = None
            notify_tg = None

        use_site = panel_base is None
        if use_site:
            user_id_str = panel_username_for_site_user(db_uid, white_flag)
            site_em = email_norm
        else:
            site_em = None
            user_id_str = panel_base

        return user_id_str, db_uid, notify_tg, use_site, site_em

    db_uid = int(raw)
    if db_uid > 0:
        return str(db_uid), db_uid, db_uid, False, None

    row = await sql.get_user(db_uid)
    if row is None:
        logger.error("Платёж: пользователь сайта {} не найден в БД", db_uid)
        return None
    linked = row[28] if len(row) > 28 else None
    email_norm = _norm_email(str(row[18])) if row[18] else None
    notify_tg: Optional[int] = None
    if linked is not None:
        try:
            tid = int(linked)
            if tid > 0:
                notify_tg = tid
        except (TypeError, ValueError):
            pass
    user_id_str = panel_username_for_site_user(db_uid, white_flag)
    if not email_norm:
        logger.error("Платёж сайта: нет email у user_id={}", db_uid)
        return user_id_str, db_uid, notify_tg, False, None
    return user_id_str, db_uid, notify_tg, True, email_norm


async def _apply_panel_subscription(
    duration: int,
    user_id_str: str,
    db_uid: int,
    white_flag: bool,
    use_add_client_site: bool,
    site_email_norm: Optional[str],
) -> Tuple[bool, bool]:
    """Продлевает/создаёт подписку в панели. Возвращает (success, existed_in_panel)."""
    existing_user = await x3.get_user_by_username(user_id_str)
    existed = bool(existing_user and existing_user.get("response"))

    if existed:
        logger.info("⏫ Обновляем {} на {} дней", user_id_str, duration)
        response = await x3.updateClient(duration, user_id_str, db_uid)
    elif use_add_client_site:
        if not site_email_norm:
            logger.error("add_client_site без site_email_norm")
            return False, False
        logger.info("➕ add_client_site {} на {} дней", user_id_str, duration)
        response = await x3.add_client_site(duration, site_email_norm, white_flag, db_uid)
    else:
        logger.info("➕ Добавляем {} на {} дней", user_id_str, duration)
        response = await x3.addClient(duration, user_id_str, db_uid)

    return response, existed


async def _process_traffic_topup(
    user_id: int,
    gb: int,
    method: str,
    amount: int | float,
    *,
    transaction_id: Optional[str] = None,
    is_card: bool = False,
) -> bool:
    """Пополнение трафика Антиглушилка после успешной оплаты."""
    await sql.add_wl_limit(user_id, float(gb))

    panel_user = await fetch_panel_user(x3, user_id, sql=sql)
    await _mark_connect_panel(transaction_id, is_card)
    if panel_user:
        await reassign_to_active_squad(x3, panel_user)

    trafic_wl, limit_wl = await sql.get_wl_limits(user_id)
    used_gb = await get_wl_used_gb_for_user(x3, user_id, trafic_wl, sql=sql)

    await post_payment_success(user_id, method, amount)
    await _credit_partner_commission(user_id, method, amount)

    if CHECKER_ID is not None:
        try:
            await bot.send_message(
                chat_id=CHECKER_ID,
                text=format_wl_checker_traffic_purchase(user_id, gb, used_gb, limit_wl),
            )
        except Exception as e:
            logger.error(f"❌ Ошибка уведомления CHECKER_ID о покупке трафика {user_id}: {e}")

    if user_id > 0:
        try:
            await bot.send_message(
                chat_id=user_id,
                text=lexicon["wl_traffic_success"].format(gb=gb),
                parse_mode="HTML",
                reply_markup=create_kb(1, back_to_main=BTN_BACK),
            )
        except Exception as e:
            logger.error(f"❌ Ошибка уведомления о пополнении трафика {user_id}: {e}")

    logger.info(f"✅ Трафик Антиглушилка +{gb} GB для user={user_id}")
    return True


async def process_confirmed_payment(
    payload,
    *,
    transaction_id: Optional[str] = None,
    is_card: bool = False,
) -> bool:
    """Обработка подтвержденного платежа. True — подписка/подарок применены успешно."""
    try:
        payload_parts: dict[str, str] = {}
        for item in payload.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" in item:
                k, v = item.split(":", 1)
                payload_parts[k] = v
            else:
                payload_parts[item] = "1"
        raw_uid = payload_parts.get("user_id", "0")
        raw_duration = str(payload_parts.get("duration", "0") or "0").strip()
        traffic_gb = parse_traffic_duration(raw_duration)
        if traffic_gb is not None:
            method = payload_parts.get("method", "")
            if method in (
                "sbp", "stars", "card", "crypto", "cryptobot",
                "wata_sbp", "wata_card", "fk_sbp", "fk_card", "fksbp",
            ):
                amount = int(payload_parts.get("amount", 0))
            else:
                amount = float(payload_parts.get("amount", 0.0))
            if "@" in str(raw_uid):
                logger.error("Traffic top-up с email в user_id не поддерживается")
                return False
            uid_traffic = int(raw_uid)
            if method == "stars":
                await sql.add_payment_stars(uid_traffic, amount, payload, False)
            return await _process_traffic_topup(
                uid_traffic,
                traffic_gb,
                method,
                amount,
                transaction_id=transaction_id,
                is_card=is_card,
            )

        duration = _payload_duration_to_panel_days(raw_duration)
        secret_tariff = raw_duration == "30secret"
        is_discount = "discount" in payload_parts
        if duration is None or duration <= 0:
            logger.error("Платёж: некорректный duration в payload: {}", raw_duration)
            return False
        white_flag = payload_parts.get("white", "False") == "True"
        if white_flag:
            logger.error("White tariff отключён, платёж отклонён")
            return False
        is_gift = payload_parts.get("gift", "False") == "True"
        method = payload_parts.get("method", "")
        if method in ("sbp", "stars", "card", "crypto", "cryptobot", "wata_sbp", "wata_card", "fk_sbp", "fk_card", "fksbp"):
            amount = int(payload_parts.get("amount", 0))
        else:
            amount = float(payload_parts.get("amount", 0.0))

        logger.info(
            "Обработка подтвержденного платежа user={} duration={} secret={} white={} gift={} method={} amount={}",
            raw_uid,
            duration,
            secret_tariff,
            white_flag,
            is_gift,
            method,
            amount,
        )

        if method in ["sbp", "card", "crypto", "cryptobot", "wata_sbp", "wata_card", "fk_sbp", "fk_card", "fksbp"]:
            currency = "руб"
        elif method == "stars":
            currency = "⭐️"
            if "@" in str(raw_uid):
                logger.error("Stars-платёж с email в user_id не поддерживается")
                return False
            uid_stars = int(raw_uid)
            await sql.add_payment_stars(uid_stars, amount, payload, is_gift)
        elif method in ("ton", "usdt"):
            currency = method.upper()
        else:
            currency = ""

        if is_gift:
            row_g = None
            if "@" in str(raw_uid):
                row_g = await sql.get_user_by_email(_norm_email(str(raw_uid)))
                if row_g is None:
                    logger.error("Подарок: email не найден в БД")
                    return False
                giver_billing_id = int(row_g[1])
            else:
                giver_billing_id = int(raw_uid)

            gift_id = await sql.create_gift(giver_billing_id, duration, white_flag)
            pay_tracker_uid: Optional[int] = None
            if giver_billing_id > 0:
                pay_tracker_uid = giver_billing_id
            elif row_g is not None and len(row_g) > 27 and row_g[27] is not None:
                try:
                    lt = int(row_g[27])
                    if lt > 0:
                        pay_tracker_uid = lt
                except (TypeError, ValueError):
                    pass
            if pay_tracker_uid is not None:
                await post_payment_success(pay_tracker_uid, method, amount)

            await _credit_partner_commission(giver_billing_id, method, amount)

            marker = ' (тариф «Включи мобильный»)' if white_flag else ''
            gift_message = lexicon["payment_gift"].format(duration, marker, gift_id)

            if giver_billing_id > 0:
                try:
                    await bot.send_message(
                        chat_id=giver_billing_id,
                        text=gift_message,
                        disable_web_page_preview=True,
                    )
                    await bot.send_message(
                        chat_id=giver_billing_id,
                        text=lexicon["payment_gift_faq"],
                        reply_markup=create_kb(1, back_to_main="🔙 Назад"),
                    )
                    await bot.send_message(
                        chat_id=giver_billing_id,
                        text=lexicon["payment_gift_web"].format(gift_id),
                        disable_web_page_preview=True,
                    )
                    logger.info("✅ Сообщения о подарке отправлены пользователю {}", giver_billing_id)
                    await _mark_connect_panel(transaction_id, is_card)
                except Exception as e:
                    logger.error("❌ Ошибка отправки сообщения о подарке: {}", e)
            else:
                logger.info("Подарок (сайт): уведомление в Telegram пропущено, giver_id={}", giver_billing_id)

            return True

        else:
            resolved = await _resolve_buyer_for_payment(raw_uid, white_flag)
            if resolved is None:
                return False
            user_id_str, db_uid, notify_tg, use_add_client_site, site_email_norm = resolved

            response, existing_user = await _apply_panel_subscription(
                duration,
                user_id_str,
                db_uid,
                white_flag,
                use_add_client_site,
                site_email_norm,
            )

            if not response:
                logger.error("❌ Не удалось обновить клиента {}", user_id_str)
                return False

            await _mark_connect_panel(transaction_id, is_card)

            result_active = await x3.activ(user_id_str)
            subscription_time = result_active.get("time", "-")

            if subscription_time != "-":
                try:
                    subscription_end_date = datetime.strptime(
                        subscription_time, "%d-%m-%Y %H:%M МСК"
                    )
                    if white_flag:
                        await sql.update_white_subscription_end_date(db_uid, subscription_end_date)
                    else:
                        await sql.update_subscription_end_date(db_uid, subscription_end_date)
                    logger.info("✅ Дата подписки обновлена: {}", subscription_end_date)
                except ValueError as e:
                    logger.error("❌ Ошибка парсинга даты: {}", e)

            try:
                user_data = await sql.get_user(db_uid)
                if user_data and len(user_data) > 4:
                    ref_reserve_field = user_data[8]
                    ref_id_str = user_data[2]

                    if not ref_reserve_field and ref_id_str:
                        try:
                            ref_id = int(ref_id_str)
                            ref_data = await sql.get_user(ref_id)

                            if ref_data and len(ref_data) > 4:
                                ref_in_panel = ref_data[4]

                                if ref_in_panel:
                                    logger.info("🎁 Начисляем 7 дней рефереру {} за приглашение", ref_id)

                                    ref_existing = await x3.get_user_by_username(str(ref_id))

                                    if ref_existing and "response" in ref_existing and ref_existing["response"]:
                                        await x3.updateClient(7, str(ref_id), ref_id)

                                    ref_result_active = await x3.activ(str(ref_id))
                                    ref_subscription_time = ref_result_active.get("time", "-")

                                    if ref_subscription_time != "-":
                                        try:
                                            ref_subscription_end_date = datetime.strptime(
                                                ref_subscription_time,
                                                "%d-%m-%Y %H:%M МСК",
                                            )
                                            await sql.update_subscription_end_date(
                                                ref_id, ref_subscription_end_date
                                            )
                                            logger.info("✅ Дата подписки реферера обновлена")
                                        except ValueError as e:
                                            logger.error("❌ Ошибка парсинга даты реферера: {}", e)

                                    if ref_id > 0:
                                        try:
                                            await bot.send_message(
                                                chat_id=ref_id,
                                                text=lexicon["ref_success"].format(db_uid),
                                                reply_markup=create_kb(1, back_to_main="🔙 Назад"),
                                            )
                                            logger.info("✅ Уведомление отправлено рефереру {}", ref_id)
                                        except Exception as e:
                                            logger.error("❌ Ошибка отправки уведомления рефереру: {}", e)

                        except (ValueError, Exception) as e:
                            logger.error("❌ Ошибка при обработке реферальной системы: {}", e)
            except Exception as e:
                logger.error("❌ Ошибка при проверке реферальной системы: {}", e)

            if await sql.get_user(db_uid) is not None:
                await sql.update_in_panel(db_uid)
            else:
                await sql.add_user(db_uid, True)
            await sql.update_reserve_field(db_uid)
            if (secret_tariff or is_discount) and not is_gift:
                await sql.update_field_bool_3(db_uid, True)

            if not white_flag:
                bonus_gb = subscription_bonus_gb(duration)
                if bonus_gb > 0:
                    await sql.add_wl_limit(db_uid, bonus_gb)
                panel_user = await fetch_panel_user(x3, db_uid, sql=sql)
                if panel_user and user_on_limited_squad(panel_user):
                    await reassign_to_active_squad(x3, panel_user)

            tracker_pay_uid = (
                notify_tg if notify_tg is not None and notify_tg > 0 else (db_uid if db_uid > 0 else None)
            )
            if tracker_pay_uid is not None:
                await post_payment_success(tracker_pay_uid, method, amount)

            await _credit_partner_commission(db_uid, method, amount)

            if notify_tg is not None and notify_tg > 0:
                try:
                    sub_link = await x3.sublink(user_id_str)
                    marker = "продлена" if existing_user else "активирована"
                    message_text = lexicon["payment_success"].format(
                        marker, subscription_time, amount, currency, duration, sub_link
                    )

                    await bot.send_message(
                        chat_id=notify_tg,
                        text=message_text,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                        reply_markup=keyboard_sub_after_buy(sub_link),
                    )

                    logger.info("✅ Уведомление отправлено пользователю {}", notify_tg)

                except Exception as e:
                    logger.error("❌ Ошибка отправки уведомления: {}", e)
            else:
                logger.info("Платёж сайта: Telegram-уведомление не отправлялось (db_uid={})", db_uid)

            return True

    except Exception as e:
        logger.error("❌ Ошибка обработки подтвержденного платежа: {}", e)
        return False

    return False
