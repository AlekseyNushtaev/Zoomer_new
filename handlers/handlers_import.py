from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InputMediaPhoto

from bot import bot, sql, x3
from keyboard import (
    keyboard_import_os,
    keyboard_import_app,
    keyboard_import_after_album,
    create_kb,
)
from lexicon import lexicon
from utils.menu_photos import import_photos
from utils.menu_ui import edit_or_send_photo

router: Router = Router()

OS_CALLBACKS = {'import_android', 'import_ios', 'import_windows', 'import_macos'}

OS_DISPLAY = {
    'android': '🤖 Android',
    'ios': '🍎 iOS',
    'windows': '🖥️ Windows',
    'macos': '🍏 MacOS',
}

APP_DISPLAY = {
    'incy': '🔥 INCY',
    'happ': '⭐️ Happ',
    'v2': '📡 V2raytun',
}

IMPORT_URLS = {
    'android': {
        'incy': {
            'url_app': 'https://play.google.com/store/apps/details?id=llc.itdev.incy',
        },
        'happ': {
            'url_app': 'https://play.google.com/store/apps/details?id=com.happproxy',
            'url_import': 'happ://add/{sub_link}',
        },
        'v2': {
            'url_app': 'https://play.google.com/store/apps/details?id=com.v2raytun.android',
            'url_import': 'v2raytun://import/{sub_link}',
        },
    },
    'ios': {
        'incy': {
            'url_app': 'https://apps.apple.com/ru/app/incy/id6756943388',
        },
        'happ': {
            'url_app': 'https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973',
            'url_import': 'happ://add/{sub_link}',
        },
        'v2': {
            'url_app': 'https://apps.apple.com/app/v2raytun/id6476628951',
            'url_import': 'v2raytun://import/{sub_link}',
        },
    },
    'windows': {
        'incy': {
            'url_app': 'https://github.com/INCY-DEV/incy-platforms/releases/latest/download/incy-windows-setup.exe',
        },
        'happ': {
            'url_app': 'https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe',
            'url_import': 'happ://add/{sub_link}',
        },
        'v2': {
            'url_app': 'https://v2raytun.com/',
            'url_import': 'v2raytun://import/{sub_link}',
        },
    },
    'macos': {
        'incy': {
            'url_app': 'https://github.com/INCY-DEV/incy-platforms/releases/latest/download/incy-macos-arm64.dmg',
        },
        'happ': {
            'url_app': 'https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973',
            'url_import': 'happ://add/{sub_link}',
        },
        'v2': {
            'url_app': 'https://apps.apple.com/ru/app/v2raytun/id6476628951',
            'url_import': 'v2raytun://import/{sub_link}',
        },
    },
}


def _parse_import_app_callback(data: str) -> tuple[str, str] | None:
    parts = (data or "").split("_")
    if len(parts) < 3 or parts[0] != "import":
        return None
    os_key, app_key = parts[1], parts[2]
    if os_key not in OS_DISPLAY or app_key not in APP_DISPLAY:
        return None
    return os_key, app_key


async def _finish_import(callback: CallbackQuery, os_key: str, app_key: str) -> None:
    user_id = str(callback.from_user.id)
    sub_url = await x3.sublink(user_id)
    label = "💫 VPN PRO"

    if not sub_url:
        await edit_or_send_photo(
            callback,
            "faq",
            "❌ Не удалось получить ссылку. Обратитесь в поддержку.",
            create_kb(1, back_to_main="🔙 Назад"),
        )
        return

    urls = IMPORT_URLS[os_key][app_key]
    url_app = urls["url_app"]

    if app_key == "incy":
        lexicon_key = "import_end_incy"
    elif app_key == "happ":
        lexicon_key = "import_end_happ"
    else:
        lexicon_key = "import_end_v2"

    photos = import_photos(app_key)

    caption = lexicon[lexicon_key].format(
        os=OS_DISPLAY[os_key],
        app=APP_DISPLAY[app_key],
        label=label,
        url_app=url_app,
        url_import=sub_url,
    )

    media = [InputMediaPhoto(media=file_id) for file_id in photos]
    media[0] = InputMediaPhoto(media=photos[0], caption=caption, parse_mode="HTML")

    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass

    await bot.send_media_group(callback.message.chat.id, media=media)
    await bot.send_message(
        callback.message.chat.id,
        "Если нужно, вернитесь в меню:",
        reply_markup=keyboard_import_after_album(),
    )


@router.callback_query(F.data == 'import')
async def import_select_os(callback: CallbackQuery):
    await callback.answer()
    await edit_or_send_photo(
        callback,
        "faq",
        lexicon['import_start'],
        keyboard_import_os(),
    )


@router.callback_query(F.data.in_(OS_CALLBACKS))
async def import_select_app(callback: CallbackQuery):
    await callback.answer()
    await edit_or_send_photo(
        callback,
        "faq",
        lexicon['import_select_app'],
        keyboard_import_app(callback.data),
    )


@router.callback_query(
    F.data.startswith('import_') &
    (F.data.endswith('_incy') | F.data.endswith('_happ') | F.data.endswith('_v2'))
)
async def import_select_sub(callback: CallbackQuery):
    user_data = await sql.get_user(callback.from_user.id)
    has_casual = bool(user_data and user_data[9])

    if not has_casual:
        await callback.answer()
        await edit_or_send_photo(
            callback,
            "faq",
            lexicon['no_sub'],
            create_kb(1, back_to_main='🔙 Назад'),
        )
        return

    parsed = _parse_import_app_callback(callback.data)
    if not parsed:
        await callback.answer("Неверный выбор.", show_alert=True)
        return

    await callback.answer()
    os_key, app_key = parsed
    await _finish_import(callback, os_key, app_key)


@router.callback_query(
    F.data.startswith('import_') &
    F.data.endswith('_casual')
)
async def import_end(callback: CallbackQuery):
    """Старые сообщения с выбором подписки — сразу инструкция."""
    parsed = _parse_import_app_callback(callback.data)
    if not parsed:
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    await callback.answer()
    os_key, app_key = parsed
    await _finish_import(callback, os_key, app_key)
