from aiogram import Router, F
from aiogram.types import CallbackQuery, InputMediaPhoto

from bot import sql, x3
from keyboard import (
    keyboard_import_os,
    keyboard_import_app,
    keyboard_import_sub,
    keyboard_import_end,
    create_kb,
)
from lexicon import lexicon

router: Router = Router()

OS_CALLBACKS = {'import_android', 'import_ios', 'import_windows', 'import_macos'}

INCY_PHOTOS = [
    'AgACAgQAAxkBAAGKrdNqQiUf_p1S_47iMASx26z1h-vIYwADDmsbah0YUqdKC9q7VzbCAQADAgADeAADPAQ',
    'AgACAgQAAxkBAAGKrd1qQiU5wcFKAX2nsvvka2PvqxfZoAACAQ5rG2odGFIhi-_aVyh4JwEAAwIAA3gAAzwE',
]

HAPP_PHOTOS = [
    'AgACAgIAAxkBAAEQ72Rpu6TFlYB57q-1ovQZamC8oCuvIwACSRdrG_ly2ElqTaWIZs_b5wEAAwIAA3kAAzoE',
    'AgACAgIAAxkBAAEQ72Zpu6TTtTiuL0Z1lFD3v9pFrjcyyQACShdrG_ly2EnjX6j31mWvqwEAAwIAA3kAAzoE',
]

V2_PHOTOS = [
    'AgACAgIAAxkBAAEQ73Npu6UVieJU3Bd-TaeF-lhHFaam5AACTRdrG_ly2El7qYfhiDllAAEBAAMCAAN5AAM6BA',
    'AgACAgIAAxkBAAEQ73Vpu6UdeEpyg_2bF0v4BqGiqs2MdQACThdrG_ly2EnTgHMXZPyj_QEAAwIAA3kAAzoE',
    'AgACAgIAAxkBAAEQ73tpu6UvSnw8j_IJRSGhIRwpVBGz2AACTxdrG_ly2EkeG-IsWsAVkQEAAwIAA3kAAzoE',
]

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


@router.callback_query(F.data == 'import')
async def import_select_os(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        text=lexicon['import_start'],
        reply_markup=keyboard_import_os()
    )


@router.callback_query(F.data.in_(OS_CALLBACKS))
async def import_select_app(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        text=lexicon['import_select_app'],
        reply_markup=keyboard_import_app(callback.data)
    )


@router.callback_query(
    F.data.startswith('import_') &
    (F.data.endswith('_incy') | F.data.endswith('_happ') | F.data.endswith('_v2'))
)
async def import_select_sub(callback: CallbackQuery):
    user_data = await sql.get_user(callback.from_user.id)
    has_casual = has_white = False
    if user_data and user_data[9]:
        has_casual = True
    if user_data and user_data[10]:
        has_white = True

    if not has_casual and not has_white:
        await callback.answer()
        await callback.message.answer(
            text=lexicon['no_sub'],
            reply_markup=create_kb(1, back_to_main='🔙 Назад')
        )
        return

    await callback.answer()
    await callback.message.answer(
        text=lexicon['import_select_sub'],
        reply_markup=keyboard_import_sub(callback.data, has_casual, has_white)
    )


@router.callback_query(
    F.data.startswith('import_') &
    (F.data.endswith('_casual') | F.data.endswith('_white'))
)
async def import_end(callback: CallbackQuery):
    await callback.answer()
    user_id = str(callback.from_user.id)

    if callback.data.endswith('_white'):
        sub_url = await x3.sublink(user_id + '_white')
        label = '🦾 Включи мобильный'
    else:
        sub_url = await x3.sublink(user_id)
        label = '💫 VPN PRO'

    if not sub_url:
        await callback.message.answer(
            '❌ Не удалось получить ссылку. Обратитесь в поддержку.',
            reply_markup=create_kb(1, back_to_main='🔙 Назад')
        )
        return

    parts = callback.data.split('_')
    os_key = parts[1]
    app_key = parts[2]

    urls = IMPORT_URLS[os_key][app_key]
    url_app = urls['url_app']

    if app_key == 'incy':
        lexicon_key = 'import_end_incy'
        photos = INCY_PHOTOS
    elif app_key == 'happ':
        lexicon_key = 'import_end_happ'
        photos = HAPP_PHOTOS
    else:
        lexicon_key = 'import_end_v2'
        photos = V2_PHOTOS

    caption = lexicon[lexicon_key].format(
        os=OS_DISPLAY[os_key],
        app=APP_DISPLAY[app_key],
        label=label,
        url_app=url_app,
        url_import=sub_url,
    )

    media = [InputMediaPhoto(media=file_id) for file_id in photos]
    media[0] = InputMediaPhoto(media=photos[0], caption=caption, parse_mode='HTML')

    await callback.message.answer_media_group(media=media)
