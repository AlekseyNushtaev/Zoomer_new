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
            )
        ]
    ])
    return keyboard


ABOUT_SERVICE_CB = "about_service"


def keyboard_start(
    *,
    has_active_sub: bool = False,
    buy_primary: bool = True,
    sub_url: Optional[str] = None,
    show_trial: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_active_sub:
        if sub_url:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="🔗 Подключить VPN",
                        url=sub_url,
                        style=STYLE_PRIMARY,
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text="Управление подпиской",
                    callback_data="connect_vpn",
                )
            ]
        )
    buy_kwargs = {"text": "💰 Купить подписку", "callback_data": "buy_vpn"}
    if buy_primary:
        buy_kwargs["style"] = STYLE_PRIMARY
    rows.append([InlineKeyboardButton(**buy_kwargs)])
    if show_trial:
        rows.append(
            [InlineKeyboardButton(text="Попробовать бесплатно", callback_data="free_vpn")]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="💸 Заработок",
                callback_data="earn_with_us",
            ),
            InlineKeyboardButton(
                text="🌐 Наш сайт",
                callback_data=OPEN_SITE_CB,
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="О сервисе", callback_data=ABOUT_SERVICE_CB),
            InlineKeyboardButton(
                text="Поддержка",
                url="https://t.me/Helpzoomerbot",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def keyboard_trial_existing_expired() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_BACK, callback_data="back_to_main")],
        ]
    )


def keyboard_subscription_manage(sub_url: str) -> InlineKeyboardMarkup:
    from wl_traffic.constants import WL_TRAFFIC_BUY_CB

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="📦 Купить трафик",
                callback_data=WL_TRAFFIC_BUY_CB,
            )
        ],
        [
            InlineKeyboardButton(
                text="Управление устройствами",
                callback_data="manage_devices",
            ),
        ],
        [
            InlineKeyboardButton(
                text="Если страница не загружается",
                callback_data="import",
            )
        ],
        [InlineKeyboardButton(text=BTN_BACK, callback_data="back_to_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def keyboard_about_service() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Пользовательское соглашение",
                    url="https://telegra.ph/Polzovatelskoe-soglashenie-08-11-20",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Политика конфиденциальности",
                    url="https://telegra.ph/Politika-konfidencialnosti-08-11-52",
                )
            ],
            [InlineKeyboardButton(text=BTN_BACK, callback_data="back_to_main")],
        ]
    )


def keyboard_buy_menu() -> InlineKeyboardMarkup:
    return create_kb(
        1,
        buy_vpn_self='👤 Для себя',
        buy_gift='🎁 Подарить подписку',
        back_to_main=BTN_BACK,
    )


def keyboard_earn_with_us() -> InlineKeyboardMarkup:
    return create_kb(
        1,
        ref='👭 Бесплатный VPN за приглашения',
        partner_earn='🔗 Партнерская ссылка',
        create_partner_bot='🤖 Хочу своего бота',
        back_to_main=BTN_BACK,
    )


def keyboard_tariff_bonus():
    return create_kb(
        1,
        r_7='👌 7 дней — 99 руб',
        r_30='🤝 30 дней — 299 руб',
        r_90='✅ 90 дней — 749 руб (выгода −17%)',
        r_180='🏆 180 дней — 1349 руб (выгода −25%)',
        r_365='💎 365 дней — 2399 руб (выгода −33%)',
        free_vpn='🔥ПОПРОБОВАТЬ 1 день БЕСПЛАТНО🔥',
        wl_traffic_buy_sub='📦 Купить трафик Антиглушилка',
        back_to_buy_menu='🔙 Назад',
    )


def keyboard_tariff():
    return create_kb(
        1,
        r_7='👌 7 дней — 99 руб',
        r_30='🤝 30 дней — 299 руб',
        r_90='✅ 90 дней — 749 руб (выгода −17%)',
        r_180='🏆 180 дней — 1349 руб (выгода −25%)',
        r_365='💎 365 дней — 2399 руб (выгода −33%)',
        wl_traffic_buy_sub='📦 Купить трафик Антиглушилка',
        back_to_buy_menu='🔙 Назад',
    )


def keyboard_tariff_trial():
    return create_kb(
        1,
        r_7='👌 7 дней — 99 руб',
        r_30='🤝 30 дней — 299 руб',
        r_90='✅ 90 дней — 749 руб (выгода −17%)',
        r_120='🔥 Акция: 120 дней — 749 руб',
        r_180='🏆 180 дней — 1349 руб (выгода −25%)',
        r_365='💎 365 дней — 2399 руб (выгода −33%)',
        wl_traffic_buy_sub='📦 Купить трафик Антиглушилка',
        back_to_buy_menu='🔙 Назад',
    )


def keyboard_tariff_old():
    return create_kb(
        1,
        r_30old='🤝 30 дней — 99 руб',
        r_90='✅ 90 дней — 749 руб (выгода −17%)',
        r_180='🏆 180 дней — 1349 руб (выгода −25%)',
        r_365='💎 365 дней — 2399 руб (выгода −33%)',
        back_to_main='🔙 Назад',
    )


def keyboard_gift_tariff():
    return create_kb(
        1,
        gift_r_7='👌 7 дней — 99 руб',
        gift_r_30='🤝 30 дней — 299 руб',
        gift_r_90='✅ 90 дней — 749 руб (выгода −17%)',
        gift_r_180='🏆 180 дней — 1349 руб (выгода −25%)',
        gift_r_365='💎 365 дней — 2399 руб (выгода −33%)',
        back_to_buy_menu='🔙 Назад',
    )


def keyboard_subscription(sub_url):
    buttons = []
    if sub_url:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="💫 Ваша подписка на VPN PRO",
                    url=sub_url,
                )
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton(
                text="🌐 Войти через сайт",
                callback_data=OPEN_SITE_CB,
            )
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                text="⚠️ Если страница не загружается",
                callback_data='import',
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
            )
        ],
        [
            InlineKeyboardButton(
                text="🌐 Войти через сайт",
                callback_data=OPEN_SITE_CB,
            )
        ],
        [
            InlineKeyboardButton(
                text="⚠️ Если страница не загружается",
                callback_data='import',
            )
        ],
        [
            InlineKeyboardButton(
                text="🎁 Подарить подписку",
                callback_data="buy_gift",
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
            )
        ],
        [
            InlineKeyboardButton(
                text="🌐 Войти через сайт",
                callback_data=OPEN_SITE_CB,
            )
        ],
        [
            InlineKeyboardButton(
                text="⚠️ Если страница не загружается",
                callback_data="import",
            )
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")],
    ])
    return keyboard


def keyboard_import_os():
    return create_kb(
        1,
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
            )
        ],
        [
            InlineKeyboardButton(
                text="⭐️ Happ",
                callback_data=f"{os_callback}_happ",
            )
        ],
        [
            InlineKeyboardButton(
                text="📡 V2raytun",
                callback_data=f"{os_callback}_v2",
            )
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="import")],
    ])


def keyboard_import_after_album() -> InlineKeyboardMarkup:
    return create_kb(
        1,
        connect_vpn="🔙 Назад к подписке",
    )


def keyboard_import_sub(app_callback: str, has_casual: bool):
    buttons = []
    if has_casual:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="💫 Ваша подписка на VPN PRO",
                    callback_data=f"{app_callback}_casual",
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
            )
        ],
        [
            InlineKeyboardButton(
                text="🎁 Подарить подписку",
                callback_data="start_gift",
            )
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data='back_to_main')],
    ])
    return keyboard


def keyboard_payment_method(tarif):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="⚡СБП",
                callback_data=f"wata_sbp_{tarif}",
            )
        ],
        [
            InlineKeyboardButton(
                text="💳 Карта РФ",
                callback_data=f"wata_card_{tarif}",
            )
        ],
        [
            InlineKeyboardButton(
                text="⭐️ Telegram Stars",
                callback_data=f"stars_{tarif}",
            )
        ],
        [
            InlineKeyboardButton(
                text="💎 Crypto bot",
                callback_data=f"crypto_{tarif}",
            )
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data='back_to_main')],
    ])
    return keyboard


def keyboard_payment_method_stock(tarif):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="⚡СБП",
                callback_data=f"wata_sbp_{tarif}",
            )
        ],
        [
            InlineKeyboardButton(
                text="💳 Карта РФ",
                callback_data=f"wata_card_{tarif}",
            )
        ],
        [
            InlineKeyboardButton(
                text="⭐️ Telegram Stars",
                callback_data=f"stars_{tarif}",
            )
        ],
        [
            InlineKeyboardButton(
                text="💎 Crypto bot",
                callback_data=f"crypto_{tarif}",
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
            )
        ]
    ])


def keyboard_payment_stars(stars_amount):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"Оплатить {stars_amount} ⭐️",
                pay=True,
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
                )
            ],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_earn")],
        ]
    )
    return keyboard


def keyboard_inline_ref(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🔗 Подключить VPN",
                url=f"https://t.me/zoomerskyvpn_bot?start=ref{user_id}",
            )
        ]
    ])


def keyboard_partner_intro():
    return create_kb(
        1,
        partner_create_link='🔗 Создать партнёрскую ссылку',
        back_to_earn=BTN_BACK,
    )


def keyboard_partner_dashboard():
    return create_kb(
        1,
        partner_withdraw='💰 Создать заявку на вывод',
        back_to_earn=BTN_BACK,
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
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text=BTN_BACK, callback_data="back_to_profile")])
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
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text=BTN_BACK, callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def keyboard_devices_confirm(slot_key: str, device_idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да",
                    callback_data=f"dev_rm_yes_{slot_key}_{device_idx}",
                ),
                InlineKeyboardButton(
                    text="❌ Нет",
                    callback_data=f"dev_sub_{slot_key}",
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
            )
        ],
    ])


def keyboard_discount_push_buy() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="⚡ Купить со скидкой",
                callback_data="dpush_buy",
            )
        ],
    ])


def keyboard_discount_push_tariffs() -> InlineKeyboardMarkup:
    p = dct_price_discount_33
    return create_kb(
        1,
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
            )
        ],
        [
            InlineKeyboardButton(
                text="💳 Карта РФ",
                callback_data=f"dpush_wata_card_{duration}",
            )
        ],
        [
            InlineKeyboardButton(
                text="⭐️ Telegram Stars",
                callback_data=f"dpush_stars_{duration}",
            )
        ],
        [
            InlineKeyboardButton(
                text="💎 Crypto bot",
                callback_data=f"dpush_crypto_{duration}",
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
            )
        ],
        [
            InlineKeyboardButton(
                text="🔙 Назад",
                callback_data="partner_earn",
            )
        ],
    ])


def keyboard_profile() -> InlineKeyboardMarkup:
    return create_kb(
        1,
        manage_devices="📱 Управление устройствами",
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
    from_sub = back_callback in ("buy_vpn", "buy_vpn_self")
    buttons = []
    for mb, label in labels.items():
        cb = f"wl_traffic_sub_{mb}" if from_sub else f"wl_traffic_{mb}"
        buttons.append([
            InlineKeyboardButton(
                text=label,
                callback_data=cb,
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
        )],
        [InlineKeyboardButton(
            text="💳 Карта РФ",
            callback_data=f"wl_traffic_card_{mb}",
        )],
        [InlineKeyboardButton(
            text="⭐️ Telegram Stars",
            callback_data=f"wl_traffic_stars_{mb}",
        )],
        [InlineKeyboardButton(
            text="💎 Crypto bot",
            callback_data=f"wl_traffic_crypto_{mb}",
        )],
        [InlineKeyboardButton(text=BTN_BACK, callback_data=back_callback)],
    ])
