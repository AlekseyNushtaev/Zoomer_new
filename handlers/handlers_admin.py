import random
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select

from bot import sql, x3, bot
from config import ADMIN_IDS, CHECKER_ID
from telegram_ids import is_telegram_chat_id
from config_bd.models import Users
from X3 import panel_username_for_site_user
from keyboard import create_kb, STYLE_SUCCESS, STYLE_PRIMARY, STYLE_DANGER, keyboard_sub_after_buy
from lexicon import lexicon
from logging_config import logger
import asyncio
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from sheduler.check_connect import check_connect

_ADD_7_MAY_GIFT_HTML = (
    "🎁 <b>Сюрприз от Zoomer VPN</b>\n\n"
    "Добрый день!\n\n"
    "Мы дарим вам <b>7 дней бесплатного доступа</b> к VPN — с благодарностью, что вы с нами. "
    "Пользуйтесь спокойно и безопасно в сети. ✨\n\n"
    "Ниже — кнопка, чтобы сразу перейти к подключению 👇"
)

router = Router()

_MSK = timezone(timedelta(hours=3))


def _msk_dt_str(dt: Optional[datetime]) -> str:
    if dt is None:
        return "Нет"
    if dt.tzinfo is None:
        aware = dt.replace(tzinfo=timezone.utc)
    else:
        aware = dt.astimezone(timezone.utc)
    return aware.astimezone(_MSK).strftime("%d-%m-%Y %H:%M МСК")


def _pay_dt_str(dt: Optional[datetime]) -> str:
    """Формат даты для /pay: YYYY-MM-DD HH:MM:SS (МСК)."""
    if dt is None:
        return "Нет"
    if dt.tzinfo is None:
        aware = dt.replace(tzinfo=timezone.utc)
    else:
        aware = dt.astimezone(timezone.utc)
    return aware.astimezone(_MSK).strftime("%Y-%m-%d %H:%M:%S")


def _pay_panel_sub_line(activ_result: dict) -> str:
    t = activ_result.get("time", "-")
    if t in (None, "", "-"):
        return "Нет"
    try:
        parsed = datetime.strptime(str(t).replace(" МСК", "").strip(), "%d-%m-%Y %H:%M")
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(t)


_SUB_TIER_LABELS = {
    "main": "💫 подписка на VPN",
    "white": "🦾 Включи мобильный интернет",
}


def _panel_usernames_from_row(row: tuple) -> tuple[str, str]:
    """Пара username в панели: обычная, вайт (как в web_api._panel_vpn_usernames)."""
    tg_col = row[1]
    linked = row[28]
    tg = None
    if tg_col is not None and int(tg_col) > 0:
        tg = int(tg_col)
    elif linked is not None and int(linked) > 0:
        tg = int(linked)
    if tg is not None:
        s = str(tg)
        return s, f"{s}_white"
    db_uid = int(tg_col)
    return panel_username_for_site_user(db_uid, False), panel_username_for_site_user(db_uid, True)


def _split_long_text(text: str, limit: int = 3800) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    rest = text
    while rest:
        parts.append(rest[:limit])
        rest = rest[limit:]
    return parts


_ADD7WHITE_CB = "add7white_start"
_ADD7REG_CB = "add7regular_start"
_ADD7ALL_PREVIEW_CB = "add7all_preview"
_ADD7ALL_YES_CB = "add7all_yes"
_ADD7ALL_NO_CB = "add7all_no"

_ADD7ALL_PROMO_TEXT = (
    "Самое время вернутся в Зумерский ВПН — дарим 7 дней тестдрайва новых серверов🟢\n\n"
    "Подключение займет пару секунд\n\n"
    "Жми👇"
)

_ADD7ALL_TRIAL_KB = create_kb(
    1,
    styles={"trial_return_get": STYLE_SUCCESS},
    trial_return_get="🔥Получить ТРИАЛ",
)

_ADD7WHITE_USER_TEXT = (
    "✅ Неполадки устранены, а мы добавили вам 7 дней к подписке  '🦾 Включи мобильный интернет', как и обещали. "
    "Оставайтесь с нами! 🙏✨"
)

_ADD7REG_USER_TEXT = (
    "✅ Неполадки устранены, а мы добавили вам 7 дней к подписке '💫 VPN PRO', как и обещали. "
    "Оставайтесь с нами! 🙏✨"
)

# Сквады обычной подписки (для /new): хотя бы один uuid в activeInternalSquads
_NEW_PANEL_SQUAD_UUIDS = frozenset(
    {
        "2a2236d1-517b-4015-b961-eae22d2ef7fe",
        "889e0d7a-cfa6-4bf9-b2ed-3cb7a1b44cbd",
    }
)

_NEW_BULK_SQUAD_CHOICES = (
    "2a2236d1-517b-4015-b961-eae22d2ef7fe",
    "889e0d7a-cfa6-4bf9-b2ed-3cb7a1b44cbd",
)
_NEW_BULK_UUID_BATCH = 500


@router.message(F.video, F.from_user.id.in_(ADMIN_IDS))
async def get_video(message: Message):
    await message.answer(message.video.file_id)


@router.message(F.photo, F.from_user.id.in_(ADMIN_IDS))
async def get_photo(message: Message):
    await message.answer(message.photo[-1].file_id)


@router.message(Command(commands=['user']))
async def user_info(message: Message):

    # Проверка прав администратора
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        # Извлекаем аргументы команды
        args = message.text.split()

        if len(args) < 2:
            await message.answer("❌ Использование: /user <telegram_id>\nНапример: /user 123456789")
            return

        user_id = int(args[1].strip())

        # Проверяем, существует ли пользователь в БД
        user_data = await sql.get_user(user_id)

        if not user_data:
            await message.answer(f"❌ Пользователь с ID {user_id} не найден в базе данных.")
            return
        text = []
        for i in range(len(user_data)):
            if isinstance(user_data[i], datetime):
                item = user_data[i].strftime('%Y-%m-%d %H:%M:%S')
                text.append(item)
            elif user_data[i] is None:
                text.append('None')
            else:
                text.append(str(user_data[i]))
        text = '\n'.join(text)
        await message.answer(text)
    except Exception as e:
        await message.answer(f'Ошибка при формировании сообщения: {str(e)}')


@router.message(Command(commands=['pay']))
async def pay_info_command(message: Message):
    """Сводка подписок (БД / панель) и успешные платежи пользователя."""
    if message.from_user.id not in ADMIN_IDS:
        return

    args = (message.text or "").split()
    if len(args) < 2:
        await message.answer("❌ Использование: /pay <telegram_id>\nНапример: /pay 123456789")
        return

    try:
        target_id = int(args[1].strip())
    except ValueError:
        await message.answer("❌ ID должен быть числом.")
        return

    user_row = await sql.get_user(target_id)
    if not user_row:
        await message.answer(f"❌ Пользователь {target_id} не найден в базе данных.")
        return

    reg_un, white_un = _panel_usernames_from_row(user_row)
    sub_db = user_row[9]
    white_db = user_row[10]

    try:
        ar_reg, ar_white = await asyncio.gather(
            x3.activ(reg_un),
            x3.activ(white_un),
        )
    except Exception as e:
        logger.exception("/pay: панель")
        await message.answer(f"❌ Ошибка запроса к панели: {e}")
        return

    pay_rows = await sql.get_user_subscription_payment_report(target_id)
    pay_lines: list[str] = []
    for tc, kind, days_s in pay_rows:
        ts = _pay_dt_str(tc)
        pay_lines.append(f"• {ts} — {kind} — {days_s} дн.")

    body = (
        f"<b>/pay {target_id}</b>\n\n"
        f"Подписка обычная в БД бота — {_pay_dt_str(sub_db)}\n"
        f"Подписка обычная в панели — {_pay_panel_sub_line(ar_reg)}\n"
        f"Подписка вайт в БД бота — {_pay_dt_str(white_db)}\n"
        f"Подписка вайт в панели — {_pay_panel_sub_line(ar_white)}\n\n"
        f"<b>Платежи:</b>\n"
    )
    if pay_lines:
        body += "\n".join(pay_lines)
    else:
        body += "Нет"

    for chunk in _split_long_text(body):
        await message.answer(chunk)


async def _partner_admin_stats_text(tg_id: int) -> Optional[str]:
    user = await sql.get_user_object_by_user_id(tg_id)
    if user is None:
        return None
    if not user.partner_flag:
        return "not_partner"

    referrals = await sql.select_partner_count(tg_id)
    payments_sum = await sql.select_partner_referrals_payments_sum(tg_id)
    balance = user.partner_balance or 0
    paid_out = user.partner_pay or 0
    total_earned = balance + paid_out

    return (
        f"📊 <b>Статистика {tg_id}:</b>\n\n"
        f"👥 Друзей перешло (/start): <b>{referrals}</b>\n"
        f"💳 Приобретено подписок друзьями на: <b>{payments_sum} ₽</b>\n\n"
        f"💵 Заработок партнёра (всего): <b>{total_earned} ₽</b>\n"
        f"✅ Выведено: <b>{paid_out} ₽</b>\n"
        f"🏦 Осталось на вывод: <b>{balance} ₽</b>"
    )


@router.message(Command(commands=['partner']))
async def partner_info_command(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    args = (message.text or "").split()
    if len(args) < 2:
        await message.answer(
            "❌ Использование: /partner <telegram_id>\nНапример: /partner 123456789"
        )
        return

    try:
        target_id = int(args[1].strip())
    except ValueError:
        await message.answer("❌ ID должен быть числом.")
        return

    try:
        text = await _partner_admin_stats_text(target_id)
    except Exception as e:
        logger.exception("/partner")
        await message.answer(f"❌ Ошибка: {e}")
        return

    if text is None:
        await message.answer(f"❌ Пользователь {target_id} не найден в базе данных.")
        return
    if text == "not_partner":
        await message.answer(
            f"❌ Пользователь {target_id} не участвует в партнёрской программе "
            f"(partner_flag = False)."
        )
        return

    await message.answer(text, parse_mode="HTML")


@router.message(Command(commands=['partner_remove']))
async def partner_remove_command(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    args = (message.text or "").split()
    if len(args) < 3:
        await message.answer(
            "❌ Использование: /partner_remove <telegram_id> <сумма>\n"
            "Например: /partner_remove 123456789 500"
        )
        return

    try:
        target_id = int(args[1].strip())
        amount = int(args[2].strip())
    except ValueError:
        await message.answer("❌ ID и сумма должны быть целыми числами.")
        return

    ok, err = await sql.partner_record_payout(target_id, amount)
    if not ok:
        await message.answer(f"❌ {err}")
        return

    stats = await _partner_admin_stats_text(target_id)
    if stats and stats != "not_partner":
        await message.answer(
            f"✅ Списано <b>{amount} ₽</b> с баланса, добавлено в «Выведено».\n\n{stats}",
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"✅ Списано {amount} ₽ с баланса пользователя {target_id}, добавлено в partner_pay."
        )


@router.message(Command(commands=['sub']))
async def set_subscription_date(message: Message):
    """Установка subscription_end_date или white_subscription_end_date в БД и панели"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Эта команда доступна только администраторам.")
        return

    try:
        args = message.text.split()
        if len(args) < 3:
            await message.answer(
                "❌ Использование:\n"
                "  /sub <telegram_id> <дата_время>               – обновить обычную подписку\n"
                "  /sub <telegram_id> white <дата_время>         – обновить белую подписку\n"
                "Примеры:\n"
                "  /sub 123456789 2026-02-01 17:14:27\n"
                "  /sub 123456789 white 2026-02-01 17:14:27\n"
                "Формат даты: YYYY-MM-DD HH:MM:SS"
            )
            return

        user_id = int(args[1].strip())

        # Определяем тип и позицию даты
        if args[2].lower() == 'white':
            is_white = True
            date_str = " ".join(args[3:])
        else:
            is_white = False
            date_str = " ".join(args[2:])

        # Парсим дату
        date_formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M"
        ]
        target_date = None
        for fmt in date_formats:
            try:
                target_date = datetime.strptime(date_str, fmt)
                target_date = target_date.replace(tzinfo=timezone.utc)  # панель работает в UTC
                break
            except ValueError:
                continue
        if target_date is None:
            await message.answer(f"❌ Неверный формат даты: {date_str}")
            return

        # Проверяем наличие пользователя в БД
        user_data = await sql.get_user(user_id)
        if not user_data:
            await message.answer("⚠️ Пользователь не найден в БД.")
            return

        # Формируем username для панели
        username = str(user_id) + ('_white' if is_white else '')

        # Устанавливаем дату в панели
        success, actual_date = await x3.set_expiration_date(username, target_date, user_id)

        if not success or actual_date is None:
            await message.answer("❌ Не удалось установить дату в панели. Подробности в логах.")
            return

        if is_white:
            await sql.update_white_subscription_end_date(user_id, actual_date)
        else:
            await sql.update_subscription_end_date(user_id, actual_date)

        tier = "white" if is_white else "main"
        notify_status = ""
        if is_telegram_chat_id(user_id):
            try:
                sub_link = await x3.sublink(username)
                user_text = lexicon["sub_granted_notify"].format(
                    tier=_SUB_TIER_LABELS.get(tier, tier),
                    end_date=_msk_dt_str(actual_date),
                )
                await bot.send_message(
                    user_id,
                    user_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=keyboard_sub_after_buy(sub_link) if sub_link else None,
                )
                notify_status = "\n📨 Пользователь уведомлён."
            except Exception as e:
                logger.error(f"/sub: не удалось уведомить user={user_id}: {e}")
                notify_status = f"\n⚠️ Не удалось уведомить пользователя: {e}"
        else:
            notify_status = "\nℹ️ Уведомление не отправлено (не Telegram ID)."

        await message.answer(
            f"✅ Дата подписки успешно установлена!\n\n"
            f"👤 Пользователь: {user_id}\n"
            f"🔑 Панель: {username}\n"
            f"📅 Целевая дата (UTC): {target_date.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📅 Установленная в панели дата (UTC): {actual_date.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📝 Тариф: {_SUB_TIER_LABELS.get(tier, tier)}\n"
            f"💾 База данных обновлена."
            f"{notify_status}"
        )

    except Exception as e:
        logger.error(f"Ошибка в команде /sub: {e}")
        await message.answer(f"❌ Произошла ошибка: {str(e)}")


@router.message(Command(commands=['delete']))
async def delete_user_command(message: Message):
    """Удаление пользователя из БД по Telegram ID"""

    # Проверка прав администратора
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        # Извлекаем аргументы команды
        args = message.text.split()

        if len(args) < 2:
            await message.answer("❌ Использование: /delete <telegram_id>\nНапример: /delete 123456789")
            return

        user_id_to_delete = int(args[1].strip())

        # Проверяем, существует ли пользователь в БД
        user_data = await sql.get_user(user_id_to_delete)

        if not user_data:
            await message.answer(f"❌ Пользователь с ID {user_id_to_delete} не найден в базе данных.")
            return

        # Получаем информацию о пользователе для уведомления
        user_info = {
            "user_id": user_data[1],  # User_id
            "ref": user_data[2],  # Ref
            "in_panel": user_data[4],
            "in_chanel": user_data[7] if len(user_data) > 7 else False,
        }

        # УДАЛЯЕМ ПОЛЬЗОВАТЕЛЯ ИЗ БД
        deletion_success = await sql.delete_from_db(user_id_to_delete)

        if deletion_success:
            # Логируем действие
            logger.info(f"Администратор {message.from_user.id} удалил пользователя {user_id_to_delete} из БД")

            # Формируем отчет об удалении
            report_message = (
                f"✅ Пользователь успешно удалён из базы данных\n\n"
                f"📋 Информация об удалённом пользователе:\n"
                f"├ ID: {user_info['user_id']}\n"
                f"├ Реферер: {user_info['ref'] if user_info['ref'] else 'нет'}\n"
                f"├ Брал ключ: {'✅ да' if user_info['in_panel'] else '❌ нет'}\n"
                f"└ В канале: {'✅ да' if user_info['in_chanel'] else '❌ нет'}\n\n"
                f"⚠️ Пользователь удалён только из базы данных бота.\n"
                f"   Подписка в панели управления (X3) остаётся активной.\n"
                f"   Чтобы удалить полностью, используйте команду /gift на 0 дней."
            )

            await message.answer(report_message)

        else:
            await message.answer(f"❌ Ошибка при удалении пользователя {user_id_to_delete}.\n"
                                 "Возможно, пользователь уже был удалён или произошла ошибка базы данных.")

    except ValueError:
        await message.answer("❌ Неверный формат Telegram ID.\n"
                             "Используйте только цифры, например: /delete 123456789")
    except Exception as e:
        logger.error(f"Ошибка в команде /delete: {e}")
        await message.answer(f"❌ Произошла ошибка при выполнении команды: {str(e)}")


@router.message(Command("online"))
async def check_online(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    users_x3 = await x3.get_all_users()

    active_telegram_ids = []
    for user in users_x3:
        if user['userTraffic']['firstConnectedAt']:
            connected_str = user['userTraffic']['onlineAt']
            try:
                connected_dt = datetime.fromisoformat(connected_str.replace('Z', '+00:00'))
                connected_date = connected_dt.date()
                if connected_date == datetime.now().date():
                    telegram_id = user.get('telegramId')
                    if telegram_id is not None:
                        active_telegram_ids.append(int(telegram_id))
            except (ValueError, TypeError):
                continue

    count_pay = 0
    count_trial = 0
    for tg_id in active_telegram_ids:
        user_data = await sql.get_user(tg_id)
        if user_data:
            if user_data[8]:
                count_pay += 1
            else:
                count_trial += 1
    await message.answer(
        f"Всего юзеров в панели: {len(users_x3)}\n"
        f"Юзеров, которые были онлайн сегодня: {len(active_telegram_ids)}\n"
        f"Юзеры с платной подпиской: {count_pay}\n"
        f"Юзеры на триале: {count_trial}"
    )


@router.message(Command("balance_panel"))
async def check_online(message: Message):
    squad_1 = ['2a2236d1-517b-4015-b961-eae22d2ef7fe']
    squad_2 = ['889e0d7a-cfa6-4bf9-b2ed-3cb7a1b44cbd']
    success_count = 0
    fail_count = 0
    if message.from_user.id not in ADMIN_IDS:
        return
    users_x3 = await x3.get_all_users()
    for user in users_x3:
        try:
            await asyncio.sleep(0.3)
            random_squad = random.choice([squad_1, squad_2])
            username = user.get('username', '')
            if 'white' not in username and 'cascade-bridge-system' not in username:
                uuid = user.get('uuid')
                if user['userTraffic']['firstConnectedAt']:
                    connected_str = user['userTraffic']['onlineAt']
                    connected_dt = datetime.fromisoformat(connected_str.replace('Z', '+00:00'))
                    connected_date = connected_dt.date()
                    if connected_date == datetime.now().date() and uuid:
                        if await x3.update_user_squads(uuid, random_squad):
                            success_count += 1
                        else:
                            fail_count += 1
        except:
            pass
    await message.answer(f"{len(users_x3)} - всего юзеров в панели\n{success_count + fail_count} - онлайн сегодня\n{success_count} - обновлено\n{fail_count} - ошибка")


@router.message(Command(commands=['get_second']))
async def get_second_command(message: Message):
    """Проверяет, сколько пользователей с ttclid='second_chance_100326' были онлайн после 10.03.2026"""
    if message.from_user.id not in ADMIN_IDS:
        return

    await message.answer("🔄 Получаю данные из панели и базы...")

    try:
        # 1. Получаем всех пользователей с нужным ttclid
        async with sql.session_factory() as session:
            stmt = select(Users.user_id).where(Users.ttclid == 'second_chance_100326')
            result = await session.execute(stmt)
            user_ids = [row[0] for row in result.all()]

        if not user_ids:
            await message.answer("❌ Нет пользователей с ttclid = second_chance_100326")
            return

        # 2. Загружаем всех пользователей из панели
        panel_users = await x3.get_all_users()  # список словарей с полными данными
        logger.info(f"Загружено {len(panel_users)} пользователей из панели")

        # 3. Строим множество telegram_id из панели для быстрого поиска
        #    и сохраняем дату последнего онлайна
        panel_dict = {}
        for user in panel_users:
            tg_id = user.get('telegramId')
            if tg_id is not None:
                panel_dict[int(tg_id)] = user

        # 4. Проверяем каждого пользователя из списка
        cutoff_date = datetime(2026, 3, 10, 0, 0, 0, tzinfo=timezone.utc)
        online_after_cutoff = 0
        not_found_in_panel = 0
        online_before_or_never = 0

        for uid in user_ids:
            user_panel = panel_dict.get(uid)
            if not user_panel:
                not_found_in_panel += 1
                continue

            # Проверяем onlineAt (последнее подключение)
            online_at_str = user_panel.get('userTraffic', {}).get('onlineAt')
            if not online_at_str:
                online_before_or_never += 1
                continue

            try:
                online_dt = datetime.fromisoformat(online_at_str.replace('Z', '+00:00'))
                if online_dt >= cutoff_date:
                    online_after_cutoff += 1
                else:
                    online_before_or_never += 1

            except (ValueError, TypeError):
                online_before_or_never += 1

        # 5. Формируем ответ
        report = (
            f"📊 Статистика по ttclid = second_chance_100326\n"
            f"👥 Всего в БД: {len(user_ids)}\n"
            f"✅ Онлайн после 10.03.2026: {online_after_cutoff}\n"
            f"❌ Не были онлайн после 10.03.2026 (или никогда): {online_before_or_never}\n"
            f"🔍 Не найдены в панели: {not_found_in_panel}"
        )
        await message.answer(report)
        logger.info(f"Админ {message.from_user.id} выполнил /get_second: {report}")

    except Exception as e:
        logger.error(f"Ошибка в /get_second: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")


@router.message(Command(commands=['check_users']))
async def check_users_command(message: Message):
    """Проверка соответствия дат окончания подписки у оплаченных пользователей (reserve_field=True)"""
    if message.from_user.id not in ADMIN_IDS:
        return

    await message.answer("🔄 Начинаю проверку пользователей с оплатами...")

    try:
        # 1. Получаем список оплаченных пользователей из БД
        users_with_discount = await sql.get_users_with_payment()
        total = len(users_with_discount)
        if total == 0:
            await message.answer("❌ Нет пользователей с оплатами.")
            return

        # 2. Получаем всех пользователей из панели (один запрос)
        panel_users = await x3.get_all_users()
        logger.info(f"Загружено {len(panel_users)} пользователей из панели")

        # 3. Строим словарь для быстрого поиска по telegramId и username
        panel_by_telegram = {}      # ключ: telegramId (int)
        panel_by_username = {}      # ключ: username (str)

        for user in panel_users:
            tg_id = user.get('telegramId')
            username = user.get('username')
            if tg_id is not None:
                panel_by_telegram[int(tg_id)] = user
            elif username:
                panel_by_username[username] = user

        # 4. Проходим по всем оплаченным пользователям и ищем их в панели
        mismatched = []      # кортежи (user_id, db_date, panel_date) для расхождений >=3ч
        not_found_in_panel = []  # пользователи, отсутствующие в панели
        processed = 0

        for user_id in users_with_discount:
            processed += 1
            if processed % 10 == 0:
                logger.info(f"Проверено {processed}/{total}")

            # Пытаемся найти пользователя в панели
            panel_user = panel_by_telegram.get(user_id)
            if panel_user is None:
                panel_user = panel_by_username.get(str(user_id))

            if panel_user is None:
                not_found_in_panel.append(user_id)
                continue

            expire_str = panel_user.get('expireAt')
            if not expire_str:
                # нет даты в панели – считаем расхождением (panel_date = None)
                db_expire = await sql.get_subscription_end_date(user_id)
                mismatched.append((user_id, db_expire, None))
                continue

            try:
                panel_expire = datetime.fromisoformat(expire_str.replace('Z', '+00:00'))
            except Exception:
                # не удалось распарсить дату панели
                db_expire = await sql.get_subscription_end_date(user_id)
                mismatched.append((user_id, db_expire, None))
                continue

            # Получаем дату из БД (обычная подписка)
            db_expire = await sql.get_subscription_end_date(user_id)
            panel_naive = panel_expire.replace(tzinfo=None)

            if db_expire is None:
                # нет даты в БД
                mismatched.append((user_id, None, panel_naive))
                continue

            db_naive = db_expire.replace(tzinfo=None)
            diff_hours = abs((panel_naive - db_naive).total_seconds()) / 3600

            if diff_hours >= 4:
                mismatched.append((user_id, db_naive, panel_naive))

        # 5. Формируем отчёт
        report_lines = []
        report_lines.append(f"📊 Результаты проверки:\n")
        report_lines.append(f"👥 Всего проверено: {total}")
        report_lines.append(f"❌ Расхождений в датах (>=4ч): {len(mismatched)}")
        report_lines.append(f"🔍 Не найдены в панели: {len(not_found_in_panel)}")

        # Если есть расхождения и их количество не превышает лимит для прямого вывода
        if mismatched or not_found_in_panel:
            if len(mismatched) <= 50 and len(not_found_in_panel) <= 50:
                if mismatched:
                    report_lines.append("\n🆔 Расхождения (команды для синхронизации):")
                    for uid, db_dt, panel_dt in mismatched:
                        db_str = db_dt.strftime('%Y-%m-%d %H:%M:%S') if db_dt else 'None'
                        panel_str = panel_dt.strftime('%Y-%m-%d %H:%M:%S') if panel_dt else 'None'
                        report_lines.append(f"/sub {uid} {db_str} /sub {uid} {panel_str}")
                if not_found_in_panel:
                    report_lines.append("\n🆔 Не найдены в панели:")
                    report_lines.extend(str(uid) for uid in not_found_in_panel)
                await message.answer("\n".join(report_lines))
            else:
                # Если много расхождений – отправляем файлом
                import io
                text_io = io.StringIO()
                text_io.write("user_id\tdb_date\tpanel_date\n")
                for uid, db_dt, panel_dt in mismatched:
                    db_str = db_dt.strftime('%Y-%m-%d %H:%M:%S') if db_dt else 'None'
                    panel_str = panel_dt.strftime('%Y-%m-%d %H:%M:%S') if panel_dt else 'None'
                    text_io.write(f"/sub {uid} {db_str} /sub {uid} {panel_str}\n")
                for uid in not_found_in_panel:
                    text_io.write(f"{uid}\tnot_found\n")
                text_io.seek(0)
                from aiogram.types import BufferedInputFile
                file_data = BufferedInputFile(text_io.getvalue().encode(), filename="check_users_report.txt")
                await message.answer_document(
                    document=file_data,
                    caption="\n".join(report_lines[:5])
                )
        else:
            await message.answer("✅ Все оплаченные пользователи синхронизированы (разница менее 3 часов).")

    except Exception as e:
        logger.exception("Ошибка в /check_users")
        await message.answer(f"❌ Ошибка: {str(e)}")


@router.message(Command(commands=['new']))
async def new_panel_users_command(message: Message):
    """2 сквада обычной подписки → 3 чанка → POST bulk/update-squads по порядку; сквад на каждый HTTP — random из двух."""
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        users = await x3.get_all_panel()
        total = len(users)
        if not users:
            empty_report = (
                "/new: get_all_panel пуст\n"
                "С 2 сквадами: 0\n"
                "Чанк 1: 0\n"
                "Чанк 2: 0\n"
                "Чанк 3: 0"
            )
            print(empty_report + "\n", flush=True)
            await message.answer(empty_report)
            logger.info(f"Админ {message.from_user.id} /new: панель пуста")
            return

        now_utc = datetime.now(timezone.utc)
        today_utc = now_utc.date()
        allowed = _NEW_PANEL_SQUAD_UUIDS

        def expire_date_utc(u: dict):
            s = u.get("expireAt")
            if not s:
                return None
            try:
                dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.date()
            except (ValueError, TypeError):
                return None

        def subscription_ok(u: dict) -> bool:
            d = expire_date_utc(u)
            return d is not None and d >= today_utc

        def first_connected_at(u: dict):
            ut = u.get("userTraffic")
            if not isinstance(ut, dict):
                return None
            return ut.get("firstConnectedAt")

        def has_allowed_squad(u: dict) -> bool:
            squads = u.get("activeInternalSquads") or []
            for s in squads:
                uid = s.get("uuid") if isinstance(s, dict) else s
                if uid is not None and str(uid).lower() in allowed:
                    return True
            return False

        # Шаг 1: из всей панели только те, у кого в activeInternalSquads есть один из сквадов обычной подписки
        with_allowed_squads = [u for u in users if has_allowed_squad(u)]
        n_allowed = len(with_allowed_squads)

        # Шаг 2: разбиваем только этих пользователей на 3 чанка
        chunk1 = []
        chunk2 = []
        chunk3 = []
        for u in with_allowed_squads:
            if subscription_ok(u) and first_connected_at(u) is not None:
                chunk1.append(u)
            elif subscription_ok(u) and first_connected_at(u) is None:
                chunk2.append(u)
            else:
                chunk3.append(u)

        n1, n2, n3 = len(chunk1), len(chunk2), len(chunk3)
        report = (
            f"/new: в панели записей {total}\n"
            f"С одним из сквадов обычной подписки (activeInternalSquads): {n_allowed}\n"
            f"Чанк 1 — подписка ≥ сегодня UTC, firstConnectedAt не None: {n1}\n"
            f"Чанк 2 — подписка ≥ сегодня UTC, firstConnectedAt None: {n2}\n"
            f"Чанк 3 — остальные из этих {n_allowed}: {n3}"
        )

        async def bulk_apply_chunk(chunk: list, label: str) -> str:
            uuids = [str(u["uuid"]) for u in chunk if u.get("uuid")]
            if not uuids:
                return f"bulk чанк {label}: пусто, пропуск"
            total_affected = 0
            all_ok = True
            n_batches = (len(uuids) + _NEW_BULK_UUID_BATCH - 1) // _NEW_BULK_UUID_BATCH
            for off in range(0, len(uuids), _NEW_BULK_UUID_BATCH):
                batch = uuids[off : off + _NEW_BULK_UUID_BATCH]
                squad = random.choice(_NEW_BULK_SQUAD_CHOICES)
                ok, aff = await x3.bulk_update_internal_squads(batch, [squad])
                total_affected += aff
                if not ok:
                    all_ok = False
                bi = off // _NEW_BULK_UUID_BATCH + 1
                logger.info(
                    f"/new bulk чанк {label} HTTP {bi}/{n_batches}: "
                    f"squad={squad} batch_size={len(batch)} ok={ok} affected={aff}"
                )
                await asyncio.sleep(0.15)
            st = "ok" if all_ok else "были ошибки (см. лог)"
            return (
                f"bulk чанк {label}: UUID {len(uuids)}, батчей {n_batches}, "
                f"affected_rows Σ={total_affected}, сквад на каждый запрос случайный, {st}"
            )

        bulk_lines = [
            "",
            "POST /api/users/bulk/update-squads (чанки 1→2→3, на каждый запрос свой random сквад):",
            await bulk_apply_chunk(chunk1, "1"),
            await bulk_apply_chunk(chunk2, "2"),
            await bulk_apply_chunk(chunk3, "3"),
        ]
        full_report = report + "\n" + "\n".join(bulk_lines)
        print(full_report + "\n", flush=True)
        await message.answer(full_report)
        logger.info(
            f"Админ {message.from_user.id} /new: чанки {n1}/{n2}/{n3}, всего в панели {total}"
        )

    except Exception as e:
        logger.exception("Ошибка в /new")
        await message.answer(f"❌ Ошибка: {str(e)}")


@router.message(Command(commands=['shortuuid_export']))
async def shortuuid_export_command(message: Message):
    """Синхронизация shortUuid из панели в поля subscribtion / white_subscription в БД."""
    if message.from_user.id not in ADMIN_IDS:
        return

    await message.answer("🔄 Загружаю пользователей из панели и обновляю shortUuid в БД...")

    try:
        panel_users = await x3.get_all_users()
        updated_sub = 0
        updated_white = 0
        skipped_no_telegram = 0
        not_in_db = 0
        skipped_no_short = 0
        errors = 0

        for panel_user in panel_users:
            tg_raw = panel_user.get("telegramId")
            if tg_raw is None:
                skipped_no_telegram += 1
                continue
            try:
                tg_id = int(tg_raw)
            except (TypeError, ValueError):
                skipped_no_telegram += 1
                continue

            if not await sql.get_user(tg_id):
                not_in_db += 1
                continue

            short_uuid = panel_user.get("shortUuid") or panel_user.get("shortuuid")
            if not short_uuid:
                skipped_no_short += 1
                continue

            username = (panel_user.get("username") or "").strip()
            is_white = username.endswith("_white")

            try:
                if is_white:
                    await sql.update_white_subscription(tg_id, short_uuid)
                    updated_white += 1
                    logger.info(f"white_subscription обновлен для tg_id={tg_id}: {short_uuid}")
                else:
                    await sql.update_subscribtion(tg_id, short_uuid)
                    logger.info(f"subscribtion обновлен для tg_id={tg_id}: {short_uuid}")
                    updated_sub += 1
            except Exception as e:
                errors += 1
                logger.error(f"/shortuuid_export: ошибка для tg_id={tg_id}: {e}")

        report = (
            f"✅ Готово.\n"
            f"👥 Записей в панели (после фильтра): {len(panel_users)}\n"
            f"📝 subscribtion обновлено: {updated_sub}\n"
            f"📝 white_subscription обновлено: {updated_white}\n"
            f"⏭ Без telegramId: {skipped_no_telegram}\n"
            f"⏭ Нет в БД: {not_in_db}\n"
            f"⏭ Нет shortUuid в панели: {skipped_no_short}\n"
            f"❌ Ошибок записи: {errors}"
        )
        await message.answer(report)
        logger.info(f"Админ {message.from_user.id} выполнил /shortuuid_export: {report}")

    except Exception as e:
        logger.exception("Ошибка в /shortuuid_export")
        await message.answer(f"❌ Ошибка: {str(e)}")


@router.message(Command("import_panel_white"))
async def import_panel_white_command(message: Message):
    """Для всех с непустым white_subscription_end_date: создать white-клиента в панели из БД."""
    if message.from_user.id not in ADMIN_IDS:
        return

    await message.answer("🔄 Выбираю пользователей и создаю записи white в панели…")

    try:
        async with sql.session_factory() as session:
            stmt = select(
                Users.user_id,
                Users.white_subscription_end_date,
                Users.white_subscription,
            ).where(Users.white_subscription_end_date.isnot(None))
            result = await session.execute(stmt)
            rows = result.all()

        ok = 0
        fail = 0
        skipped_no_short = 0

        for row in rows:
            uid = int(row[0])
            end_dt = row[1]
            short_u = row[2]
            if not (short_u or "").strip():
                skipped_no_short += 1
                continue
            await asyncio.sleep(0.12)
            if await x3.create_white_user_import_panel(uid, short_u, end_dt):
                ok += 1
            else:
                fail += 1

        report = (
            f"✅ Готово.\n"
            f"📋 В выборке (white_subscription_end_date не NULL): {len(rows)}\n"
            f"✔ Создано в панели: {ok}\n"
            f"❌ Ошибок: {fail}\n"
            f"⏭ Пропущено (пустой white_subscription): {skipped_no_short}"
        )
        await message.answer(report)
        logger.info(
            "Админ %s /import_panel_white: выборка=%s ok=%s fail=%s skip_short=%s",
            message.from_user.id,
            len(rows),
            ok,
            fail,
            skipped_no_short,
        )

    except Exception as e:
        logger.exception("Ошибка в /import_panel_white")
        await message.answer(f"❌ Ошибка: {str(e)}")


@router.message(Command("import_panel_active"))
async def import_panel_active_command(message: Message):
    """
    Пользователи с активной обычной подпиской (subscription_end_date не NULL и в будущем):
    при необходимости генерирует subscribtion, создаёт запись в панели, ставит field_bool_2.
    """
    if message.from_user.id not in ADMIN_IDS:
        return

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        async with sql.session_factory() as session:
            stmt = (
                select(
                    Users.user_id,
                    Users.subscription_end_date,
                    Users.subscribtion,
                )
                .where(
                    Users.subscription_end_date.isnot(None),
                    Users.subscription_end_date > now,
                )
                .order_by(Users.user_id)
            )
            result = await session.execute(stmt)
            rows = result.all()

        total = len(rows)
        await message.answer(
            f"🔄 Активных по subscription_end_date: {total}\n"
            f"Создаю записи в панели…"
        )

        ok = 0
        fail = 0
        generated_short = 0

        for idx, row in enumerate(rows, start=1):
            uid = int(row[0])
            end_dt = row[1]
            short_u = row[2]

            if not (short_u or "").strip():
                short_u = x3.generate_client_id(uid)
                await sql.update_subscribtion(uid, short_u)
                generated_short += 1

            await asyncio.sleep(0.12)
            if await x3.create_regular_user_import_panel(uid, short_u, end_dt):
                ok += 1
                await sql.update_field_bool_2(uid, True)
            else:
                fail += 1

            if idx % 1000 == 0:
                await message.answer(
                    f"📊 import_panel_active: обработано {idx} / {total}\n"
                    f"✔ ок: {ok}, ❌ ошибок: {fail}, shortUuid сгенерировано: {generated_short}"
                )

        report = (
            f"✅ Готово.\n"
            f"📋 В выборке: {total}\n"
            f"✔ Создано в панели: {ok}\n"
            f"❌ Ошибок: {fail}\n"
            f"🆕 Сгенерировано subscribtion: {generated_short}"
        )
        await message.answer(report)
        logger.info(
            "Админ %s /import_panel_active: выборка=%s ok=%s fail=%s gen_short=%s",
            message.from_user.id,
            total,
            ok,
            fail,
            generated_short,
        )

    except Exception as e:
        logger.exception("Ошибка в /import_panel_active")
        await message.answer(f"❌ Ошибка: {str(e)}")


@router.message(Command("import_panel_all"))
async def import_panel_all_command(message: Message):
    """
    Все с непустым subscription_end_date и field_bool_2=False:
    при необходимости генерирует subscribtion, создаёт запись в панели (expireAt = UTC сейчас + 1 ч),
    при успехе ставит field_bool_2.
    """
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        async with sql.session_factory() as session:
            stmt = (
                select(
                    Users.user_id,
                    Users.subscription_end_date,
                    Users.subscribtion,
                )
                .where(
                    Users.subscription_end_date.isnot(None),
                    Users.field_bool_2.is_(False),
                )
                .order_by(Users.user_id)
            )
            result = await session.execute(stmt)
            rows = result.all()

        total = len(rows)
        await message.answer(
            f"🔄 import_panel_all: в выборке {total} пользователей\n"
            f"(subscription_end_date не NULL, field_bool_2=false).\n"
            f"Создаю в панели (expireAt = сейчас UTC + 1 ч)…"
        )

        ok = 0
        fail = 0
        generated_short = 0

        for idx, row in enumerate(rows, start=1):
            uid = int(row[0])
            end_dt = row[1]
            short_u = row[2]

            if not (short_u or "").strip():
                short_u = x3.generate_client_id(uid)
                await sql.update_subscribtion(uid, short_u)
                generated_short += 1

            expire_override = datetime.utcnow() + timedelta(hours=1)

            await asyncio.sleep(0.12)
            if await x3.create_regular_user_import_panel(
                uid, short_u, end_dt, expire_at_override=expire_override
            ):
                ok += 1
                await sql.update_field_bool_2(uid, True)
            else:
                fail += 1

            if idx % 1000 == 0:
                await message.answer(
                    f"📊 import_panel_all: обработано {idx} / {total}\n"
                    f"✔ ок: {ok}, ❌ ошибок: {fail}, shortUuid сгенерировано: {generated_short}"
                )

        report = (
            f"✅ Готово.\n"
            f"📋 В выборке: {total}\n"
            f"✔ Создано в панели: {ok}\n"
            f"❌ Ошибок: {fail}\n"
            f"🆕 Сгенерировано subscribtion: {generated_short}"
        )
        await message.answer(report)
        logger.info(
            "Админ %s /import_panel_all: выборка=%s ok=%s fail=%s gen_short=%s",
            message.from_user.id,
            total,
            ok,
            fail,
            generated_short,
        )

    except Exception as e:
        logger.exception("Ошибка в /import_panel_all")
        await message.answer(f"❌ Ошибка: {str(e)}")


@router.message(Command(commands=['update_delete']))
async def check_users_command(message: Message):
    """Проверка соответствия дат окончания подписки у оплаченных пользователей (reserve_field=True)"""
    if message.from_user.id not in ADMIN_IDS:
        return
    await sql.update_delete_all(False)
    await message.answer('Все юзеры разблокированы')


@router.message(Command(commands=['reset_bool3']))
async def reset_field_bool_3_all_command(message: Message):
    """Сброс field_bool_3 у всех пользователей (триал / одноразовые акции)."""
    if message.from_user.id not in ADMIN_IDS:
        return
    n = await sql.reset_field_bool_3_all()
    await message.answer(f"Готово: field_bool_3 = false у {n} записей в users.")
    logger.info(f"Админ {message.from_user.id}: сброс field_bool_3 для всех, обновлено строк: {n}")


@router.message(Command(commands=['add_2d']))
async def add_2d_command(message: Message):
    """Всем с непустыми датами: +2 дня к subscription_end_date и white_subscription_end_date."""
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        return
    n_sub, n_white = await sql.bulk_add_2_days_to_subscription_dates()
    await message.answer(
        f"Готово: +2 дня к subscription_end_date — {n_sub} строк; "
        f"+2 дня к white_subscription_end_date — {n_white} строк."
    )
    logger.info(
        "Админ %s: /add_2d subscription_end_date=%s white_subscription_end_date=%s",
        message.from_user.id,
        n_sub,
        n_white,
    )


@router.message(Command(commands=['add_7_to_all']))
async def add_7_to_all_command(message: Message):
    """
    Рассылка: нет PRO или подписка закончилась 2+ дня назад (UTC).
    Кнопка «ТРИАЛ»; +7 дней по нажатию (создание в панели или продление), field_bool_3.
    """
    if message.from_user.id not in ADMIN_IDS:
        return

    user_ids = await sql.SELECT_USER_IDS_NO_ACTIVE_PRO_SUBSCRIPTION()
    n = len(user_ids)
    if not user_ids:
        await message.answer(
            "Нет пользователей: is_delete=False, нет PRO-подписки "
            "(subscription_end_date пусто) или она закончилась 2+ дня назад (UTC)."
        )
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶️ Превью и подтверждение",
                    callback_data=_ADD7ALL_PREVIEW_CB,
                    style=STYLE_SUCCESS,
                )
            ]
        ]
    )
    await message.answer(
        f"К получателям рассылки: {n} чел.\n"
        f"(is_delete=False, нет PRO или subscription_end_date ≤ сегодня−2 дня UTC).\n\n"
        f"Дальше бот пришлёт вам превью текста с кнопкой «🔥Получить ТРИАЛ» и запрос подтверждения.\n"
        f"Начисление +7 дней — только по нажатию: нет в панели → создать на 7 дней, "
        f"есть, но PRO истёк → +7 дней от текущего момента.",
        reply_markup=kb,
    )


@router.callback_query(F.data == _ADD7ALL_PREVIEW_CB)
async def add_7_to_all_preview(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    await callback.answer()
    user_ids = await sql.SELECT_USER_IDS_NO_ACTIVE_PRO_SUBSCRIPTION()
    n = len(user_ids)
    if not user_ids:
        await callback.message.edit_text("Список пуст. Повторите /add_7_to_all.")
        return

    chat_id = callback.message.chat.id
    await callback.message.edit_text(
        "Ниже — превью рассылки и кнопка подтверждения отправки пользователям."
    )

    await bot.send_message(
        chat_id,
        _ADD7ALL_PROMO_TEXT,
        reply_markup=_ADD7ALL_TRIAL_KB,
    )

    confirm_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да",
                    callback_data=_ADD7ALL_YES_CB,
                    style=STYLE_SUCCESS,
                ),
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=_ADD7ALL_NO_CB,
                    style=STYLE_DANGER,
                ),
            ]
        ]
    )
    await bot.send_message(
        chat_id,
        f"Человек в рассылке — {n}. Подтвердите отправку.",
        reply_markup=confirm_kb,
    )


@router.callback_query(F.data == _ADD7ALL_NO_CB)
async def add_7_to_all_cancel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        "Отправка рассылки add_7_to_all отменена.",
        reply_markup=None,
    )


@router.callback_query(F.data == _ADD7ALL_YES_CB)
async def add_7_to_all_confirm(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    await callback.answer()
    user_ids = await sql.SELECT_USER_IDS_NO_ACTIVE_PRO_SUBSCRIPTION()
    if not user_ids:
        await callback.message.edit_text("Список пуст. Повторите /add_7_to_all.")
        return

    await callback.message.edit_text(
        f"⏳ Рассылка add_7_to_all: {len(user_ids)} получателей…"
    )

    admin_chat_id = callback.message.chat.id
    sent = 0
    failed = 0
    skipped_non_tg = 0

    for user_id in user_ids:
        if not is_telegram_chat_id(user_id):
            skipped_non_tg += 1
            await asyncio.sleep(0.1)
            continue
        try:
            await bot.send_message(
                user_id,
                _ADD7ALL_PROMO_TEXT,
                reply_markup=_ADD7ALL_TRIAL_KB,
            )
            sent += 1
            if sent % 1000 == 0:
                try:
                    await bot.send_message(
                        admin_chat_id,
                        f"add_7_to_all: отправлено сообщений — {sent}",
                    )
                except Exception as notify_err:
                    logger.warning(
                        "add_7_to_all: не удалось отправить прогресс админу: %s",
                        notify_err,
                    )
        except Exception as e:
            failed += 1
            logger.warning("add_7_to_all: не отправлено user_id=%s: %s", user_id, e)

        await asyncio.sleep(0.1)

    await callback.message.answer(
        "Готово (add_7_to_all).\n"
        f"• Отправлено: {sent}\n"
        f"• Ошибок: {failed}\n"
        f"• Пропущено (не Telegram chat_id): {skipped_non_tg}"
    )


@router.message(Command(commands=['send_push']))
async def send_push_command(message: Message):
    if CHECKER_ID is None or message.from_user.id != CHECKER_ID:
        return

    await message.answer("🔄 Начинаю отправку push-уведомления...")

    # Текущая дата
    now = datetime.now()

    # Получаем всех пользователей
    all_users = await sql.get_all_users()

    # Фильтруем
    candidates = [CHECKER_ID]
    for user in all_users:
        if user.is_delete:
            continue
        if not user.in_panel:
            continue
        if not user.subscription_end_date or user.subscription_end_date < now:
            continue
        candidates.append(user.user_id)

    if not candidates:
        await message.answer("❌ Нет пользователей, удовлетворяющих условиям.")
        return
    else:
        await message.answer(f"Всего {len(candidates)} пользователей, удовлетворяющих условиям.")

    push_text = '''
⚠️ Технические работы завершены

Дорогие пользователи! Мы столкнулись с мощной DDoS-атакой, из-за чего страница личного кабинета <b>могла быть</b> временно не доступна у некоторых пользователей.
Хорошие новости: <b>мы всё починили!</b> Работаем в штатном режиме. 💪

🤔 Всё ещё не в сети?
Если вы никак не могли разобраться с импортом конфигов — не беда. Мы записали для вас <b>видео</b>, которое решит все вопросы. Смотрите и повторяйте.

🌐 Осталось только нажать кнопку '🔗 Подключить VPN' — и вы снова в безопасном интернете.
    '''

    success_count = 0
    fail_count = 0
    skipped_non_tg = 0

    for user_id in candidates:
        if not is_telegram_chat_id(user_id):
            skipped_non_tg += 1
            continue
        try:
            await bot.send_message(user_id,
                                   push_text,
                                   reply_markup=create_kb(1,
                                                          video_faq='🎥 Видеоинструкция',
                                                          connect_vpn='🔗 Подключить VPN'))
            success_count += 1
            logger.info(f"Push отправлен пользователю {user_id}")
            await asyncio.sleep(0.05)
        except Exception as e:
            fail_count += 1
            logger.error(f"Ошибка отправки для {user_id}: {e}")

    await message.answer(
        f"✅ Рассылка завершена.\n"
        f"👥 В списке: {len(candidates)}\n"
        f"⏭ Пропущено (не Telegram user id): {skipped_non_tg}\n"
        f"✅ Успешно: {success_count}\n"
        f"❌ Ошибок: {fail_count}"
    )

