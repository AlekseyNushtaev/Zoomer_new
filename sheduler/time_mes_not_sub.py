from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from bot import bot, sql
from config import CHECKER_ID
from keyboard import create_kb, STYLE_PRIMARY, STYLE_SUCCESS
from telegram_ids import is_telegram_chat_id
from lexicon import lexicon
from logging_config import logger

VIDEO_FILE_ID = 'BAACAgIAAxkBAAEBk_5pmqIm8a5-5ioQ3GziIJ4dBH9PugAC_ZgAAtS92EjbvWnuAla0dDoE'


@dataclass(frozen=True)
class PushStage:
    window_start: int
    window_end: int
    lexicon_key: str
    with_video: bool = False
    keyboard: str = 'free_only'


NOT_SUB_STAGES = (
    PushStage(30, 60, 'push_not_subscribed_30m', keyboard='free_only'),
    PushStage(180, 210, 'push_not_subscribed_3h', with_video=True, keyboard='free_green'),
    PushStage(1410, 1440, 'push_not_subscribed_day2_0h', keyboard='buy_free'),
    PushStage(2130, 2160, 'push_not_subscribed_day2_12h', keyboard='buy_free'),
    PushStage(2850, 2880, 'push_not_subscribed_day3_0h', keyboard='buy_free'),
    PushStage(4290, 4320, 'push_not_subscribed_day4_0h', keyboard='buy_free'),
    PushStage(5730, 5760, 'push_not_subscribed_day5_0h', keyboard='buy_free_secret'),
    PushStage(7170, 7200, 'push_not_subscribed_day6_0h', keyboard='buy_free'),
    PushStage(8610, 8640, 'push_not_subscribed_day7_0h', keyboard='buy_free'),
)

# Как в partner_bot: день 1 — все 3; далее по одному у границ 48/72/96/120/144/168ч
NOT_CONNECT_STAGES = (
    # День 1
    PushStage(30, 60, 'push_not_connected_30m', keyboard='connect_only'),
    PushStage(180, 210, 'push_not_connected_3h', with_video=True, keyboard='connect_only'),
    PushStage(1410, 1440, 'push_not_connected_24h', keyboard='connect_only'),
    # День 2 (48ч) — 1-е, день 3 (72ч) — 2-е, день 4 (96ч) — 3-е
    PushStage(2850, 2880, 'push_not_connected_30m', keyboard='connect_only'),
    PushStage(4290, 4320, 'push_not_connected_3h', with_video=True, keyboard='connect_only'),
    PushStage(5730, 5760, 'push_not_connected_24h', keyboard='connect_only'),
    # День 5 (120ч) — 1-е, день 6 (144ч) — 2-е, день 7 (168ч) — 3-е
    PushStage(7170, 7200, 'push_not_connected_30m', keyboard='connect_only'),
    PushStage(8610, 8640, 'push_not_connected_3h', with_video=True, keyboard='connect_only'),
    PushStage(10050, 10080, 'push_not_connected_24h', keyboard='connect_only'),
)


def _find_stage(offset_minutes: int, stages: tuple[PushStage, ...]) -> Optional[PushStage]:
    for stage in stages:
        if stage.window_start <= offset_minutes <= stage.window_end:
            return stage
    return None


def _keyboard_for(stage: PushStage):
    if stage.keyboard == 'free_only':
        return create_kb(1, free_vpn='🔥 Попробовать бесплатно')
    if stage.keyboard == 'free_green':
        return create_kb(
            1,
            free_vpn='🔥 Попробовать бесплатно',
        )
    if stage.keyboard == 'buy_free':
        return create_kb(
            1,
            styles={'buy_vpn': STYLE_PRIMARY},
            buy_vpn='💰 Купить подписку',
            free_vpn='🔥 Попробовать бесплатно',
        )
    if stage.keyboard == 'buy_free_secret':
        return create_kb(
            1,
            styles={'buy_vpn': STYLE_PRIMARY},
            buy_vpn='💰 Купить подписку',
            free_vpn='🔥 Попробовать бесплатно',
            r_30secret='💰 Секретный тариф',
        )
    if stage.keyboard == 'connect_only':
        return create_kb(
            1,
            styles={'connect_vpn': STYLE_PRIMARY},
            connect_vpn='🔗 Подключить VPN',
        )
    return None


async def _send_push(user_id: int, stage: PushStage) -> None:
    message_text = lexicon[stage.lexicon_key]
    keyboard = _keyboard_for(stage)
    if stage.with_video:
        await bot.send_video(
            chat_id=user_id,
            video=VIDEO_FILE_ID,
            caption=message_text,
            reply_markup=keyboard,
        )
    else:
        await bot.send_message(
            chat_id=user_id,
            text=message_text,
            reply_markup=keyboard,
        )


async def send_push_cron(debug: bool = False):
    """
    Push по этапам после регистрации (без циклов):
    - без подписки (in_panel=False) — только первые 7 дней;
    - с активной подпиской, но без VPN (is_connect=False) — день 1: 3 пуша,
      дни 2–7: по одному пушу, затем стоп.
    """
    try:
        all_users = await sql.SELECT_ALL_USERS()

        if not all_users:
            logger.info("Нет пользователей для отправки push-уведомлений")
            return

        sent_count_not_sub = 0
        failed_count_not_sub = 0
        sent_count_not_connect = 0
        failed_count_not_connect = 0
        failed_count = 0
        now = datetime.now()

        for user_id in all_users:
            if not is_telegram_chat_id(user_id):
                continue
            try:
                user_data = await sql.get_user(user_id)
                if not user_data:
                    continue

                create_time = user_data[6]
                if not create_time:
                    continue

                minutes_diff = int((now - create_time).total_seconds() / 60)

                if not user_data[4]:  # in_panel: нет подписки в панели
                    stage = _find_stage(minutes_diff, NOT_SUB_STAGES)
                    if stage:
                        try:
                            await _send_push(user_id, stage)
                            sent_count_not_sub += 1
                            logger.info(
                                f"Отправлено push-уведомление (не в панели) пользователю {user_id}"
                            )
                        except Exception as e:
                            failed_count_not_sub += 1
                            logger.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")

                elif not user_data[5]:  # is_connect: VPN ещё не подключён
                    subscription_end_date = user_data[9]
                    if not subscription_end_date or subscription_end_date < now:
                        continue
                    stage = _find_stage(minutes_diff, NOT_CONNECT_STAGES)
                    if stage:
                        try:
                            await _send_push(user_id, stage)
                            sent_count_not_connect += 1
                            logger.info(
                                f"Отправлено push-уведомление (не подключен) пользователю {user_id}"
                            )
                        except Exception as e:
                            failed_count_not_connect += 1
                            logger.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
            except Exception as e:
                failed_count += 1
                logger.error(f"Ошибка обработки пользователя {user_id}: {e}")

        if CHECKER_ID is not None:
            try:
                await bot.send_message(
                    chat_id=CHECKER_ID,
                    text=f"📊 Отчет по push-уведомлениям:\n\n"
                         f"✅ Отправлено не подписанным: {sent_count_not_sub}\n"
                         f"❌ Не удалось отправить не подписанным: {failed_count_not_sub}\n\n"
                         f"✅ Отправлено не подключенным: {sent_count_not_connect}\n"
                         f"❌ Не удалось отправить не подключенным: {failed_count_not_connect}\n\n"
                         f"❌ Не удалось обработать: {failed_count}\n\n"
                         f"⏰ Время: {now.strftime('%H:%M:%S')}"
                )
                logger.info(
                    f"Отчет отправлен: отправлено {sent_count_not_connect + sent_count_not_sub}, "
                    f"не удалось {failed_count + failed_count_not_connect + failed_count_not_sub}"
                )
            except Exception as e:
                logger.error(f"Не удалось отправить отчет: {e}")

    except Exception as e:
        logger.error(f"Критическая ошибка в send_push_cron: {e}")
