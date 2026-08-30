"""Константы лимита трафика сервера Антиглушилка (белые ноды)."""

from datetime import datetime
from zoneinfo import ZoneInfo

# Белые ноды Антиглушилка: расход суммируется по всем.
WL_NODE_NAMES = (
    "YANDEX-RU-002",
    "Yandex-RU-003",
)
WL_TIMEZONE = ZoneInfo("Europe/Moscow")
# Сутки WL-трафика: с 03:00 до 02:59 МСК (накопление в 02:57, проверка после 03:05).
WL_DAY_RESET_HOUR = 3
WL_ACCUMULATE_HOUR = 2
WL_ACCUMULATE_MINUTE = 57
WL_CHECK_SKIP_UNTIL_HOUR = 3
WL_CHECK_SKIP_UNTIL_MINUTE = 5
WL_LEGACY_RETRIES = 3
WL_TOP_USERS_LIMIT = 5000

# С белой нодой (Антиглушилка)
WL_SQUAD_ACTIVE = (
    "2a2236d1-517b-4015-b961-eae22d2ef7fe",
    "889e0d7a-cfa6-4bf9-b2ed-3cb7a1b44cbd",
)

# Без белой ноды
WL_SQUAD_LIMITED = (
    "3cda696c-5cd0-4a2e-a800-d4dc32d03ae0",
    "0a8e3fa0-4fa3-4198-86fc-227bf5a4bf3b",
)

WL_TRIAL_LIMIT_GB = 2.0
WL_GB_PER_MONTH = 10
WL_LOW_TRAFFIC_WARNING_GB = 1.0

# Тариф «Навсегда» (5000 дней) — expire далеко за 2030
FOREVER_DURATION_DAYS = 5000
FOREVER_YEAR_THRESHOLD = 2030
FOREVER_END_CUTOFF = datetime(FOREVER_YEAR_THRESHOLD, 1, 1)

# gb -> price (₽), от большего к меньшему
WL_TRAFFIC_TARIFFS: dict[str, int] = {
    "500": 1249,
    "250": 629,
    "100": 259,
    "50": 149,
    "20": 79,
    "10": 50,
}

# duration days -> months for +10 GB/month bonus on subscription payment
WL_SUBSCRIPTION_MONTHS: dict[int, int] = {
    7: 0,
    30: 1,
    90: 3,
    120: 4,
    180: 6,
    365: 12,
    730: 24,
    FOREVER_DURATION_DAYS: 1,
}

PROFILE_CB = "user_profile"
WL_TRAFFIC_BUY_CB = "wl_traffic_buy"
WL_TRAFFIC_BUY_SUB_CB = "wl_traffic_buy_sub"
BUY_VPN_CB = "buy_vpn"
