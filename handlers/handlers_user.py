import urllib.parse
from datetime import datetime, timezone

from bot import sql, x3, bot
from config import CHANEL_ID, PUBLIC_SITE_URL, PARTNER_PROCENT, PARTNER_MIN, SUPPORT_URL, BOT_URL
from lead_tracker import post_user_registered, post_user_trial, tracker_source_from_ref_and_stamp
from keyboard import (keyboard_start, keyboard_start_bonus, keyboard_tariff_bonus, keyboard_tariff,
                      keyboard_subscription, keyboard_sub_after_free, ref_keyboard, keyboard_gift_tariff,
                      keyboard_payment_method, keyboard_payment_method_stock, chanel_keyboard, create_kb,
                      keyboard_inline_ref, keyboard_partner_intro, keyboard_partner_dashboard,
                      keyboard_partner_withdraw, STYLE_PRIMARY, OPEN_SITE_CB, SITE_URL)
from web_api import create_bot_site_login_token
from logging_config import logger
import asyncio
import re
from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    ChatMemberUpdated,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.filters import BaseFilter, ChatMemberUpdatedFilter, KICKED, MEMBER, Command
from lexicon import lexicon


router: Router = Router()

_TRIAL_RETURN_GET_CB = "trial_return_get"
# индексы в кортеже get_user (_user_tuple)
_USER_TUPLE_SUBSCRIPTION_END_DATE = 9
_USER_TUPLE_FIELD_BOOL_3 = 26


def _user_has_active_pro_subscription(user_data: tuple) -> bool:
    sub_end = user_data[_USER_TUPLE_SUBSCRIPTION_END_DATE]
    if sub_end is None:
        return False
    if sub_end.tzinfo is None:
        aware = sub_end.replace(tzinfo=timezone.utc)
    else:
        aware = sub_end.astimezone(timezone.utc)
    return aware.date() >= datetime.now(timezone.utc).date()


async def _panel_regular_subscription_is_active(uid: int) -> bool:
    existing = await x3.get_user_by_username(str(uid))
    if not existing or not existing.get("response"):
        return False
    user = existing["response"]
    if isinstance(user, list):
        user = user[0]
    expire_at_str = user.get("expireAt")
    if not expire_at_str:
        return False
    expire_at = datetime.fromisoformat(expire_at_str.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    return user.get("status") == "ACTIVE" and expire_at > now

_SECRET_TARIFF_PAYMENT_TEXT = (
    "Секретный тариф - 💫 подписка на VPN PRO\n"
    "4 сервера из разных стран на выбор.\n"
    "5 устройств, безлимитный трафик.\n\n"
    "СКИДКА 40% - 149 руб за месяц\n\n"
    "Выберите способ оплаты:"
)

_LINKING_CODE_TEXT = re.compile(r"^[A-Za-z0-9]{8}$")


class LinkingCodeMessageFilter(BaseFilter):
    """Ровно 8 латинских букв/цифр (код привязки с сайта)."""

    async def __call__(self, message: Message) -> bool:
        t = (message.text or "").strip()
        if t.startswith("/"):
            return False
        return bool(_LINKING_CODE_TEXT.fullmatch(t))


# Этот хэндлер срабатывает на команду /start
@router.message(Command(commands="start"))
async def process_start_command(message: Message, command: Command):

    user_data = await sql.get_user(message.from_user.id)
    in_panel = False
    in_chanel = False
    ref_login = ''
    partner_login = ''
    existing = False
    stamp = ''
    ttclid = None

    if user_data:
        in_panel = user_data[4]
        in_chanel = user_data[7]
        existing = True

    if len(message.text.split(' ')) == 1:
        if user_data:
            logger.info(f'Юзер {message.from_user.id} - {message.from_user.username} нажал старт повторно')
        else:
            logger.success(f'Юзер {message.from_user.id} - {message.from_user.username} зашел в бота в первый раз')

    else:
        start_arg = message.text.split(' ', 1)[1]

        if start_arg.startswith('partner_'):
            if user_data:
                logger.info(
                    f'Юзер {message.from_user.id} - {message.from_user.username} '
                    f'нажал старт повторно с партнёрской ссылкой'
                )
            else:
                logger.success(
                    f'Юзер {message.from_user.id} - {message.from_user.username} '
                    f'зашел в бота в первый раз по партнёрской ссылке'
                )
                raw_partner = start_arg.replace('partner_', '', 1)
                if raw_partner.isdigit() and raw_partner != str(message.from_user.id):
                    partner_login = raw_partner

        elif start_arg.startswith('ref'):
            if user_data:
                logger.info(f'Юзер {message.from_user.id} - {message.from_user.username} нажал старт повторно с реферальной ссылкой')
            else:
                logger.success(
                    f'Юзер {message.from_user.id} - {message.from_user.username} зашел в бота в первый раз по реферальной ссылкой')
                ref_login = start_arg.replace('ref', '', 1)

        elif start_arg.startswith('gift_'):
            logger.info(
                f'Юзер {message.from_user.id} - {message.from_user.username} пытается активировать подарочную подписку')
            gift_id = start_arg.replace('gift_', '', 1)
            in_panel = await activate_gift(message, gift_id)
            await asyncio.sleep(2)
            existing = True

        elif start_arg.startswith('auth_'):
            # Website deeplink auth
            auth_token = start_arg.replace('auth_', '', 1)
            from web_api import confirm_tg_auth_token
            ok = confirm_tg_auth_token(
                auth_token,
                message.from_user.id,
                first_name=message.from_user.first_name or "",
                username=message.from_user.username,
            )
            if ok:
                logger.info(f'Юзер {message.from_user.id} авторизован на сайте через deeplink')
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

                dashboard_url = f"{PUBLIC_SITE_URL}/dashboard" if PUBLIC_SITE_URL else ""
                if dashboard_url:
                    kb = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="🌐 Перейти в личный кабинет",
                                    url=dashboard_url,
                                )
                            ]
                        ]
                    )
                    await message.answer("✅ Вы авторизованы на сайте!", reply_markup=kb)
                else:
                    await message.answer("✅ Вы авторизованы на сайте! Вернитесь во вкладку с сайтом.")
            else:
                await message.answer("❌ Ссылка устарела. Попробуйте ещё раз на сайте.")
            if not user_data:
                inserted = await sql.add_user(message.from_user.id, False, False)
                if inserted:
                    await post_user_registered(
                        message.from_user.id,
                        message.from_user.username,
                        message.from_user.full_name,
                        None,
                    )
            return

        else:
            if user_data:
                logger.info(f'Юзер {message.from_user.id} - {message.from_user.username} нажал старт повторно с меткой')
            else:
                logger.success(
                    f'Юзер {message.from_user.id} - {message.from_user.username} зашел в бота в первый раз по метке')
                stamp = start_arg

    if not existing:
        inserted = await sql.add_user(
            message.from_user.id,
            False,
            False,
            ref=ref_login,
            stamp=stamp,
            partner=partner_login,
        )
        logger.info(f'Юзер {message.from_user.id} - {message.from_user.username} добавлен в БД')
        if inserted:
            src = tracker_source_from_ref_and_stamp(ref_login, stamp, partner_login)
            await post_user_registered(
                message.from_user.id,
                message.from_user.username,
                message.from_user.full_name,
                src,
            )
        if ttclid:
            await sql.update_ttclid(message.from_user.id, ttclid)
            logger.info(f'Юзеру {message.from_user.id} - {message.from_user.username} присвоен ttclid')

    if not in_panel:
        await message.answer(text=lexicon['start_bonus'],
                             reply_markup=keyboard_start_bonus(message.from_user.id),
                             disable_web_page_preview=True)
    else:
        await message.answer(text=lexicon['start'],
                             reply_markup=keyboard_start(message.from_user.id),
                             disable_web_page_preview=True)


def _site_base_url() -> str:
    return (PUBLIC_SITE_URL or SITE_URL).rstrip("/")


def _site_login_url(telegram_user_id: int, first_name: str, username: str | None) -> str:
    token = create_bot_site_login_token(
        telegram_user_id=telegram_user_id,
        first_name=first_name,
        username=username,
    )
    return f"{_site_base_url()}/auth/bot?token={urllib.parse.quote(token, safe='')}"


@router.callback_query(F.data == OPEN_SITE_CB)
async def open_site_callback(callback: CallbackQuery):
    """Ссылка на сайт с одноразовым токеном для авто-входа."""
    await callback.answer()
    u = callback.from_user
    login_url = _site_login_url(
        u.id,
        u.first_name or "",
        u.username,
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🌐 Открыть сайт",
                    url=login_url,
                    style=STYLE_PRIMARY,
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Назад",
                    callback_data="back_to_main",
                )
            ],
        ]
    )
    await callback.message.answer(
        "🌐 Нажмите кнопку ниже — откроется сайт, вход выполнится автоматически.\n"
        "Ссылка действует 10 минут и только один раз.",
        reply_markup=kb,
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == 'buy_vpn')
async def buy_vpn_cb(callback: CallbackQuery):
    await callback.answer()
    user_data = await sql.get_user(callback.from_user.id)
    in_panel = False

    if user_data is not None and len(user_data) > 4:
        in_panel = user_data[4]

    result_active = await x3.activ(str(callback.from_user.id))

    if result_active['activ'] == '🔎 - Не подключён' and not in_panel:
        await callback.message.answer(text=lexicon['buy'],
                                      reply_markup=keyboard_tariff_bonus(),
                                      disable_web_page_preview=True)
    else:
        await callback.message.answer(text=lexicon['buy'],
                                      reply_markup=keyboard_tariff(),
                                      disable_web_page_preview=True)


@router.callback_query(F.data == 'connect_vpn')
async def direct_connect_vpn_cb(callback: CallbackQuery):
    # await x3.test_connect()
    user_id = str(callback.from_user.id)
    sub_url = await x3.sublink(user_id)
    sub_url_white = None
    user_data = await sql.get_user(callback.from_user.id)
    if user_data[10]:
        user_id_white = user_id + '_white'
        sub_url_white = await x3.sublink(user_id_white)

    if not sub_url and not sub_url_white:
        await callback.message.answer(lexicon['no_sub'])
        return

    await callback.message.answer(
        text=lexicon['to_sub'],
        reply_markup=keyboard_subscription(sub_url, sub_url_white),
        disable_web_page_preview=True
    )
    await callback.answer()


@router.callback_query(F.data == _TRIAL_RETURN_GET_CB)
async def trial_return_get_cb(callback: CallbackQuery):
    uid = callback.from_user.id
    user_data = await sql.get_user(uid)
    if user_data is None:
        await sql.add_user(uid, False)
        user_data = await sql.get_user(uid)

    if user_data[_USER_TUPLE_FIELD_BOOL_3]:
        await callback.answer("Вы уже взяли свой триал!", show_alert=True)
        return

    await callback.answer()

    user_id_str = str(uid)
    panel_user = await x3.get_user_by_username(user_id_str)
    if panel_user and panel_user.get("response"):
        ok = await x3.updateClient(7, user_id_str, uid)
    else:
        ok = await x3.addClient(7, user_id_str, uid)

    if not ok:
        await callback.message.answer(
            "Не удалось начислить дни. Попробуйте позже или напишите в поддержку."
        )
        return

    await sql.update_in_panel(uid)
    await sql.update_field_bool_3(uid, True)
    await post_user_trial(uid)
    await callback.message.answer(
        "🎉 Поздравляем! Вы получили 7 триальных дней доступа к ВПН! ✨🔐",
        reply_markup=create_kb(
            1,
            styles={"connect_vpn": STYLE_PRIMARY},
            connect_vpn="🔗 Подключить VPN",
        ),
    )


@router.callback_query(F.data == "r_30secret")
async def secret_tariff_payment(callback: CallbackQuery):
    uid = callback.from_user.id
    user_data = await sql.get_user(uid)
    if user_data is None:
        await sql.add_user(uid, False)
        user_data = await sql.get_user(uid)
    if user_data is not None and user_data[_USER_TUPLE_FIELD_BOOL_3]:
        await callback.answer(
            "Вы уже воспользовались секретным тарифом!",
            show_alert=True,
        )
        return
    await callback.answer()
    await callback.message.answer(
        _SECRET_TARIFF_PAYMENT_TEXT,
        reply_markup=keyboard_payment_method("r_30secret"),
    )


@router.callback_query(F.data.in_({'r_7', 'r_30', 'r_90', 'r_180', 'r_5000', 'r_white_30'}))
async def process_payment_method(callback: CallbackQuery):
    await callback.answer()
    text = lexicon['payment_link']
    if 'white' in callback.data:
        await sql.add_white_counter_if_not_exists(callback.from_user.id)
        text = lexicon['payment_link_white']
    text += '\n\nВыберите способ оплаты:'
    tariff = callback.data
    await callback.message.answer(text, reply_markup=keyboard_payment_method(tariff))


@router.callback_query(F.data == 'free_vpn')
async def free_vpn_cb(callback: CallbackQuery):
    day = 1

    user_data = await sql.get_user(callback.from_user.id)
    in_panel = False
    if user_data is not None and len(user_data) > 4:
        in_panel = user_data[4]
    if in_panel:
        await callback.message.answer(text=lexicon['free_vpn_no'],
                                      reply_markup=keyboard_start(callback.from_user.id))
        return
    # Проверка на наличие данных
    # await x3.test_connect()
    logger.info(await x3.addClient(day, str(callback.from_user.id), int(callback.from_user.id)))
    result_active = await x3.activ(str(callback.from_user.id))
    time = result_active['time']

    # Проверка на наличие данных
    if await sql.get_user(callback.from_user.id) is not None:
        await sql.update_in_panel(callback.from_user.id)
    else:
        await sql.add_user(callback.from_user.id, True)
    user_id = str(callback.from_user.id)
    sub_url = await x3.sublink(user_id)

    await callback.message.answer(text=lexicon['buy_success'].format(time, sub_url),
                                  reply_markup=keyboard_sub_after_free(sub_url),
                                  disable_web_page_preview=True)
    await asyncio.sleep(1)
    await callback.message.answer(lexicon['to_chanel'], reply_markup=chanel_keyboard())
    await post_user_trial(callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == 'ref')
async def referral_program(callback: CallbackQuery):
    await callback.answer()
    count = await sql.select_ref_count(int(callback.from_user.id))
    await callback.message.answer(
        text=lexicon['ref_info'].format(count, callback.from_user.id),
        reply_markup=ref_keyboard(callback.from_user.id),
        disable_web_page_preview=True
    )


async def _ensure_user_exists(user_id: int) -> None:
    if await sql.get_user(user_id) is None:
        await sql.add_user(user_id, False, False)


async def _send_partner_dashboard(callback: CallbackQuery) -> None:
    tg_id = callback.from_user.id
    user = await sql.get_user_object_by_user_id(tg_id)
    if user is None:
        await _ensure_user_exists(tg_id)
        user = await sql.get_user_object_by_user_id(tg_id)

    referrals = await sql.select_partner_count(tg_id)
    payments_sum = await sql.select_partner_referrals_payments_sum(tg_id)
    balance = user.partner_balance or 0
    paid_out = user.partner_pay or 0
    total_earned = balance + paid_out
    link = f"{BOT_URL}?start=partner_{tg_id}"

    await callback.message.answer(
        text=lexicon['partner_dashboard'].format(
            link=link,
            procent=PARTNER_PROCENT,
            referrals=referrals,
            payments_sum=payments_sum,
            total_earned=total_earned,
            paid_out=paid_out,
            balance=balance,
        ),
        reply_markup=keyboard_partner_dashboard(),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == 'partner_earn')
async def partner_program(callback: CallbackQuery):
    await callback.answer()
    await _ensure_user_exists(callback.from_user.id)
    user = await sql.get_user_object_by_user_id(callback.from_user.id)

    if user and user.partner_flag:
        await _send_partner_dashboard(callback)
    else:
        await callback.message.answer(
            text=lexicon['partner_intro'].format(
                procent=PARTNER_PROCENT,
                min_sum=PARTNER_MIN,
            ),
            reply_markup=keyboard_partner_intro(),
        )


@router.callback_query(F.data == 'partner_create_link')
async def partner_create_link(callback: CallbackQuery):
    await callback.answer()
    await _ensure_user_exists(callback.from_user.id)
    await sql.update_partner_flag(callback.from_user.id, True)
    await _send_partner_dashboard(callback)


@router.callback_query(F.data == 'partner_withdraw')
async def partner_withdraw(callback: CallbackQuery):
    user = await sql.get_user_object_by_user_id(callback.from_user.id)
    if user is None:
        await callback.answer()
        return

    balance = user.partner_balance or 0
    if balance < PARTNER_MIN:
        await callback.answer(
            lexicon['partner_withdraw_alert'].format(min_sum=PARTNER_MIN),
            show_alert=True,
        )
        return

    await callback.answer()
    await callback.message.answer(
        text=lexicon['partner_withdraw_info'].format(
            balance=balance,
            min_sum=PARTNER_MIN,
        ),
        reply_markup=keyboard_partner_withdraw(SUPPORT_URL),
    )


@router.callback_query(F.data == 'buy_gift')
async def gift_subscription_start(callback: CallbackQuery):
    await callback.answer()
    """Начало процесса подарка подписки"""
    await callback.message.answer(
        lexicon['gift_start'],
        reply_markup=keyboard_gift_tariff()
    )


@router.callback_query(F.data.startswith('gift_'))
async def process_gift_payment_method(callback: CallbackQuery):
    await callback.answer()
    text = lexicon['payment_link']
    if 'white' in callback.data:
        await sql.add_white_counter_if_not_exists(callback.from_user.id)
        text = lexicon['payment_link_white']
    tariff = callback.data
    text += '\n\nВыберите способ оплаты <b>подарочной подписки</b>:'
    await callback.message.answer(text, reply_markup=keyboard_payment_method(tariff))


async def activate_gift(message: Message, gift_id: str):
    """Активация подарка по gift_id"""
    result = await sql.activate_gift(gift_id, message.from_user.id)

    if not result[0]:
        await message.answer(lexicon['gift_no'])
        logger.warning(f'Ссылка на подарок протухла')
        if await sql.get_user(message.from_user.id) is None:
            await sql.add_user(message.from_user.id, False)
            logger.success(
                f'Юзер {message.from_user.id} - {message.from_user.username} зашел в бота в первый раз по подарочной ссылке')
        return False

    duration = result[1]
    white_flag = result[2]

    # Активируем подписку для получателя
    # await x3.test_connect()
    user_id = message.from_user.id
    user_id_str = str(message.from_user.id)
    if white_flag:
        user_id_str += '_white'

    # Важно: `x3.addClient/updateClient` проставляет end_date в БД через UPDATE.
    # Для нового пользователя строка в `users` еще не создана, поэтому UPDATE ничего не меняет.
    # Сначала гарантируем наличие пользователя в БД.
    was_in_db = await sql.get_user(message.from_user.id) is not None
    if not was_in_db:
        await sql.add_user(message.from_user.id, False)


    # Проверяем существует ли пользователь
    existing_user = await x3.get_user_by_username(user_id_str)

    if existing_user and 'response' in existing_user and existing_user['response']:
        response = await x3.updateClient(duration, user_id_str, user_id)
    else:
        response = await x3.addClient(duration, user_id_str, user_id)

    if response:
        # Получаем информацию о подписке
        result_active = await x3.activ(user_id_str)
        subscription_time = result_active.get('time', '-')

        # Обновляем базу данных
        await sql.update_in_panel(message.from_user.id)
        if was_in_db:
            logger.info(
                f'Юзер {message.from_user.id} - {message.from_user.username} получил в подарок подписку, уже был в БД')
        else:
            logger.success(
                f'Юзер {message.from_user.id} - {message.from_user.username} зашел в бота в первый раз и получил подарочную подписку')

        # Отправляем сообщение получателю
        await message.answer(lexicon['gift_yes'].format(duration, subscription_time))
        return True

    else:
        await message.answer("❌ Ошибка при активации подарка. Обратитесь в поддержку.")
        if await sql.get_user(message.from_user.id) is None:
            await sql.add_user(message.from_user.id, False)
        return False


@router.callback_query(F.data == 'video_faq')
async def video_faq(callback: CallbackQuery):
    await callback.message.answer_video(video='BAACAgIAAxkBAAEBk_5pmqIm8a5-5ioQ3GziIJ4dBH9PugAC_ZgAAtS92EjbvWnuAla0dDoE',
                                        caption=lexicon['push_not_subscribed_3h'],
                                        reply_markup=create_kb(1, back_to_main='🔙 Назад'))


@router.callback_query(F.data == 'back_to_buy_menu')
async def handle_back_to_menu(callback: CallbackQuery):
    """Обработчик для возврата в главное меню из оплаты"""
    await callback.message.answer(text=lexicon['buy'], reply_markup=keyboard_tariff())


@router.callback_query(F.data == 'back_to_main')
async def handle_back_to_menu(callback: CallbackQuery):
    """Обработчик для возврата в главное меню из оплаты"""
    await callback.message.answer(text=lexicon['start'],
                                  reply_markup=keyboard_start(callback.from_user.id),
                                  disable_web_page_preview=True)


@router.callback_query(F.data == 'back_to_gift_menu')
async def handle_back_to_menu(callback: CallbackQuery):
    """Обработчик для возврата в главное меню из оплаты"""
    await callback.message.edit_text(text=lexicon['gift_start'], reply_markup=keyboard_gift_tariff())


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=KICKED))
async def user_blocked_bot(event: ChatMemberUpdated):
    await sql.update_delete(event.from_user.id, True)
    logger.warning(f'Юзер {event.from_user.id} заблокировал бота')


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=MEMBER))
async def user_unblocked_bot(event: ChatMemberUpdated):
    await sql.update_delete(event.from_user.id, False)
    logger.success(f'Юзер {event.from_user.id} разблокировал бота')


@router.callback_query(F.data == 'r_120')
async def process_payment_method_bonus(callback: CallbackQuery):
    user_data = await sql.get_user(callback.from_user.id)
    if user_data[8]:
        await callback.message.answer('Акция действительна только при первой оплате.',
                                      reply_markup=create_kb(1, back_to_main='🔙 Назад'))
    tariff = callback.data
    await callback.message.answer('Выберите метод оплаты акционной подписки:', reply_markup=keyboard_payment_method_stock(tariff))


@router.chat_member()
async def handle_chat_member_update(update: ChatMemberUpdated):
    if str(update.chat.id) != str(CHANEL_ID):
        return
    user_id = update.new_chat_member.user.id
    user_dct = await sql.get_user(user_id)

    if not user_dct:
        logger.warning(f"User in chanel {user_id} not found in database")
        return

    if update.old_chat_member.status == "left" and update.new_chat_member.status == "member":
        await sql.update_in_chanel(user_id, True)
        logger.success(f"User {user_id} connect to chanel")
    elif update.old_chat_member.status != "left" and update.new_chat_member.status == "left":
        await sql.update_in_chanel(user_id, False)
        logger.warning(f"User {user_id} left chanel")


@router.message(LinkingCodeMessageFilter())
async def process_account_linking_code(message: Message):
    code = message.text.strip().upper()
    hit = await sql.get_valid_linking_code(code)
    if hit is None:
        await message.answer(lexicon["linking_invalid"])
        return
    code_id, creator_internal_id = hit
    creator = await sql.get_user_by_internal_id(creator_internal_id)
    if creator is None:
        await sql.delete_linking_code_by_id(code_id)
        await message.answer(lexicon["linking_invalid"])
        return

    if creator[1] is not None and int(creator[1]) > 0:
        await message.answer(lexicon["linking_use_web"])
        return

    tg_id = message.from_user.id
    if await sql.get_user(tg_id) is None:
        await sql.add_user(tg_id, False, False)

    ok = await sql.merge_email_placeholder_into_telegram(creator_internal_id, tg_id)
    if ok:
        await sql.delete_linking_code_by_id(code_id)
        await message.answer(lexicon["linking_ok"].format(tg_id))
    else:
        await message.answer(lexicon["linking_fail"])


@router.inline_query(lambda query: query.query == 'partner')
async def inline_partner(inline_query: InlineQuery):
    user_id = inline_query.from_user.id

    text = f'''
Привет. Подключись к VPN по моей ссылке:

https://t.me/zoomerskyvpn_bot?start=ref{user_id}

🚀Работает быстро и стабильно.
    '''

    result = InlineQueryResultArticle(
        id="1",
        title='🤝🤝🤝 Приглашение',
        description="Друг, перешедший по этой кнопке станет Вашим рефералом.",
        input_message_content=InputTextMessageContent(
            message_text=text,
            parse_mode='HTML',
            disable_web_page_preview=False
        ),
        reply_markup=keyboard_inline_ref(user_id),
        thumb_url="https://img.freepik.com/premium-photo/glowing-blue-neon-wifi-signal-icon-dark-background_989822-6238.jpg?semt=ais_hybrid"  # опционально: иконка
    )

    # Отправляем результат обратно в Telegram
    await bot.answer_inline_query(
        inline_query.id,
        results=[result],
        cache_time=0
    )
