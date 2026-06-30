import asyncio
from datetime import datetime

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot import bot
from config import ADMIN_PARTNER_IDS
from config_bd.partner_apps import PartnerAppSQL
from keyboard import create_kb, STYLE_PRIMARY, STYLE_SUCCESS, STYLE_DANGER, BTN_BACK
from logging_config import logger
from services.partner_apply import submit_partner_application
from services.partner_vps_client import (
    PartnerVpsError,
    bot_stats,
    deploy_bot,
    restart_bot,
    stop_bot,
    wait_bot_running,
)
from utils.token_crypto import decrypt_token

router = Router()
partner_sql = PartnerAppSQL()


class PartnerApplyFSM(StatesGroup):
    waiting_token = State()
    waiting_reject_reason = State()


def _is_partner_admin(user_id: int) -> bool:
    return user_id in ADMIN_PARTNER_IDS


async def _safe_edit_text(message: Message, text: str, **kwargs) -> None:
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


async def _safe_edit_message(chat_id: int, message_id: int, text: str, **kwargs) -> None:
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, **kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


def _admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Заявки на модерации", callback_data="pa_list_pending", style=STYLE_PRIMARY)],
        [InlineKeyboardButton(text="✅ Активные боты", callback_data="pa_list_active", style=STYLE_SUCCESS)],
        [InlineKeyboardButton(text="⏸ Остановленные", callback_data="pa_list_stopped", style=STYLE_PRIMARY)],
        #[InlineKeyboardButton(text="🗑 Удалить всех ботов", callback_data="pa_delete_all", style=STYLE_DANGER)],
    ])


def _delete_all_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить всех", callback_data="pa_delete_all_yes", style=STYLE_DANGER),
            InlineKeyboardButton(text="❌ Отмена", callback_data="pa_menu", style=STYLE_PRIMARY),
        ],
    ])


def _deploying_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К меню", callback_data="pa_menu")],
    ])


def _app_card_kb(app_id: int, status: str) -> InlineKeyboardMarkup:
    rows = []
    if status == "pending":
        rows.append([
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"pa_approve_{app_id}", style=STYLE_SUCCESS),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"pa_reject_{app_id}", style=STYLE_DANGER),
        ])
    elif status == "deploying":
        pass
    elif status == "active":
        rows.append([InlineKeyboardButton(text="⏹ Остановить", callback_data=f"pa_stop_{app_id}", style=STYLE_DANGER)])
    elif status == "stopped":
        rows.append([InlineKeyboardButton(text="▶️ Запустить", callback_data=f"pa_start_{app_id}", style=STYLE_SUCCESS)])
    rows.append([InlineKeyboardButton(text="🔙 К меню", callback_data="pa_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_app(app) -> str:
    source_line = ""
    if getattr(app, "source_bot_id", None):
        source_line = f"\nИсточник заявки: бот #{app.source_bot_id}"
    return (
        f"<b>Заявка #{app.id}</b> — <code>{app.status}</code>\n"
        f"Партнёр: {app.partner_first_name or '—'} "
        f"(@{app.partner_username or '—'}, <code>{app.partner_tg_id}</code>)\n"
        f"Бот: {app.bot_display_name} (@{app.bot_username})"
        f"{source_line}\n"
        f"Создана: {app.created_at:%d.%m.%Y %H:%M}"
        + (f"\nПричина отказа: {app.reject_reason}" if app.reject_reason else "")
    )


async def _run_partner_deploy(
    app_id: int,
    admin_id: int,
    chat_id: int,
    message_id: int,
    *,
    is_restart: bool = False,
) -> None:
    app = await partner_sql.get_by_id(app_id)
    if not app:
        logger.error("partner deploy aborted: app_id={} not found", app_id)
        return

    failure_status = "stopped" if is_restart else "pending"
    log_action = "restart" if is_restart else "deploy"
    logger.info(
        "partner {} started: app_id={} admin_id={} bot=@{} partner_tg_id={}",
        log_action,
        app_id,
        admin_id,
        app.bot_username,
        app.partner_tg_id,
    )
    try:
        token = decrypt_token(app.bot_token_encrypted)
        deploy_result = await deploy_bot(
            app_id,
            token,
            app.partner_tg_id,
            app.bot_username,
            source_bot_id=getattr(app, "source_bot_id", None),
        )
        vps_status = await wait_bot_running(app_id, deploy_result)
        logger.info("partner {} VPS confirmed: app_id={} vps_status={}", log_action, app_id, vps_status)

        await partner_sql.update_status(
            app_id,
            "active",
            instance_id=deploy_result.get("instance_id") or vps_status.get("instance_id"),
            deployed_at=datetime.now(),
        )
        updated = await partner_sql.get_by_id(app_id)
        if is_restart:
            partner_text = f"▶️ Бот @{app.bot_username} снова запущен."
            admin_text = (
                f"✅ Бот #{app_id} @{app.bot_username} снова запущен.\n"
                f"Партнёр: <code>{app.partner_tg_id}</code>"
            )
            success_text = f"✅ Бот #{app_id} снова запущен.\n{_format_app(updated)}"
            success_markup = _app_card_kb(app_id, "active")
        else:
            partner_text = (
                f"✅ Ваш бот @{app.bot_username} запущен!\n"
                f"Откройте его и нажмите /start для панели управления."
            )
            admin_text = (
                f"✅ Бот #{app_id} @{app.bot_username} успешно развёрнут на VPS.\n"
                f"Партнёр: <code>{app.partner_tg_id}</code>"
            )
            success_text = f"✅ Бот #{app_id} развёрнут.\n{_format_app(updated)}"
            success_markup = _admin_menu_kb()
        try:
            await bot.send_message(app.partner_tg_id, partner_text)
            logger.info("partner {} notify partner: app_id={} tg_id={}", log_action, app_id, app.partner_tg_id)
        except Exception as e:
            logger.error("partner {} notify partner failed: app_id={} err={}", log_action, app_id, e)
        try:
            await bot.send_message(admin_id, admin_text)
            logger.info("partner {} notify admin: app_id={} admin_id={}", log_action, app_id, admin_id)
        except Exception as e:
            logger.error("partner {} notify admin failed: app_id={} err={}", log_action, app_id, e)

        await _safe_edit_message(
            chat_id,
            message_id,
            success_text,
            reply_markup=success_markup,
        )
        logger.info("partner {} finished ok: app_id={}", log_action, app_id)
    except Exception as e:
        logger.exception("partner {} failed: app_id={} admin_id={}", log_action, app_id, admin_id)
        await partner_sql.update_status(app_id, failure_status)
        failed = await partner_sql.get_by_id(app_id)
        fail_admin_text = f"❌ Ошибка деплоя бота #{app_id} @{app.bot_username}: {e}"
        try:
            await bot.send_message(admin_id, fail_admin_text)
        except Exception as notify_err:
            logger.error("partner {} fail notify admin: app_id={} err={}", log_action, app_id, notify_err)
        if failed:
            await _safe_edit_message(
                chat_id,
                message_id,
                f"❌ Деплой не удался.\n{_format_app(failed)}",
                reply_markup=_app_card_kb(app_id, failure_status),
            )


async def _notify_admins_new_application(partner_tg_id: int, app_id: int, source_bot_id: int | None = None) -> None:
    from services.partner_apply import notify_admins_new_application
    await notify_admins_new_application(partner_tg_id, app_id, source_bot_id)


@router.callback_query(F.data == "create_partner_bot")
async def create_partner_bot_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PartnerApplyFSM.waiting_token)
    await callback.message.answer(
        "🚀 <b>Создай своего бота VPN и зарабатывай!</b>\n\n"
        "💼 Предлагаем партнёрскую программу получения дохода со своего бота VPN.\n\n"
        "💰 <b>В своём боте вы зарабатываете:</b>\n"
        "• <b>50%</b> — от платежей клиентов вашего бота <i>без</i> партнёрской ссылки\n"
        "• <b>20%</b> — от платежей клиентов вашего бота <i>с</i> партнёрской ссылкой\n"
        "• <b>10%</b> — от платежей клиентов партнёров, которые создали своего бота через вас\n\n"
        "🤖 Создайте бота в @BotFather и отправьте его токен.\n"
        "📋 Формат: <code>123456789:ABCdef...</code>",
        reply_markup=create_kb(1, cancel_partner_apply="❌ Отмена"),
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_partner_apply")
async def cancel_partner_apply(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _safe_edit_text(callback.message, "Заявка отменена.")
    await callback.answer()


@router.message(PartnerApplyFSM.waiting_token)
async def partner_token_received(message: Message, state: FSMContext):
    token = (message.text or "").strip()

    app, err = await submit_partner_application(
        partner_tg_id=message.from_user.id,
        partner_username=message.from_user.username,
        partner_first_name=message.from_user.first_name,
        token=token,
        source_bot_id=None,
    )
    if err:
        await message.answer(f"❌ {err}")
        if "уже" in err.lower():
            await state.clear()
        return

    await state.clear()
    await message.answer("✅ Заявка отправлена на модерацию. Мы уведомим вас после проверки.")
    from services.partner_apply import notify_admins_new_application
    await notify_admins_new_application(message.from_user.id, app.id)


@router.message(Command("admin_partner"))
async def admin_partner_command(message: Message):
    if not _is_partner_admin(message.from_user.id):
        await message.answer("❌ Нет доступа.")
        return
    await message.answer("🛠 <b>Админка партнёрских ботов</b>", reply_markup=_admin_menu_kb())


@router.callback_query(F.data == "pa_menu")
async def pa_menu(callback: CallbackQuery):
    if not _is_partner_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _safe_edit_text(callback.message, "🛠 <b>Админка партнёрских ботов</b>", reply_markup=_admin_menu_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("pa_list_"))
async def pa_list(callback: CallbackQuery):
    if not _is_partner_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    status_map = {
        "pa_list_pending": "pending",
        "pa_list_active": "active",
        "pa_list_stopped": ("stopped", "rejected"),
    }
    key = callback.data
    if key == "pa_list_stopped":
        apps = await partner_sql.list_by_status("stopped") + await partner_sql.list_by_status("rejected")
    else:
        apps = await partner_sql.list_by_status(status_map[key])

    if not apps:
        await _safe_edit_text(callback.message, "Список пуст.", reply_markup=_admin_menu_kb())
        await callback.answer()
        return

    lines = []
    kb_rows = []
    for app in apps[:15]:
        extra = ""
        if app.status == "active":
            try:
                st = await bot_stats(app.id)
                extra = f" | юзеров: {st.get('users_count', '?')}"
            except PartnerVpsError:
                pass
        lines.append(f"#{app.id} @{app.bot_username} — {app.status}{extra}")
        kb_rows.append([
            InlineKeyboardButton(
                text=f"#{app.id} @{app.bot_username}",
                callback_data=f"pa_view_{app.id}",
                style=STYLE_PRIMARY,
            )
        ])
    kb_rows.append([InlineKeyboardButton(text="🔙 К меню", callback_data="pa_menu")])
    await _safe_edit_text(
        callback.message,
        "<b>Список:</b>\n" + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pa_view_"))
async def pa_view(callback: CallbackQuery):
    if not _is_partner_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    app_id = int(callback.data.replace("pa_view_", ""))
    app = await partner_sql.get_by_id(app_id)
    if not app:
        await callback.answer("Не найдено", show_alert=True)
        return
    await _safe_edit_text(callback.message, _format_app(app), reply_markup=_app_card_kb(app_id, app.status))
    await callback.answer()


@router.callback_query(F.data.startswith("pa_approve_"))
async def pa_approve(callback: CallbackQuery):
    if not _is_partner_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    app_id = int(callback.data.replace("pa_approve_", ""))
    app = await partner_sql.get_by_id(app_id)
    if not app or app.status != "pending":
        await callback.answer("Заявка недоступна", show_alert=True)
        return

    admin_id = callback.from_user.id
    chat_id = callback.message.chat.id
    message_id = callback.message.message_id

    logger.info("partner approve clicked: app_id={} admin_id={} bot=@{}", app_id, admin_id, app.bot_username)
    await partner_sql.update_status(app_id, "deploying")
    app = await partner_sql.get_by_id(app_id)

    deploying_text = (
        f"{_format_app(app)}\n\n"
        f"⏳ <b>Начался деплой нового бота, подождите...</b>"
    )
    await _safe_edit_text(callback.message, deploying_text, reply_markup=_deploying_kb())
    await callback.answer("Начался деплой, подождите")

    asyncio.create_task(_run_partner_deploy(app_id, admin_id, chat_id, message_id))


@router.callback_query(F.data.startswith("pa_reject_"))
async def pa_reject_start(callback: CallbackQuery, state: FSMContext):
    if not _is_partner_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    app_id = int(callback.data.replace("pa_reject_", ""))
    await state.update_data(reject_app_id=app_id)
    await state.set_state(PartnerApplyFSM.waiting_reject_reason)
    await callback.message.answer(
        f"Отклонение заявки #{app_id}. Отправьте причину (или «-» без причины):",
    )
    await callback.answer()


@router.message(PartnerApplyFSM.waiting_reject_reason)
async def pa_reject_reason(message: Message, state: FSMContext):
    if not _is_partner_admin(message.from_user.id):
        return
    data = await state.get_data()
    app_id = data.get("reject_app_id")
    reason = (message.text or "").strip()
    if reason == "-":
        reason = ""
    await partner_sql.update_status(app_id, "rejected", reject_reason=reason or None)
    app = await partner_sql.get_by_id(app_id)
    if app:
        text = f"❌ Заявка на бота @{app.bot_username} отклонена."
        if reason:
            text += f"\nПричина: {reason}"
        try:
            await bot.send_message(app.partner_tg_id, text)
        except Exception as e:
            logger.error("reject notify: {}", e)
    await state.clear()
    await message.answer("Заявка отклонена.", reply_markup=_admin_menu_kb())


@router.callback_query(F.data.startswith("pa_stop_"))
async def pa_stop(callback: CallbackQuery):
    if not _is_partner_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    app_id = int(callback.data.replace("pa_stop_", ""))
    app = await partner_sql.get_by_id(app_id)
    if not app:
        await callback.answer("Не найдено", show_alert=True)
        return
    try:
        await stop_bot(app_id)
        await partner_sql.update_status(app_id, "stopped")
        await bot.send_message(app.partner_tg_id, f"⏸ Бот @{app.bot_username} остановлен администратором.")
        await callback.answer("Остановлен")
    except PartnerVpsError as e:
        await callback.answer(str(e), show_alert=True)


@router.callback_query(F.data == "pa_delete_all")
async def pa_delete_all_confirm(callback: CallbackQuery):
    if not _is_partner_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    apps = await partner_sql.list_all()
    if not apps:
        await callback.answer("В БД нет заявок", show_alert=True)
        return
    lines = "\n".join(f"• #{a.id} @{a.bot_username} — {a.status}" for a in apps[:20])
    extra = f"\n… и ещё {len(apps) - 20}" if len(apps) > 20 else ""
    await _safe_edit_text(
        callback.message,
        f"⚠️ <b>Удалить всех партнёрских ботов из БД?</b>\n\n"
        f"Будет удалено записей: <b>{len(apps)}</b>\n\n"
        f"{lines}{extra}\n\n"
        f"Активные инстансы на VPS будут остановлены (если доступен API).\n"
        f"<b>Это действие необратимо.</b>",
        reply_markup=_delete_all_confirm_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "pa_delete_all_yes")
async def pa_delete_all_execute(callback: CallbackQuery):
    if not _is_partner_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    apps = await partner_sql.list_all()
    if not apps:
        await callback.answer("Уже пусто", show_alert=True)
        return

    stopped = 0
    stop_errors = 0
    for app in apps:
        if app.status in ("active", "deploying", "stopped"):
            try:
                await stop_bot(app.id)
                stopped += 1
            except PartnerVpsError as e:
                stop_errors += 1
                logger.warning("pa_delete_all stop bot_id={}: {}", app.id, e)

    deleted = await partner_sql.delete_all()
    await _safe_edit_text(
        callback.message,
        f"✅ Удалено из БД: <b>{deleted}</b> записей.\n"
        f"Остановлено на VPS: {stopped}"
        + (f"\n⚠️ Ошибок остановки: {stop_errors}" if stop_errors else ""),
        reply_markup=_admin_menu_kb(),
    )
    await callback.answer("Готово")
    logger.info(
        "pa_delete_all: admin_id={} deleted={} stopped={} stop_errors={}",
        callback.from_user.id,
        deleted,
        stopped,
        stop_errors,
    )


@router.callback_query(F.data.startswith("pa_start_"))
async def pa_start(callback: CallbackQuery):
    if not _is_partner_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    app_id = int(callback.data.replace("pa_start_", ""))
    app = await partner_sql.get_by_id(app_id)
    if not app or app.status != "stopped":
        await callback.answer("Бот недоступен для запуска", show_alert=True)
        return

    admin_id = callback.from_user.id
    chat_id = callback.message.chat.id
    message_id = callback.message.message_id

    logger.info("partner restart clicked: app_id={} admin_id={} bot=@{}", app_id, admin_id, app.bot_username)
    await partner_sql.update_status(app_id, "deploying")
    app = await partner_sql.get_by_id(app_id)

    deploying_text = (
        f"{_format_app(app)}\n\n"
        f"⏳ <b>Деплой бота повторный начался</b>"
    )
    await _safe_edit_text(callback.message, deploying_text, reply_markup=_deploying_kb())
    await callback.answer("Начался повторный деплой, подождите")

    asyncio.create_task(
        _run_partner_deploy(app_id, admin_id, chat_id, message_id, is_restart=True)
    )
