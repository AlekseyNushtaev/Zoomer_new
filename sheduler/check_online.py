from datetime import datetime
from bot import x3, sql
from logging_config import logger

async def check_online_daily():
    """Собирает статистику онлайн-активности и сохраняет в таблицу online"""
    try:
        logger.info("📊 Запуск сбора статистики online")

        # 1. Получаем всех пользователей из панели
        await x3.test_connect()
        users_x3 = await x3.get_all_users()
        users_panel = len(users_x3)

        # 2. Фильтруем тех, кто был онлайн сегодня
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
        users_active = len(active_telegram_ids)

        # 3. Классифицируем на платных и триальных
        users_pay = 0
        users_trial = 0
        for tg_id in active_telegram_ids:
            user_data = await sql.get_user(tg_id)
            if user_data:
                if user_data[8]:
                    users_pay += 1
                else:
                    users_trial += 1

        # 4. Люди с хотя бы одной активной подпиской в БД
        users_subscribed = await sql.count_users_with_active_subscription()

        # 5. Запись в БД
        await sql.add_online_stats(
            users_panel, users_active, users_pay, users_trial, users_subscribed
        )

        logger.info(
            f"✅ Статистика online записана: "
            f"panel={users_panel}, active={users_active}, "
            f"pay={users_pay}, trial={users_trial}, subscribed={users_subscribed}"
        )

    except Exception as e:
        logger.error(f"❌ Ошибка в check_online: {e}")

