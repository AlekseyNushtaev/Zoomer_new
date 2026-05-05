import random
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select

from bot import sql, x3, bot
from config import ADMIN_IDS, CHECKER_ID
from telegram_ids import is_telegram_chat_id
from config_bd.models import Users
from X3 import panel_username_for_site_user
from keyboard import create_kb, STYLE_SUCCESS, STYLE_PRIMARY, STYLE_DANGER
from logging_config import logger
import asyncio
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from sheduler.check_connect import check_connect

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


def _panel_sub_line(activ_result: dict) -> str:
    t = activ_result.get("time", "-")
    if t in (None, "", "-"):
        return "Нет"
    return str(t)


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
        "6ba41467-be68-438c-ad6e-5a02f7df826c",
        "c6973051-58b7-484c-b669-6a123cda465b",
        "a867561f-8736-4f67-8970-e20fddd00e5e",
        "29b73cd8-8a68-41cd-99c7-5d30dbac4c71",
        "d108d4a0-a121-4b52-baee-a97243208179",
    }
)

_NEW_BULK_SQUAD_CHOICES = (
    "7c21ebc7-5463-449c-8e9c-44c0677380ab",
    "bc27ae8e-a5c2-4278-9af2-461623d5dd0d",
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
        if tc.tzinfo is None:
            tc_aware = tc.replace(tzinfo=timezone.utc)
        else:
            tc_aware = tc.astimezone(timezone.utc)
        ts = tc_aware.astimezone(_MSK).strftime("%d-%m-%Y %H:%M МСК")
        pay_lines.append(f"• {ts} — {kind} — {days_s} дн.")

    body = (
        f"<b>/pay {target_id}</b>\n\n"
        f"Подписка обычная в БД бота — {_msk_dt_str(sub_db)}\n"
        f"Подписка обычная в панели — {_panel_sub_line(ar_reg)}\n"
        f"Подписка вайт в БД бота — {_msk_dt_str(white_db)}\n"
        f"Подписка вайт в панели — {_panel_sub_line(ar_white)}\n\n"
        f"<b>Платежи:</b>\n"
    )
    if pay_lines:
        body += "\n".join(pay_lines)
    else:
        body += "Нет"

    for chunk in _split_long_text(body):
        await message.answer(chunk)


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

        # Сообщаем результат
        await message.answer(
            f"✅ Дата подписки успешно установлена!\n\n"
            f"👤 Пользователь: {user_id}\n"
            f"📅 Целевая дата (UTC): {target_date.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📅 Установленная в панели дата (UTC): {actual_date.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📝 Тип: {'white' if is_white else 'обычная'}\n"
            f"💾 База данных обновлена."
        )
        if not is_telegram_chat_id(user_id):
            await message.answer(
                "ℹ️ Уведомление в Telegram пользователю не отправлялось: "
                "для этого user_id нет личного чата (например, аккаунт только с сайта)."
            )
        else:
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=f"✅ Вам обновлена дата подписки!\n\n"
                    f"📅 Новая дата окончания подписки: {actual_date.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"📝 Тариф: {'🦾 Включи мобильный интернет' if is_white else '💫 подписка на VPN'}\n",
                    reply_markup=create_kb(1, back_to_main='🔙 Назад')
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке сообщения пользователю {user_id}: {e}")
                await message.answer(
                    f"❌ Произошла ошибка при отправке сообщения пользователю {user_id}: {str(e)}"
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
    squad_1 = ['6ba41467-be68-438c-ad6e-5a02f7df826c']
    squad_2 = ['c6973051-58b7-484c-b669-6a123cda465b']
    squad_3 = ['a867561f-8736-4f67-8970-e20fddd00e5e']
    squad_4 = ['29b73cd8-8a68-41cd-99c7-5d30dbac4c71']
    squad_5 = ['d108d4a0-a121-4b52-baee-a97243208179']
    success_count = 0
    fail_count = 0
    if message.from_user.id not in ADMIN_IDS:
        return
    users_x3 = await x3.get_all_users()
    for user in users_x3:
        try:
            await asyncio.sleep(0.3)
            random_squad = random.choice([squad_1, squad_2, squad_3, squad_4, squad_5])
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
    """5 сквадов → 3 чанка → POST bulk/update-squads по порядку; сквад на каждый HTTP — random из двух."""
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        users = await x3.get_all_panel()
        total = len(users)
        if not users:
            empty_report = (
                "/new: get_all_panel пуст\n"
                "С 5 сквадами: 0\n"
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

        # Шаг 1: из всей панели только те, у кого в activeInternalSquads есть один из 5 сквадов
        with_five_squads = [u for u in users if has_allowed_squad(u)]
        n_five = len(with_five_squads)

        # Шаг 2: разбиваем только этих пользователей на 3 чанка
        chunk1 = []
        chunk2 = []
        chunk3 = []
        for u in with_five_squads:
            if subscription_ok(u) and first_connected_at(u) is not None:
                chunk1.append(u)
            elif subscription_ok(u) and first_connected_at(u) is None:
                chunk2.append(u)
            else:
                chunk3.append(u)

        n1, n2, n3 = len(chunk1), len(chunk2), len(chunk3)
        report = (
            f"/new: в панели записей {total}\n"
            f"С одним из 5 сквадов (activeInternalSquads): {n_five}\n"
            f"Чанк 1 — подписка ≥ сегодня UTC, firstConnectedAt не None: {n1}\n"
            f"Чанк 2 — подписка ≥ сегодня UTC, firstConnectedAt None: {n2}\n"
            f"Чанк 3 — остальные из этих {n_five}: {n3}"
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


# @router.message(Command(commands=['add_7_to_connected']))
# async def add_7_to_connected_command(message: Message):
#     """
#     Пользователи с активной white-подпиской (календарная дата окончания ≥ сегодня по UTC): +7 дней в панель и БД,
#     затем по кнопке — уведомление в ЛС (только после успешного обновления панели).
#     """
#     if message.from_user.id not in ADMIN_IDS:
#         return

#     user_ids = await sql.SELECT_USER_IDS_ACTIVE_WHITE_SUBSCRIPTION()
#     n = len(user_ids)
#     if not user_ids:
#         await message.answer(
#             "Нет пользователей с активной white-подпиской "
#             "(дата окончания по UTC ≥ сегодняшнего дня UTC)."
#         )
#         return

#     kb = InlineKeyboardMarkup(
#         inline_keyboard=[
#             [
#                 InlineKeyboardButton(
#                     text="▶️ Начать отправку",
#                     callback_data=_ADD7WHITE_CB,
#                     style=STYLE_SUCCESS,
#                 )
#             ]
#         ]
#     )
#     await message.answer(
#         f"Будет обработано пользователей: {n}\n"
#         f"(дата окончания white в БД по UTC ≥ сегодняшнего дня UTC).\n\n"
#         f"Для каждого: +7 дней в панели и БД, затем сообщение в Telegram "
#         f"(если обновление панели прошло успешно).",
#         reply_markup=kb,
#     )


# @router.callback_query(F.data == _ADD7WHITE_CB)
# async def add_7_to_connected_confirm(callback: CallbackQuery):
#     if callback.from_user.id not in ADMIN_IDS:
#         await callback.answer("Нет доступа.", show_alert=True)
#         return

#     await callback.answer()
#     user_ids = await sql.SELECT_USER_IDS_ACTIVE_WHITE_SUBSCRIPTION()
#     if not user_ids:
#         await callback.message.edit_text(
#             "Список пуст (условия уже не выполняются). Повторите /add_7_to_connected."
#         )
#         return

#     await callback.message.edit_text(
#         f"⏳ Обработка {len(user_ids)} пользователей…"
#     )

#     panel_ok = 0
#     panel_fail = 0
#     msg_ok = 0
#     msg_fail = 0
#     skipped_chat = 0

#     for user_id in user_ids:
#         username_panel = f"{user_id}_white"
#         ok = await x3.updateClient(7, username_panel, user_id)
#         if not ok:
#             panel_fail += 1
#             logger.warning(
#                 "add_7_to_connected: панель не обновлена user_id=%s", user_id
#             )
#         else:
#             panel_ok += 1
#             if is_telegram_chat_id(user_id):
#                 try:
#                     await bot.send_message(user_id, _ADD7WHITE_USER_TEXT)
#                     msg_ok += 1
#                 except Exception as e:
#                     msg_fail += 1
#                     logger.warning(
#                         "add_7_to_connected: не отправлено user_id=%s: %s",
#                         user_id,
#                         e,
#                     )
#             else:
#                 skipped_chat += 1

#         await asyncio.sleep(0.1)

#     await callback.message.answer(
#         "Готово.\n"
#         f"• Панель + БД: успешно {panel_ok}, ошибок {panel_fail}\n"
#         f"• Сообщения в TG: отправлено {msg_ok}, ошибок {msg_fail}\n"
#         f"• Пропущено (не Telegram chat_id): {skipped_chat}"
#     )


# @router.message(Command(commands=['add_7_subscribed']))
# async def add_7_subscribed_command(message: Message):
#     """
#     Пользователи с активной обычной подпиской (календарная дата окончания ≥ сегодня по UTC): +7 дней в панель и БД,
#     затем по кнопке — уведомление в ЛС (только после успешного обновления панели).
#     """
#     if message.from_user.id not in ADMIN_IDS:
#         return

#     user_ids = await sql.SELECT_USER_IDS_ACTIVE_SUBSCRIPTION()
#     n = len(user_ids)
#     if not user_ids:
#         await message.answer(
#             "Нет пользователей с активной обычной подпиской "
#             "(дата окончания по UTC ≥ сегодняшнего дня UTC)."
#         )
#         return

#     kb = InlineKeyboardMarkup(
#         inline_keyboard=[
#             [
#                 InlineKeyboardButton(
#                     text="▶️ Начать отправку",
#                     callback_data=_ADD7REG_CB,
#                     style=STYLE_SUCCESS,
#                 )
#             ]
#         ]
#     )
#     await message.answer(
#         f"Будет обработано пользователей: {n}\n"
#         f"(дата окончания обычной подписки в БД по UTC ≥ сегодняшнего дня UTC).\n\n"
#         f"Для каждого: +7 дней в панели и БД, затем сообщение в Telegram "
#         f"(если обновление панели прошло успешно).",
#         reply_markup=kb,
#     )


# @router.callback_query(F.data == _ADD7REG_CB)
# async def add_7_subscribed_confirm(callback: CallbackQuery):
#     if callback.from_user.id not in ADMIN_IDS:
#         await callback.answer("Нет доступа.", show_alert=True)
#         return

#     await callback.answer()
#     user_ids = await sql.SELECT_USER_IDS_ACTIVE_SUBSCRIPTION()
#     if not user_ids:
#         await callback.message.edit_text(
#             "Список пуст (условия уже не выполняются). Повторите /add_7_subscribed."
#         )
#         return

#     await callback.message.edit_text(
#         f"⏳ Обработка {len(user_ids)} пользователей (обычная подписка)…"
#     )

#     admin_chat_id = callback.message.chat.id
#     panel_ok = 0
#     panel_fail = 0
#     msg_ok = 0
#     msg_fail = 0
#     skipped_chat = 0
#     processed = 0

#     for user_id in user_ids:
#         # Только обычная подписка: список из subscription_end_date (не white),
#         # в панели username = telegram id без суффикса _white → updateClient пишет subscription_end_date.
#         username_panel = str(user_id)
#         ok = await x3.updateClient(7, username_panel, user_id)
#         if not ok:
#             panel_fail += 1
#             logger.warning(
#                 "add_7_subscribed: панель не обновлена user_id=%s", user_id
#             )
#         else:
#             panel_ok += 1
#             if is_telegram_chat_id(user_id):
#                 try:
#                     await bot.send_message(user_id, _ADD7REG_USER_TEXT)
#                     msg_ok += 1
#                 except Exception as e:
#                     msg_fail += 1
#                     logger.warning(
#                         "add_7_subscribed: не отправлено user_id=%s: %s",
#                         user_id,
#                         e,
#                     )
#             else:
#                 skipped_chat += 1

#         processed += 1
#         if processed % 1000 == 0:
#             try:
#                 await bot.send_message(
#                     admin_chat_id,
#                     f"add_7_subscribed: обработано пользователей — {processed}",
#                 )
#             except Exception as notify_err:
#                 logger.warning(
#                     "add_7_subscribed: не удалось отправить прогресс админу: %s",
#                     notify_err,
#                 )

#         await asyncio.sleep(0.1)

#     await callback.message.answer(
#         "Готово (обычная подписка).\n"
#         f"• Панель + БД: успешно {panel_ok}, ошибок {panel_fail}\n"
#         f"• Сообщения в TG: отправлено {msg_ok}, ошибок {msg_fail}\n"
#         f"• Пропущено (не Telegram chat_id): {skipped_chat}"
#     )


# @router.message(Command(commands=['add_7_to_all']))
# async def add_7_to_all_command(message: Message):
#     """
#     Рассылка: в панели, обычная подписка истекла (дата UTC), is_delete=False.
#     Кнопка «ТРИАЛ»; +7 дней по нажатию, флаг field_bool_3 (handlers_user).
#     """
#     if message.from_user.id not in ADMIN_IDS:
#         return

#     user_ids = await sql.SELECT_USER_IDS_PANEL_EXPIRED_REGULAR_SUBSCRIPTION()
#     n = len(user_ids)
#     if not user_ids:
#         await message.answer(
#             "Нет пользователей: in_panel, не удалены, обычная подписка истекла по дате UTC "
#             "(или subscription_end_date пусто)."
#         )
#         return

#     kb = InlineKeyboardMarkup(
#         inline_keyboard=[
#             [
#                 InlineKeyboardButton(
#                     text="▶️ Превью и подтверждение",
#                     callback_data=_ADD7ALL_PREVIEW_CB,
#                     style=STYLE_SUCCESS,
#                 )
#             ]
#         ]
#     )
#     await message.answer(
#         f"К получателям рассылки: {n} чел.\n"
#         f"(в панели, is_delete=False, подписка не активна по календарной дате UTC).\n\n"
#         f"Дальше бот пришлёт вам превью текста с кнопкой «🔥Получить ТРИАЛ» и запрос подтверждения.\n"
#         f"Начисление +7 дней у пользователей — только по нажатию (field_bool_3).",
#         reply_markup=kb,
#     )


# @router.callback_query(F.data == _ADD7ALL_PREVIEW_CB)
# async def add_7_to_all_preview(callback: CallbackQuery):
#     if callback.from_user.id not in ADMIN_IDS:
#         await callback.answer("Нет доступа.", show_alert=True)
#         return

#     await callback.answer()
#     user_ids = await sql.SELECT_USER_IDS_PANEL_EXPIRED_REGULAR_SUBSCRIPTION()
#     n = len(user_ids)
#     if not user_ids:
#         await callback.message.edit_text("Список пуст. Повторите /add_7_to_all.")
#         return

#     chat_id = callback.message.chat.id
#     await callback.message.edit_text(
#         "Ниже — превью рассылки и кнопка подтверждения отправки пользователям."
#     )

#     await bot.send_message(
#         chat_id,
#         _ADD7ALL_PROMO_TEXT,
#         reply_markup=_ADD7ALL_TRIAL_KB,
#     )

#     confirm_kb = InlineKeyboardMarkup(
#         inline_keyboard=[
#             [
#                 InlineKeyboardButton(
#                     text="Да",
#                     callback_data=_ADD7ALL_YES_CB,
#                     style=STYLE_SUCCESS,
#                 ),
#                 InlineKeyboardButton(
#                     text="Нет",
#                     callback_data=_ADD7ALL_NO_CB,
#                     style=STYLE_DANGER,
#                 ),
#             ]
#         ]
#     )
#     await bot.send_message(
#         chat_id,
#         f"Человек в рассылке — {n}. Подтвердите отправку.",
#         reply_markup=confirm_kb,
#     )


# @router.callback_query(F.data == _ADD7ALL_NO_CB)
# async def add_7_to_all_cancel(callback: CallbackQuery):
#     if callback.from_user.id not in ADMIN_IDS:
#         await callback.answer("Нет доступа.", show_alert=True)
#         return
#     await callback.answer()
#     await callback.message.edit_text(
#         "Отправка рассылки add_7_to_all отменена.",
#         reply_markup=None,
#     )


# @router.callback_query(F.data == _ADD7ALL_YES_CB)
# async def add_7_to_all_confirm(callback: CallbackQuery):
#     if callback.from_user.id not in ADMIN_IDS:
#         await callback.answer("Нет доступа.", show_alert=True)
#         return

#     await callback.answer()
#     user_ids = await sql.SELECT_USER_IDS_PANEL_EXPIRED_REGULAR_SUBSCRIPTION()
#     if not user_ids:
#         await callback.message.edit_text("Список пуст. Повторите /add_7_to_all.")
#         return

#     await callback.message.edit_text(
#         f"⏳ Рассылка add_7_to_all: {len(user_ids)} получателей…"
#     )

#     admin_chat_id = callback.message.chat.id
#     sent = 0
#     failed = 0
#     skipped_non_tg = 0

#     for user_id in user_ids[10000:]:
#         if not is_telegram_chat_id(user_id):
#             skipped_non_tg += 1
#             await asyncio.sleep(0.1)
#             continue
#         try:
#             await bot.send_message(
#                 user_id,
#                 _ADD7ALL_PROMO_TEXT,
#                 reply_markup=_ADD7ALL_TRIAL_KB,
#             )
#             sent += 1
#             if sent % 1000 == 0:
#                 try:
#                     await bot.send_message(
#                         admin_chat_id,
#                         f"add_7_to_all: отправлено сообщений — {sent}",
#                     )
#                 except Exception as notify_err:
#                     logger.warning(
#                         "add_7_to_all: не удалось отправить прогресс админу: %s",
#                         notify_err,
#                     )
#         except Exception as e:
#             failed += 1
#             logger.warning("add_7_to_all: не отправлено user_id=%s: %s", user_id, e)

#         await asyncio.sleep(0.1)

#     await callback.message.answer(
#         "Готово (add_7_to_all).\n"
#         f"• Отправлено: {sent}\n"
#         f"• Ошибок: {failed}\n"
#         f"• Пропущено (не Telegram chat_id): {skipped_non_tg}"
#     )


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

