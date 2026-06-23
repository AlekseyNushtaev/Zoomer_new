from dotenv import load_dotenv
import os
from typing import Set, Optional
from urllib.parse import quote_plus

# Загрузка переменных окружения из .env файла
load_dotenv()

TG_TOKEN: Optional[str] = os.environ.get("TG_TOKEN")
ADMIN_IDS: Set[int] = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(', ')} if os.environ.get("ADMIN_IDS") else set()
_cid = os.environ.get("CHECKER_ID")
CHECKER_ID: Optional[int] = int(_cid) if _cid else None
CHECKER_IDS: Set[int] = {int(x) for x in os.environ.get("CHECKER_IDS", "").split(', ')} if os.environ.get("CHECKER_IDS") else set()
PLATEGA_API_KEY: Optional[str] = os.environ.get("PLATEGA_API_KEY")
PLATEGA_MERCHANT_ID: Optional[str] = os.environ.get("PLATEGA_MERCHANT_ID")
WATA_API_SBP_KEY: Optional[str] = os.environ.get("WATA_API_SBP_KEY")
WATA_API_CARD_KEY: Optional[str] = os.environ.get("WATA_API_CARD_KEY")
# Боевой: https://api.wata.pro/api/h2h — песочница: https://api-sandbox.wata.pro/api/h2h
WATA_API_BASE: str = os.environ.get("WATA_API_BASE", "https://api.wata.pro/api/h2h").rstrip("/")
# FreeKassa API v1 (orders/create, orders)
API_FREEKASSA: Optional[str] = (os.environ.get("API_FREEKASSA") or "").strip() or None
SHOP_ID_FREEKASSA: Optional[int] = (
    int(os.environ["SHOP_ID_FREEKASSA"]) if os.environ.get("SHOP_ID_FREEKASSA") else None
)
FREEKASSA_SERVER_IP: str = os.environ.get("FREEKASSA_SERVER_IP", "72.56.14.94")
# Lead Tracker (POST /users/, /users/trial, /users/connected, /payments/)
LEAD_TRACKER_BASE: Optional[str] = (os.environ.get("LEAD_TRACKER_BASE") or "").strip() or None
LEAD_TRACKER_API_KEY: Optional[str] = (os.environ.get("LEAD_TRACKER_API_KEY") or "").strip() or None
LEAD_TRACKER_STAR_RUB_PER_STAR: str = os.environ.get("LEAD_TRACKER_STAR_RUB_PER_STAR", "1.0")
CHANEL_ID: Optional[int] = int(os.environ.get("CHANEL_ID"))
CRYPTOBOT_API_TOKEN: Optional[str] = os.environ.get("CRYPTOBOT_API_TOKEN")
PANEL_URL: Optional[str] = os.environ.get("PANEL_URL")
PANEL_API_TOKEN: Optional[str] = os.environ.get("PANEL_API_TOKEN")
SHORT_UUID_SECRET: Optional[str] = os.environ.get("SHORT_UUID_SECRET")
BOT_URL: str = os.environ.get("BOT_URL") or "https://t.me/zoomerskyvpn_bot"
PARTNER_PROCENT: int = int(os.environ.get("PARTNER_PROCENT", "20"))
PARTNER_MIN: int = int(os.environ.get("PARTNER_MIN", "500"))
SUPPORT_URL: str = (
    os.environ.get("SUPPORT_URL")
    or os.environ.get("PARTNER_SUPPORT_URL")
    or "https://t.me/suppzoomvpn"
)
PARTNER_SUPPORT_URL: str = SUPPORT_URL
# Публичный URL веб-сайта (ЛК), без завершающего слэша — кнопка после входа через Telegram и т.п.
PUBLIC_SITE_URL: str = (os.environ.get("PUBLIC_SITE_URL") or "").strip().rstrip("/")

JWT_SECRET: Optional[str] = os.environ.get("JWT_SECRET")
GOOGLE_CLIENT_ID: Optional[str] = os.environ.get("GOOGLE_CLIENT_ID")
WEB_API_PORT: int = int(os.environ.get("WEB_API_PORT", "8080"))

# Кастомная страница подписки: POST /api/v1/sub_page/pay/* (заголовок X-Sub-Page-Api-Key или Bearer).
SUB_PAGE_API_KEY: Optional[str] = (os.environ.get("SUB_PAGE_API_KEY") or "").strip() or None

# Антиспам по апдейтам Telegram: не более N событий от одного user_id за window секунд (скользящее окно).
THROTTLE_MAX_UPDATES: int = int(os.environ.get("THROTTLE_MAX_UPDATES", "25"))
THROTTLE_WINDOW_SEC: float = float(os.environ.get("THROTTLE_WINDOW_SEC", "8"))

# Максимум одновременно «висящих» счетов на пользователя (WATA СБП/карта, Platega, Cryptobot — в сумме).
PAYMENT_MAX_PENDING_PER_USER: int = int(os.environ.get("PAYMENT_MAX_PENDING_PER_USER", "8"))

# Почта для сброса пароля (опционально; иначе код уходит в Telegram, если есть привязка)
SMTP_HOST: Optional[str] = os.environ.get("SMTP_HOST")
SMTP_PORT: int = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER: Optional[str] = os.environ.get("SMTP_USER")
SMTP_PASSWORD: Optional[str] = os.environ.get("SMTP_PASSWORD")
SMTP_FROM: Optional[str] = os.environ.get("SMTP_FROM")

POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_DB = os.getenv("POSTGRES_DB")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
# Пул на процесс: бот и отдельный API — отдельные процессы, у каждого свой пул.
POSTGRES_POOL_SIZE = int(os.getenv("POSTGRES_POOL_SIZE", "10"))
POSTGRES_MAX_OVERFLOW = int(os.getenv("POSTGRES_MAX_OVERFLOW", "20"))
POSTGRES_POOL_RECYCLE = int(os.getenv("POSTGRES_POOL_RECYCLE", "1800"))


def build_database_url() -> str:
    if not POSTGRES_USER or not POSTGRES_PASSWORD or not POSTGRES_DB:
        raise RuntimeError(
            "Укажите POSTGRES_USER, POSTGRES_PASSWORD и POSTGRES_DB в окружении (.env)."
        )
    user = quote_plus(POSTGRES_USER)
    password = quote_plus(POSTGRES_PASSWORD)
    return (
        f"postgresql+asyncpg://{user}:{password}"
        f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
    )


DATABASE_URL = build_database_url()

ADMIN_PARTNER_IDS: Set[int] = {
    int(x.strip()) for x in os.environ.get("ADMIN_PARTNER_IDS", "").split(",") if x.strip()
}
PARTNER_VPS_IP: str = (os.environ.get("PARTNER_VPS_IP") or "").strip().rstrip("/")
PARTNER_VPS_API_KEY: Optional[str] = (os.environ.get("PARTNER_VPS_API_KEY") or "").strip() or None
TOKEN_ENCRYPTION_KEY: Optional[str] = (os.environ.get("TOKEN_ENCRYPTION_KEY") or "").strip() or None
PARTNER_BOT_API_KEY: Optional[str] = (os.environ.get("PARTNER_BOT_API_KEY") or "").strip() or None