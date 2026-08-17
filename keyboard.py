import urllib.parse
from typing import List, Optional

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import BOT_URL
from aiogram.utils.keyboard import InlineKeyboardBuilder
from lexicon import dct_price_discount_33

STYLE_PRIMARY = "primary"
STYLE_SUCCESS = "success"
STYLE_DANGER = "danger"

BTN_BACK = "◀️ Назад"

SITE_URL = "https://4zoomer.top/"
OPEN_SITE_CB = "open_site"


def create_kb(
    width: int,
    *,
    styles: Optional[dict[str, str]] = None,
    **kwargs: str,
) -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру. kwargs: callback_data -> текст кнопки.
    styles: callback_data -> 'primary' | 'success' | 'danger' (цвет кнопки в клиентах Telegram).
    """
    kb_builder = InlineKeyboardBuilder()
    buttons: List[InlineKeyboardButton] = []
    style_map = styles or {}

    for button_data, button_text in kwargs.items():
        st = style_map.get(button_data)
        if st:
            buttons.append(
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=button_data,
                    style=st,
                )
            )
        else:
            buttons.append(
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=button_data,
                )
            )

    kb_builder.row(*buttons, width=width)
    return kb_builder.as_markup()


def chanel_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Подписаться на канал",
                url="https://t.me/+C3B1C6zruYc4M2Ey",
                style=STYLE_PRIMARY,
            )
        ]
    ])
    return keyboard


def keyboard_start_bonus(user_id: Optional[int] = None):
    kwargs = {
        "free_vpn": "🔥 Попробовать бесплатно",
        "buy_vpn": "💰 Купить подписку",
    }
    styles = {"free_vpn": STYLE_SUCCESS, "buy_vpn": STYLE_SUCCESS}
    kwargs["create_partner_bot"] = "🤖 Создать своего VPN-бота"
    styles["create_partner_bot"] = STYLE_PRIMARY
    return create_kb(1, styles=styles, **kwargs)


def keyboard_start(user_id: Optional[int] = None):
    markup = create_kb(
        1,
        styles={
            "buy_vpn": STYLE_SUCCESS,
            "connect_vpn": STYLE_PRIMARY,
            "user_profile": STYLE_PRIMARY,
            "manage_devices": STYLE_PRIMARY,
            "ref": STYLE_PRIMARY,
            "buy_gift": STYLE_SUCCESS,
        },
        buy_vpn='💰 Купить подписку',
        connect_vpn='🔗 Подключить VPN',
        user_profile='👤 Профиль',
        manage_devices='📱 Управление устройствами',
        ref='👭 Бесплатный VPN за приглашения',
        buy_gift='🎁 Подарить подписку',
    )
    rows = list(markup.inline_keyboard)
    rows.append(
        [
            InlineKeyboardButton(
                text="🌐 Наш сайт",
                callback_data=OPEN_SITE_CB,
                style=STYLE_PRIMARY,
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🤖 Создать своего VPN-бота",
                callback_data="create_partner_bot",
                style=STYLE_PRIMARY,
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="💸 Зарабатывай с нами",
                callback_data="partner_earn",
                style=STYLE_SUCCESS,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


_STYLES_TARIFF = {
    "r_7": STYLE_PRIMARY,
    "r_30": STYLE_PRIMARY,
    "r_90": STYLE_SUCCESS,
    "r_180": STYLE_SUCCESS,
    "r_365": STYLE_SUCCESS,
    "r_120": STYLE_SUCCESS,
    "r_30old": STYLE_PRIMARY,
    "free_vpn": STYLE_SUCCESS,
}


def keyboard_tariff_bonus():
    return create_kb(
        1,
        styles={**_STYLES_TARIFF, "wl_traffic_buy_sub": STYLE_SUCCESS},
        r_7='👌 7 дней — 99 руб',
        r_30='🤝 30 дней — 299 руб',
        r_90='✅ 90 дней — 749 руб (выгода −17%)',
        r_180='🏆 180 дней — 1349 руб (выгода −25%)',
        r_365='💎 365 дней — 2399 руб (выгода −33%)',
        free_vpn='🔥ПОПРОБОВАТЬ 1 день БЕСПЛАТНО🔥',
        wl_traffic_buy_sub='📦 Купить трафик Антиглушилка',
        back_to_main='🔙 Назад',
    )


def keyboard_tariff():
    return create_kb(
        1,
        styles={
            **{k: v for k, v in _STYLES_TARIFF.items() if k != "free_vpn"},
            "wl_traffic_buy_sub": STYLE_SUCCESS,
        },
        r_7='👌 7 дней — 99 руб',
        r_30='🤝 30 дней — 299 руб',
        r_90='✅ 90 дней — 749 руб (выгода −17%)',
        r_180='🏆 180 дней — 1349 руб (выгода −25%)',
        r_365='💎 365 дней — 2399 руб (выгода −33%)',
        wl_traffic_buy_sub='📦 Купить трафик Антиглушилка',
        back_to_main='🔙 Назад',
    )


def keyboard_tariff_trial():
    return create_kb(
        1,
        styles={
            **{k: v for k, v in _STYLES_TARIFF.items() if k != "free_vpn"},
            "wl_traffic_buy_sub": STYLE_SUCCESS,
        },
        r_7='👌 7 дней — 99 руб',
        r_30='🤝 30 дней — 299 руб',
        r_90='✅ 90 дней — 749 руб (выгода −17%)',
        r_120='🔥 Акция: 120 дней — 749 руб',
        r_180='🏆 180 дней — 1349 руб (выгода −25%)',
        r_365='💎 365 дней — 2399 руб (выгода −33%)',
        wl_traffic_buy_sub='📦 Купить трафик Антиглушилка',
        back_to_main='🔙 Назад',
    )


def keyboard_tariff_old():
    return create_kb(
        1,
        styles={
            "r_30old": STYLE_PRIMARY,
            "r_90": STYLE_SUCCESS,
            "r_180": STYLE_SUCCESS,
            "r_365": STYLE_SUCCESS,
        },
        r_30old='🤝 30 дней — 99 руб',
        r_90='✅ 90 дней — 749 руб (выгода −17%)',
        r_180='🏆 180 дней — 1349 руб (выгода −25%)',
        r_365='💎 365 дней — 2399 руб (выгода −33%)',
        back_to_main='🔙 Назад',
    )


_STYLES_GIFT = {
    "gift_r_7": STYLE_PRIMARY,
    "gift_r_30": STYLE_PRIMARY,
    "gift_r_90": STYLE_SUCCESS,
    "gift_r_180": STYLE_SUCCESS,
    "gift_r_365": STYLE_SUCCESS,
}


def keyboard_gift_tariff():
    return create_kb(
        1,
        styles=_STYLES_GIFT,
        gift_r_7='👌 7 дней — 99 руб',
        gift_r_30='🤝 30 дней — 299 руб',
        gift_r_90='✅ 90 дней — 749 руб (выгода −17%)',
        gift_r_180='🏆 180 дней — 1349 руб (выгода −25%)',
        gift_r_365='💎 365 дней — 2399 руб (выгода −33%)',
        back_to_main='🔙 Назад',
    )


def keyboard_subscription(sub_url, sub_url_white):
    buttons = []
    if sub_url:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="💫 Ваша подписка на VPN PRO",
                    url=sub_url,
                    style=STYLE_PRIMARY,
                )
            ]
        )
    if sub_url_white:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="🦾 Включи мобильный интернет",
                    url=sub_url_white,
                    style=STYLE_PRIMARY,
                )
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton(
                text="🌐 Войти через сайт",
                callback_data=OPEN_SITE_CB,
                style=STYLE_PRIMARY,
            )
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                text="⚠️ Если страница не загружается",
                callback_data='import',
                style=STYLE_DANGER,
            )
        ]
    )
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data='back_to_main')])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def keyboard_sub_after_buy(sub_url):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📋 В личный кабинет",
                url=sub_url,
                style=STYLE_PRIMARY,
            )
        ],
        [
            InlineKeyboardButton(
                text="🌐 Войти через сайт",
                callback_data=OPEN_SITE_CB,
                style=STYLE_PRIMARY,
            )
        ],
        [
            InlineKeyboardButton(
                text="⚠️ Если страница не загружается",
                callback_data='import',
                style=STYLE_DANGER,
            )
        ],
        [
            InlineKeyboardButton(
                text="🎁 Подарить подписку",
                callback_data="buy_gift",
                style=STYLE_SUCCESS,
            )
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data='back_to_main')],
    ])
    return keyboard


def keyboard_sub_after_free(sub_url):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📋 В личный кабинет",
                url=sub_url,
                style=STYLE_PRIMARY,
            )
        ],
        [
            InlineKeyboardButton(
                text="🌐 Войти через сайт",
                callback_data=OPEN_SITE_CB,
                style=STYLE_PRIMARY,
            )
        ],
        [
            InlineKeyboardButton(
                text="⚠️ Если страница не загружается",
                callback_data="import",
                style=STYLE_DANGER,
            )
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")],
    ])
    return keyboard


def keyboard_import_os():
    return create_kb(
        1,
        styles={
            "import_android": STYLE_PRIMARY,
            "import_ios": STYLE_PRIMARY,
            "import_windows": STYLE_PRIMARY,
            "import_macos": STYLE_PRIMARY,
        },
        import_android='🤖 Android',
        import_ios='🍎 iOS',
        import_windows='🖥️ Windows',
        import_macos='🍏 MacOS',
        back_to_main='🔙 Назад',
    )


def keyboard_import_app(os_callback: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🔥 INCY",
                callback_data=f"{os_callback}_incy",
                style=STYLE_PRIMARY,
            )
        ],
        [
            InlineKeyboardButton(
                text="⭐️ Happ",
                callback_data=f"{os_callback}_happ",
                style=STYLE_PRIMARY,
            )
        ],
        [
            InlineKeyboardButton(
                text="📡 V2raytun",
                callback_data=f"{os_callback}_v2",
                style=STYLE_PRIMARY,
            )
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")],
    ])


def keyboard_import_sub(app_callback: str, has_casual: bool, has_white: bool):
    buttons = []
    if has_casual:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="💫 Ваша подписка на VPN PRO",
                    callback_data=f"{app_callback}_casual",
                    style=STYLE_PRIMARY,
                )
            ]
        )
    if has_white:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="🦾 Включи мобильный интернет",
                    callback_data=f"{app_callback}_white",
                    style=STYLE_PRIMARY,
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def keyboard_import_end(url_app: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📥 Скачать приложение",
                url=url_app,
                style=STYLE_PRIMARY,
            )
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")],
    ])


def keyboard_payment_cancel():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="💰 Купить подписку",
                callback_data="buy_vpn",
                style=STYLE_PRIMARY,
            )
        ],
        [
            InlineKeyboardButton(
                text="🎁 Подарить подписку",
                callback_data="start_gift",
                style=STYLE_SUCCESS,
            )
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data='back_to_main')],
    ])
    return keyboard


def keyboard_payment_method(tarif):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        # [
        #     InlineKeyboardButton(
        #         text="⚡ СБП",
        #         callback_data=f"sbp_{tarif}",
        #         style=STYLE_SUCCESS,
        #     )
        # ],
        # [
        #     InlineKeyboardButton(
        #         text="💳 Карта РФ",
        #         callback_data=f"card_{tarif}",
        #         style=STYLE_PRIMARY,
        #     )
        # ],
        [
            InlineKeyboardButton(
                text="⚡СБП",
                callback_data=f"wata_sbp_{tarif}",
                style=STYLE_SUCCESS,
            )
        ],
        [
            InlineKeyboardButton(
                text="💳 Карта РФ",
                callback_data=f"wata_card_{tarif}",
                style=STYLE_PRIMARY,
            )
        ],
        [
            InlineKeyboardButton(
                text="⭐️ Telegram Stars",
                callback_data=f"stars_{tarif}",
                style=STYLE_PRIMARY,
            )
        ],
        [
            InlineKeyboardButton(
                text="💎 Crypto bot",
                callback_data=f"crypto_{tarif}",
                style=STYLE_PRIMARY,
            )
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data='back_to_main')],
    ])
    return keyboard


def keyboard_payment_method_stock(tarif):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        # [
        #     InlineKeyboardButton(
        #         text="⚡ СБП",
        #         callback_data=f"sbp_{tarif}",
        #         style=STYLE_SUCCESS,
        #     )
        # ],
        # [
        #     InlineKeyboardButton(
        #         text="💳 Карта РФ",
        #         callback_data=f"card_{tarif}",
        #         style=STYLE_PRIMARY,
        #     )
        # ],
        [
            InlineKeyboardButton(
                text="⚡СБП",
                callback_data=f"wata_sbp_{tarif}",
                style=STYLE_SUCCESS,
            )
        ],
        [
            InlineKeyboardButton(
                text="💳 Карта РФ",
                callback_data=f"wata_card_{tarif}",
                style=STYLE_PRIMARY,
            )
        ],
        [
            InlineKeyboardButton(
                text="⭐️ Telegram Stars",
                callback_data=f"stars_{tarif}",
                style=STYLE_PRIMARY,
            )
        ],
        [
            InlineKeyboardButton(
                text="💎 Crypto bot",
                callback_data=f"crypto_{tarif}",
                style=STYLE_PRIMARY,
            )
        ],
    ])
    return keyboard


def keyboard_payment_sbp(text, pay_url):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=text,
                url=pay_url,
                style=STYLE_SUCCESS,
            )
        ]
    ])


def keyboard_payment_stars(stars_amount):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"Оплатить {stars_amount} ⭐️",
                pay=True,
                style=STYLE_SUCCESS,
            )
        ]
    ])


def ref_keyboard(user_id):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Пригласить друзей🫶",
                    url=f"https://t.me/share/url?url={BOT_URL}?start=ref{user_id}&text={urllib.parse.quote('Вот ссылка для тебя на надёжный VPN!')}",
                    style=STYLE_SUCCESS,
                )
            ],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")],
        ]
    )
    return keyboard


def keyboard_inline_ref(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🔗 Подключить VPN",
                url=f"https://t.me/zoomerskyvpn_bot?start=ref{user_id}",
                style=STYLE_PRIMARY,
            )
        ]
    ])


def keyboard_partner_intro():
    return create_kb(
        1,
        styles={
            "partner_create_link": STYLE_SUCCESS,
            "back_to_main": STYLE_PRIMARY,
        },
        partner_create_link='🔗 Создать партнёрскую ссылку',
        back_to_main='🔙 Назад',
    )


def keyboard_partner_dashboard():
    return create_kb(
        1,
        styles={
            "partner_withdraw": STYLE_SUCCESS,
            "back_to_main": STYLE_PRIMARY,
        },
        partner_withdraw='💰 Создать заявку на вывод',
        back_to_main='🔙 Назад',
    )


def keyboard_devices_subscriptions(slots: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """slots: (ключ слота, текст кнопки)."""
    buttons = []
    for slot_key, label in slots:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=label[:64],
                    callback_data=f"dev_sub_{slot_key}",
                    style=STYLE_PRIMARY,
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text=BTN_BACK, callback_data="dev_back_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def keyboard_devices_list(
    slot_key: str,
    devices: list[tuple[int, str]],
) -> InlineKeyboardMarkup:
    """devices: (индекс, текст кнопки)."""
    buttons = []
    for idx, btn_text in devices:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=btn_text[:64],
                    callback_data=f"dev_rm_{slot_key}_{idx}",
                    style=STYLE_DANGER,
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text=BTN_BACK, callback_data="dev_back_subs")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def keyboard_devices_confirm(slot_key: str, device_idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да",
                    callback_data=f"dev_rm_yes_{slot_key}_{device_idx}",
                    style=STYLE_DANGER,
                ),
                InlineKeyboardButton(
                    text="❌ Нет",
                    callback_data=f"dev_sub_{slot_key}",
                    style=STYLE_PRIMARY,
                ),
            ],
        ]
    )


def keyboard_discount_push_reveal() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🎁 Узнать награду",
                callback_data="dpush_reveal",
                style=STYLE_PRIMARY,
            )
        ],
    ])


def keyboard_discount_push_buy() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="⚡ Купить со скидкой",
                callback_data="dpush_buy",
                style=STYLE_PRIMARY,
            )
        ],
    ])


def keyboard_discount_push_tariffs() -> InlineKeyboardMarkup:
    p = dct_price_discount_33
    return create_kb(
        1,
        styles={
            "dpush_tariff_7": STYLE_PRIMARY,
            "dpush_tariff_30": STYLE_PRIMARY,
            "dpush_tariff_90": STYLE_SUCCESS,
            "dpush_tariff_180": STYLE_SUCCESS,
            "dpush_tariff_365": STYLE_SUCCESS,
        },
        dpush_tariff_7=f'👌 7 дней — {p["7"]} руб',
        dpush_tariff_30=f'🤝 30 дней — {p["30"]} руб',
        dpush_tariff_90=f'✅ 90 дней — {p["90"]} руб (выгода −17%)',
        dpush_tariff_180=f'🏆 180 дней — {p["180"]} руб (выгода −25%)',
        dpush_tariff_365=f'💎 365 дней — {p["365"]} руб (выгода −33%)',
    )


def keyboard_discount_push_payment(duration: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="⚡СБП",
                callback_data=f"dpush_wata_sbp_{duration}",
                style=STYLE_SUCCESS,
            )
        ],
        [
            InlineKeyboardButton(
                text="💳 Карта РФ",
                callback_data=f"dpush_wata_card_{duration}",
                style=STYLE_PRIMARY,
            )
        ],
        [
            InlineKeyboardButton(
                text="⭐️ Telegram Stars",
                callback_data=f"dpush_stars_{duration}",
                style=STYLE_PRIMARY,
            )
        ],
        [
            InlineKeyboardButton(
                text="💎 Crypto bot",
                callback_data=f"dpush_crypto_{duration}",
                style=STYLE_PRIMARY,
            )
        ],
        [
            InlineKeyboardButton(
                text="🔙 Назад",
                callback_data="dpush_back_tariffs",
            )
        ],
    ])


def keyboard_partner_withdraw(support_url: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="💬 Вывести деньги",
                url=support_url,
                style=STYLE_SUCCESS,
            )
        ],
        [
            InlineKeyboardButton(
                text="🔙 Назад",
                callback_data="partner_earn",
                style=STYLE_PRIMARY,
            )
        ],
    ])


def keyboard_profile() -> InlineKeyboardMarkup:
    return create_kb(
        1,
        styles={
            "wl_traffic_buy": STYLE_SUCCESS,
            "back_to_main": STYLE_PRIMARY,
        },
        wl_traffic_buy="📦 Купить трафик",
        back_to_main=BTN_BACK,
    )


def keyboard_wl_traffic_tariffs(*, back_callback: str = "back_to_main") -> InlineKeyboardMarkup:
    labels = {
        "10": "10 GB — 50 ₽",
        "20": "20 GB — 79 ₽",
        "50": "50 GB — 149 ₽",
        "100": "100 GB — 259 ₽",
        "250": "250 GB — 629 ₽",
        "500": "500 GB — 1249 ₽",
    }
    from_sub = back_callback == "buy_vpn"
    buttons = []
    for mb, label in labels.items():
        cb = f"wl_traffic_sub_{mb}" if from_sub else f"wl_traffic_{mb}"
        buttons.append([
            InlineKeyboardButton(
                text=label,
                callback_data=cb,
                style=STYLE_SUCCESS if mb in ("50", "100", "250", "500") else STYLE_PRIMARY,
            )
        ])
    buttons.append([InlineKeyboardButton(text=BTN_BACK, callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def keyboard_wl_traffic_payment_method(
    mb: str, *, back_callback: str = "wl_traffic_buy"
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⚡СБП",
            callback_data=f"wl_traffic_sbp_{mb}",
            style=STYLE_SUCCESS,
        )],
        [InlineKeyboardButton(
            text="💳 Карта РФ",
            callback_data=f"wl_traffic_card_{mb}",
            style=STYLE_PRIMARY,
        )],
        [InlineKeyboardButton(
            text="⭐️ Telegram Stars",
            callback_data=f"wl_traffic_stars_{mb}",
            style=STYLE_PRIMARY,
        )],
        [InlineKeyboardButton(
            text="💎 Crypto bot",
            callback_data=f"wl_traffic_crypto_{mb}",
            style=STYLE_PRIMARY,
        )],
        [InlineKeyboardButton(text=BTN_BACK, callback_data=back_callback)],
    ])
