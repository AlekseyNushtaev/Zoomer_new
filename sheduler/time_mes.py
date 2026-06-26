import asyncio
import json
import random
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Set

TG_TEXT_LIMIT = 4096

from aiogram import Bot

from bot import sql, x3
from keyboard import keyboard_tariff, keyboard_tariff_trial, create_kb, STYLE_PRIMARY
from telegram_ids import is_telegram_chat_id
from lexicon import lexicon
from logging_config import logger
from config import CHECKER_ID

WINDOW = timedelta(minutes=10)
STATE_VERSION = 1


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _fmt_utc0(dt: datetime) -> str:
    """Naive datetime считается уже в UTC (как везде в time_mes)."""
    return dt.strftime('%Y-%m-%d %H:%M:%S') + ' UTC+0'


def _normalize_end_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)
    return dt.replace(microsecond=0)


def _end_key(end: datetime) -> str:
    return end.isoformat(timespec='seconds')


def _in_send_window(now: datetime, moment: datetime) -> bool:
    return moment <= now < moment + WINDOW


def _load_state(raw: Optional[str], end_key: str) -> Set[str]:
    if not raw:
        return set()
    try:
        data = json.loads(raw)
        if data.get('v') != STATE_VERSION or data.get('e') != end_key:
            return set()
        return set(data.get('s', []))
    except (json.JSONDecodeError, TypeError):
        return set()


def _dump_state(end_key: str, sent: Set[str]) -> str:
    return json.dumps(
        {'v': STATE_VERSION, 'e': end_key, 's': sorted(sent)},
        separators=(',', ':'),
    )


async def _persist_push_state(user_id: int, end_key: str, sent: Set[str]):
    await sql.update_field_str_1(user_id, _dump_state(end_key, sent))


def _format_ids_line(label: str, ids: List[int]) -> str:
    if not ids:
        return f'{label}: —'
    return f'{label}:\n{", ".join(map(str, ids))}'


async def _send_admin_text_chunks(bot: Bot, chat_id: int, text: str):
    """Telegram ограничивает длину сообщения; длинные списки id режем на части."""
    text = text.strip()
    if len(text) <= TG_TEXT_LIMIT:
        await bot.send_message(chat_id, text)
        return
    part = 1
    pos = 0
    n = len(text)
    while pos < n:
        take = TG_TEXT_LIMIT - 80
        chunk = text[pos : pos + take]
        if pos + take < n:
            cut = chunk.rfind('\n')
            if cut > take // 2:
                chunk = chunk[:cut]
            elif (cut := chunk.rfind(', ')) > take // 2:
                chunk = chunk[: cut + 1]
        header = f'Часть {part}\n\n' if part > 1 else ''
        await bot.send_message(chat_id, header + chunk)
        pos += len(chunk)
        part += 1


async def send_message_cron(bot: Bot):
    now = _utc_now_naive()
    window_end = now + WINDOW
    candidate_rows = await sql.select_rows_for_subscription_expiry_push(now, WINDOW)
    if CHECKER_ID is not None:
        await bot.send_message(
            CHECKER_ID,
            'Начинаю рассылку. Окно UTC+0: '
            f'{_fmt_utc0(now)} — {_fmt_utc0(window_end)} '
            f'({WINDOW}). Кандидатов: {len(candidate_rows)}.',
        )
    sent_count_7 = 0
    sent_count_3 = 0
    sent_count_1 = 0
    sent_count_0 = 0
    sent_count_week = 0
    sent_count_second_chance = 0
    failed_count = 0
    ids_7: List[int] = []
    ids_3: List[int] = []
    ids_1: List[int] = []
    ids_0: List[int] = []
    ids_week: List[int] = []
    ids_second_chance: List[int] = []

    for user_id, end_raw, in_panel, ttclid, field_str_1_raw in candidate_rows:
        try:
            if not is_telegram_chat_id(user_id):
                continue
            end = _normalize_end_utc(end_raw)
            if end is None:
                continue

            end_key = _end_key(end)
            sent = _load_state(field_str_1_raw, end_key)

            if in_panel:
                keyboard = keyboard_tariff()
            else:
                keyboard = keyboard_tariff_trial()

            if now < end:
                t7 = end - timedelta(days=7)
                t3 = end - timedelta(days=3)
                t1 = end - timedelta(days=1)
                t_h = end - timedelta(hours=1)

                if '7' not in sent and _in_send_window(now, t7):
                    await bot.send_message(chat_id=user_id, text=lexicon['push_7'], reply_markup=keyboard)
                    await asyncio.sleep(0.05)
                    sent.add('7')
                    await _persist_push_state(user_id, end_key, sent)
                    await sql.mark_notification_as_sent(user_id)
                    sent_count_7 += 1
                    ids_7.append(user_id)
                    logger.info(f"Отправлено push-уведомление пользователю {user_id} за 7 дней")
                elif '3' not in sent and _in_send_window(now, t3):
                    await bot.send_message(chat_id=user_id, text=lexicon['push_3'], reply_markup=keyboard)
                    await asyncio.sleep(0.05)
                    sent.add('3')
                    await _persist_push_state(user_id, end_key, sent)
                    await sql.mark_notification_as_sent(user_id)
                    sent_count_3 += 1
                    ids_3.append(user_id)
                    logger.info(f"Отправлено push-уведомление пользователю {user_id} за 3 дня")
                elif '1' not in sent and _in_send_window(now, t1):
                    await bot.send_message(chat_id=user_id, text=lexicon['push_1'], reply_markup=keyboard)
                    await asyncio.sleep(0.05)
                    sent.add('1')
                    await _persist_push_state(user_id, end_key, sent)
                    await sql.mark_notification_as_sent(user_id)
                    sent_count_1 += 1
                    ids_1.append(user_id)
                    logger.info(f"Отправлено push-уведомление пользователю {user_id} за 1 день")
                elif 'h' not in sent and _in_send_window(now, t_h):
                    await bot.send_message(chat_id=user_id, text=lexicon['push_0'], reply_markup=keyboard)
                    await asyncio.sleep(0.05)
                    sent.add('h')
                    await _persist_push_state(user_id, end_key, sent)
                    await sql.mark_notification_as_sent(user_id)
                    sent_count_0 += 1
                    ids_0.append(user_id)
                    logger.info(f"Отправлено push-уведомление пользователю {user_id} за 1 час")
            else:
                t_second = end + timedelta(days=7)
                if (
                    'sc' not in sent
                    and _in_send_window(now, t_second)
                    and not ttclid
                ):
                    await bot.send_message(
                        chat_id=user_id,
                        text=lexicon['second_chance_message'],
                        reply_markup=create_kb(
                            1,
                            styles={'connect_vpn': STYLE_PRIMARY},
                            connect_vpn='🔗 Подключить VPN',
                            video_faq='Видеоинструкция',
                        ),
                    )
                    logger.info(f"Отправлено push-уведомление пользователю {user_id} за second_chance")
                    user_id_str = str(user_id)
                    try:
                        response = await x3.updateClient(4, user_id_str, user_id)
                        if response:
                            result_active = await x3.activ(user_id_str)
                            subscription_time = result_active.get('time', '-')
                            if subscription_time != '-':
                                try:
                                    new_end_date = datetime.strptime(
                                        subscription_time, '%d-%m-%Y %H:%M МСК'
                                    )
                                    await sql.update_subscription_end_date(user_id, new_end_date)
                                    logger.info(
                                        f"✅ Дата подписки для {user_id} обновлена после second_chance"
                                    )
                                except ValueError as e:
                                    logger.error(
                                        f"Ошибка парсинга даты second_chance для {user_id}: {e}"
                                    )
                        else:
                            logger.error(
                                f"❌ Не удалось добавить 4 дня пользователю {user_id} (second_chance)"
                            )
                    except Exception as e:
                        logger.error(f"Ошибка при добавлении 4 дней пользователю {user_id}: {e}")

                    utc_today = now.date()
                    ttclid_value = f"second_chance_{utc_today.strftime('%d%m%y')}"
                    try:
                        await sql.update_ttclid(user_id, ttclid_value)
                        logger.info(f"✅ ttclid для {user_id} установлен: {ttclid_value}")
                    except Exception as e:
                        logger.error(f"Ошибка обновления ttclid для {user_id}: {e}")

                    sent.add('sc')
                    await _persist_push_state(user_id, end_key, sent)
                    sent_count_second_chance += 1
                    ids_second_chance.append(user_id)
                else:
                    n = 1
                    while n <= 200:
                        moment = end + timedelta(days=3 * n)
                        if moment > now + WINDOW:
                            break
                        key = f'p{n}'
                        if key not in sent and _in_send_window(now, moment):
                            await bot.send_message(
                                chat_id=user_id,
                                text=random.choice(lexicon['push_off']),
                                reply_markup=keyboard,
                            )
                            await asyncio.sleep(0.05)
                            sent.add(key)
                            await _persist_push_state(user_id, end_key, sent)
                            await sql.mark_notification_as_sent(user_id)
                            sent_count_week += 1
                            ids_week.append(user_id)
                            logger.info(
                                f"Отправлено push-уведомление пользователю {user_id} "
                                f"после окончания подписки (+{3 * n} дн от даты end)"
                            )
                            break
                        n += 1
        except Exception:
            failed_count += 1

    all_sent_ids: List[int] = (
        ids_7 + ids_3 + ids_1 + ids_0 + ids_week + ids_second_chance
    )
    report_body = f'''Рассылка об окончании подписки (UTC, окна 10 мин):
за 7 дней: {sent_count_7}
за 3 дня: {sent_count_3}
за 1 день: {sent_count_1}
за 1 час: {sent_count_0}
после окончания каждые 3 дня: {sent_count_week}
повторный триал: {sent_count_second_chance}

Не получилось: {failed_count}
'''
    if CHECKER_ID is not None:
        await _send_admin_text_chunks(bot, CHECKER_ID, report_body)
    total_sent = len(all_sent_ids)
    logger.info(
        f"Уведомлений отправлено: {total_sent}"
    )
    logger.info(f"Не удалось отправить уведомления: {failed_count}")
