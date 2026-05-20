import asyncio
import hashlib
import hmac
import os
import secrets
import smtplib
import string
import time
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal, Optional

import bcrypt
import jwt
from aiogram.types import LabeledPrice
from fastapi import Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError

from bot import bot, sql, x3
from lead_tracker import post_user_trial
from config_bd.utils import _norm_email, user_row_to_api_dict
from X3 import panel_username_for_site_user
from config import (
    ADMIN_IDS,
    API_FREEKASSA,
    BOT_URL,
    CRYPTOBOT_API_TOKEN,
    GOOGLE_CLIENT_ID,
    JWT_SECRET,
    PAYMENT_MAX_PENDING_PER_USER,
    SHOP_ID_FREEKASSA,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USER,
    SUB_PAGE_API_KEY,
    TG_TOKEN,
)
from keyboard import keyboard_payment_stars
from lexicon import dct_desc, dct_price, lexicon
from logging_config import logger
from payments.pay_cryptobot import create_cryptobot_payment
from payments.pay_freekassa import pay_site
from payments.pay_stars import get_stars_amount
import aiohttp


# ── Rate limiter (in-memory, per-IP) ─────────────────────────────────
_rate_limits: dict[str, list[float]] = {}


def _rate_check(key: str, max_requests: int, window_sec: int) -> bool:
    """Returns True if allowed, False if rate-limited."""
    now = time.time()
    timestamps = _rate_limits.get(key, [])
    timestamps = [t for t in timestamps if now - t < window_sec]
    if len(timestamps) >= max_requests:
        _rate_limits[key] = timestamps
        return False
    timestamps.append(now)
    _rate_limits[key] = timestamps
    return True


def _rate_limit_or_raise(request_ip: str, action: str, max_req: int = 5, window: int = 300):
    key = f"{action}:{request_ip}"
    if not _rate_check(key, max_req, window):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много попыток. Подождите 5 минут.")


def _client_ip_for_rate_limit(request: Request) -> str:
    x_real = (request.headers.get("x-real-ip") or "").strip()
    if x_real:
        return x_real
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if xff:
        return xff
    return request.client.host if request.client else ""


# ── Telegram deeplink auth tokens (in-memory) ───────────────────────
_tg_auth_tokens: dict[str, dict[str, Any]] = {}
TG_AUTH_TOKEN_TTL = 300  # 5 minutes


def _cleanup_expired_tg_tokens() -> None:
    now = time.time()
    expired = [k for k, v in _tg_auth_tokens.items() if now - v["created"] > TG_AUTH_TOKEN_TTL]
    for k in expired:
        del _tg_auth_tokens[k]


def confirm_tg_auth_token(token: str, telegram_user_id: int, first_name: str = "", username: str = None) -> bool:
    """Called by the bot when user sends /start auth_XXX."""
    if token not in _tg_auth_tokens:
        return False
    entry = _tg_auth_tokens[token]
    if entry["status"] != "pending":
        return False
    entry["status"] = "authenticated"
    entry["telegram_user"] = {
        "id": telegram_user_id,
        "first_name": first_name,
        "username": username,
    }
    return True


# ── Bot → site one-time login (кнопка «Наш сайт») ───────────────────
_bot_site_login_tokens: dict[str, dict[str, Any]] = {}
BOT_SITE_LOGIN_TOKEN_TTL = 600  # 10 minutes


def _cleanup_expired_bot_site_tokens() -> None:
    now = time.time()
    expired = [
        k
        for k, v in _bot_site_login_tokens.items()
        if now - v["created"] > BOT_SITE_LOGIN_TOKEN_TTL
    ]
    for k in expired:
        del _bot_site_login_tokens[k]


def create_bot_site_login_token(
    *,
    telegram_user_id: int,
    first_name: str = "",
    username: Optional[str] = None,
) -> str:
    """Одноразовый токен для входа на сайт по ссылке из бота."""
    _cleanup_expired_bot_site_tokens()
    token = secrets.token_urlsafe(32)
    _bot_site_login_tokens[token] = {
        "status": "pending",
        "created": time.time(),
        "telegram_user": {
            "id": telegram_user_id,
            "first_name": first_name,
            "username": username,
        },
    }
    return token


app = FastAPI(
    title="Zoomer Web API",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Фронт (axios withCredentials) требует конкретный Access-Control-Allow-Origin + Allow-Credentials.
# С allow_origins=["*"] и cookies браузер скрывает ответ → в логах только OPTIONS 200, POST «Network Error».
_CORS_ORIGIN_REGEX = os.environ.get(
    "CORS_ORIGIN_REGEX",
    r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
    r"|^https://[a-z0-9-]+\.(ngrok-free\.dev|ngrok-free\.app|ngrok\.io|trycloudflare\.com|loca\.lt)$"
    r"|^https://(www\.)?4zoomer\.top$",
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Auth-Token"],
)

bearer_scheme = HTTPBearer(auto_error=False)

TARIFF_PUBLIC = [
    ("7", "7 дней", 5, False),
    ("30", "30 дней", 5, False),
    ("90", "90 дней", 5, False),
    ("120", "120 дней (акция)", 5, True),
    ("180", "180 дней", 5, False),
    ("5000", "Навсегда", 5, False),
    ("white_30", "Mobile 30 дней", 1, False),
]


def _require_jwt_secret() -> str:
    if not JWT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT_SECRET is not configured",
        )
    return JWT_SECRET


def _verify_telegram_login(data: dict[str, Any]) -> None:
    if not TG_TOKEN:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "TG_TOKEN is not configured")
    check_hash = data.get("hash")
    if not check_hash:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing hash")

    auth_date = data.get("auth_date")
    if auth_date is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing auth_date")
    try:
        ts = int(auth_date)
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid auth_date")
    if abs(int(time.time()) - ts) > 300:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "auth_date expired")

    pairs = []
    for key in sorted(data.keys()):
        if key == "hash":
            continue
        val = data[key]
        if val is None:
            continue
        sval = val if isinstance(val, str) else str(val)
        pairs.append(f"{key}={sval}")
    data_check_string = "\n".join(pairs)
    secret_key = hashlib.sha256(TG_TOKEN.encode()).digest()
    h = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256)
    if h.hexdigest() != check_hash:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Telegram hash")


def _activ_block(result: dict) -> tuple[bool, Optional[str]]:
    active = str(result.get("activ", "")).startswith("✅")
    t = result.get("time") or "-"
    expires = t if active and t != "-" else None
    return active, expires


def _tariff_parts(tariff_id: str) -> tuple[str, str, bool]:
    desc_key = tariff_id
    white = "white_" in tariff_id
    d = tariff_id
    if white:
        d = d.replace("white_", "", 1)
    if "old" in d:
        d = d.replace("old", "")
    return desc_key, d, white


def _client_is_https(request: Request) -> bool:
    """Учитывает TLS-терминацию на nginx/caddy (иначе cookie Secure ломает сессию)."""
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if proto == "https":
        return True
    if request.headers.get("x-forwarded-ssl", "").lower() == "on":
        return True
    if request.headers.get("front-end-https", "").lower() == "on":
        return True
    return request.url.scheme == "https"


def _auth_cookie_samesite_secure(request: Request) -> tuple[Literal["lax", "strict", "none"], bool]:
    """
    HTTP: без Secure, Lax — иначе браузер не сохранит cookie.
    HTTPS: None + Secure — иначе при фронте на другом домене cookie не уйдёт с fetch.
    """
    if _client_is_https(request):
        return "none", True
    return "lax", False


def _set_auth_cookie(request: Request, response, token: str) -> None:
    samesite, secure = _auth_cookie_samesite_secure(request)
    response.set_cookie(
        key="zoomer_auth",
        value=token,
        httponly=True,
        secure=secure,
        samesite=samesite,
        max_age=86400,
        path="/",
    )


def _clear_auth_cookie(request: Request, response) -> None:
    samesite, secure = _auth_cookie_samesite_secure(request)
    response.delete_cookie(
        key="zoomer_auth",
        path="/",
        secure=secure,
        httponly=True,
        samesite=samesite,
    )


def _auth_response(request: Request, token: str, user: dict, **extra) -> JSONResponse:
    body = {"token": token, "user": user, **extra}
    resp = JSONResponse(content=body)
    # Дубль для фронта: читается из JS при expose_headers (cookie может быть недоступна из-за домена/HTTPS).
    resp.headers["X-Auth-Token"] = token
    _set_auth_cookie(request, resp, token)
    return resp


async def get_jwt_context(
    request: Request,
    cred: Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearer_scheme)],
) -> dict[str, Any]:
    # Try Bearer header first, then cookie
    raw_token = None
    if cred and cred.credentials:
        raw_token = cred.credentials
    else:
        raw_token = request.cookies.get("zoomer_auth")
    if not raw_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    secret = _require_jwt_secret()
    try:
        payload = jwt.decode(raw_token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    uid = payload.get("user_id")
    if isinstance(uid, (int, float)):
        uid = int(uid)
    elif isinstance(uid, str) and uid.isdigit():
        uid = int(uid)
    else:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    auth = payload.get("auth") or "telegram"
    if auth not in ("telegram", "email"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    return {"user_id": uid, "username": payload.get("username"), "auth": auth}


JwtCtx = Annotated[dict[str, Any], Depends(get_jwt_context)]


async def _user_row_from_jwt(ctx: dict[str, Any]):
    if ctx.get("auth") == "email":
        return await sql.get_user_by_internal_id(ctx["user_id"])
    return await sql.get_user(ctx["user_id"])


async def resolve_telegram_user_id(ctx: dict[str, Any]) -> int:
    row = await _user_row_from_jwt(ctx)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    tg_col = row[1]
    linked = row[28]
    tg: Optional[int] = None
    if tg_col is not None and int(tg_col) > 0:
        tg = int(tg_col)
    elif linked is not None:
        tg = int(linked)
    if tg is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Привяжите Telegram-аккаунт для этой операции",
        )
    return tg


async def _panel_vpn_usernames(ctx: dict[str, Any]) -> tuple[str, str]:
    """
    Username в панели для pro и mobile: Telegram id (+ _white) или
    panel_username_for_site_user(user_id) для клиента только с сайта.
    """
    row = await _user_row_from_jwt(ctx)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    tg_col = row[1]
    linked = row[28]
    tg: Optional[int] = None
    if tg_col is not None and int(tg_col) > 0:
        tg = int(tg_col)
    elif linked is not None and int(linked) > 0:
        tg = int(linked)
    if tg is not None:
        s = str(tg)
        return s, f"{s}_white"
    db_uid = int(tg_col)
    return (
        panel_username_for_site_user(db_uid, False),
        panel_username_for_site_user(db_uid, True),
    )


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: Optional[str]) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def _issue_jwt(*, user_id: int, auth: str, username: Optional[str]) -> str:
    secret = _require_jwt_secret()
    exp = datetime.now(timezone.utc) + timedelta(hours=24)
    payload: dict[str, Any] = {"user_id": user_id, "auth": auth, "exp": exp}
    if username is not None:
        payload["username"] = username
    token = jwt.encode(payload, secret, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token


def _random_linking_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


def _random_reset_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _send_smtp_reset_email(to_email: str, code: str) -> None:
    if not SMTP_HOST or not SMTP_FROM:
        raise RuntimeError("SMTP not configured")
    body = f"Код для сброса пароля: {code}\n\nЕсли вы не запрашивали сброс, проигнорируйте письмо."
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = "Сброс пароля"
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
        if SMTP_USER and SMTP_PASSWORD:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASSWORD)
        s.send_message(msg)


class TelegramAuthIn(BaseModel):
    id: int
    auth_date: int
    hash: str
    first_name: str = ""
    last_name: Optional[str] = None
    username: Optional[str] = None
    photo_url: Optional[str] = None


class BotLoginIn(BaseModel):
    token: str


class CreatePaymentIn(BaseModel):
    tariff_id: str
    method: Literal["sbp", "card"]
    is_gift: bool = False


SubPageDuration = Literal["7", "30", "90", "180", "5000", "white_30"]
SUB_PAGE_PAYLOAD_SOURCE = "subpage"

sub_page_api_key_header = APIKeyHeader(
    name="X-Sub-Page-Api-Key",
    scheme_name="SubPageApiKey",
    auto_error=False,
    description="Значение из .env: SUB_PAGE_API_KEY",
)


async def require_sub_page_auth(
    request: Request,
    x_sub_page_key: Optional[str] = Security(sub_page_api_key_header),
) -> None:
    if not SUB_PAGE_API_KEY:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Не задан SUB_PAGE_API_KEY — эндпоинты страницы подписки отключены.",
        )
    if x_sub_page_key == SUB_PAGE_API_KEY:
        return
    bearer = (request.headers.get("Authorization") or "").strip()
    if bearer.lower().startswith("bearer "):
        if bearer[7:].strip() == SUB_PAGE_API_KEY:
            return
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Неверный или отсутствующий ключ страницы подписки.",
    )


SubPageAuth = Annotated[None, Depends(require_sub_page_auth)]


class SubPagePayIn(BaseModel):
    user_id: int = Field(..., description="Telegram user id")
    duration: SubPageDuration


async def _bot_deeplink_for_sub_page() -> str:
    if BOT_URL and str(BOT_URL).strip():
        return str(BOT_URL).rstrip("/")
    try:
        me = await bot.get_me()
        if me.username:
            return f"https://t.me/{me.username}"
    except Exception as e:
        logger.warning("sub_page pay: bot.get_me failed: {}", e)
    return "https://t.me/"


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=256)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class VerifyEmailIn(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class ResendCodeIn(BaseModel):
    email: EmailStr


class GoogleAuthIn(BaseModel):
    credential: str


class ResetPasswordIn(BaseModel):
    email: EmailStr


class ConfirmResetIn(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)
    new_password: str = Field(min_length=1, max_length=256)


class LinkIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


async def _deliver_reset_code(email: str, code: str, row: tuple) -> None:
    tg: Optional[int] = None
    if row[1] is not None and int(row[1]) > 0:
        tg = int(row[1])
    elif row[28] is not None:
        tg = int(row[28])
    smtp_ok = False
    if SMTP_HOST and SMTP_FROM:
        try:
            await asyncio.to_thread(_send_smtp_reset_email, email, code)
            smtp_ok = True
        except Exception as e:
            logger.warning("SMTP password reset failed: {}", e)
    if not smtp_ok and tg is not None:
        try:
            await bot.send_message(tg, f"Код сброса пароля: {code}")
        except Exception as e:
            logger.warning("Telegram password reset failed: {}", e)
    if not smtp_ok and tg is None:
        logger.warning("Password reset code for {} not delivered (configure SMTP or Telegram)", email)


@app.post("/api/auth/generate-telegram-token")
async def auth_generate_telegram_token(request: Request):
    client_ip = request.headers.get("x-real-ip", request.client.host)
    _rate_limit_or_raise(client_ip, "tg_gen", max_req=10, window=300)
    _cleanup_expired_tg_tokens()
    token = secrets.token_urlsafe(32)
    _tg_auth_tokens[token] = {
        "status": "pending",
        "telegram_user": None,
        "created": time.time(),
        "client_ip": client_ip,
    }
    deeplink = f"tg://resolve?domain={TG_TOKEN.split(':')[0]}&start=auth_{token}"
    # Also provide https link for cases where tg:// doesn't work
    bot_username = None
    try:
        bot_info = await bot.get_me()
        bot_username = bot_info.username
        deeplink = f"https://t.me/{bot_username}?start=auth_{token}"
    except Exception:
        pass
    return {"token": token, "deeplink": deeplink}


@app.get("/api/auth/check-status/{token}")
async def auth_check_status(token: str, request: Request):
    client_ip = request.headers.get("x-real-ip", request.client.host)
    _rate_limit_or_raise(client_ip, "tg_check", max_req=120, window=300)
    _cleanup_expired_tg_tokens()
    entry = _tg_auth_tokens.get(token)
    if entry is None:
        return {"status": "expired"}
    # Only the same IP that generated the token can check it
    if entry.get("client_ip") and entry["client_ip"] != client_ip:
        return {"status": "expired"}
    if entry["status"] == "pending":
        return {"status": "pending"}
    # Authenticated — issue JWT
    tg_user = entry["telegram_user"]
    uid = tg_user["id"]
    user_row = await sql.get_user(uid)
    if user_row is None:
        await sql.add_user(uid, False, False)

    jwt_token = _issue_jwt(user_id=uid, auth="telegram", username=tg_user.get("username"))

    # Clean up used token
    del _tg_auth_tokens[token]

    return _auth_response(
        request,
        jwt_token,
        {
            "id": uid,
            "first_name": tg_user.get("first_name", ""),
            "username": tg_user.get("username"),
        },
        status="authenticated",
    )


@app.post("/api/auth/bot-login")
async def auth_bot_login(body: BotLoginIn, request: Request):
    """Обмен одноразового токена из бота на JWT (страница /auth/bot)."""
    client_ip = request.headers.get("x-real-ip", request.client.host)
    _rate_limit_or_raise(client_ip, "bot_login", max_req=30, window=300)
    _cleanup_expired_bot_site_tokens()

    raw = (body.token or "").strip()
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing token")

    entry = _bot_site_login_tokens.get(raw)
    if entry is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired login link")
    if time.time() - entry["created"] > BOT_SITE_LOGIN_TOKEN_TTL:
        del _bot_site_login_tokens[raw]
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired login link")
    if entry["status"] != "pending":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired login link")

    tg_user = entry["telegram_user"]
    uid = int(tg_user["id"])
    user_row = await sql.get_user(uid)
    if user_row is None:
        await sql.add_user(uid, False, False)

    del _bot_site_login_tokens[raw]
    jwt_token = _issue_jwt(user_id=uid, auth="telegram", username=tg_user.get("username"))
    return _auth_response(
        request,
        jwt_token,
        {
            "id": uid,
            "first_name": tg_user.get("first_name", ""),
            "username": tg_user.get("username"),
        },
    )


@app.post("/api/auth/telegram")
async def auth_telegram(body: TelegramAuthIn, request: Request):
    data = body.model_dump(exclude_none=True)
    _verify_telegram_login(data)
    uid = body.id

    user_row = await sql.get_user(uid)
    if user_row is None:
        await sql.add_user(uid, False, False)

    secret = _require_jwt_secret()
    exp = datetime.now(timezone.utc) + timedelta(hours=24)
    token = jwt.encode(
        {
            "user_id": uid,
            "auth": "telegram",
            "username": body.username,
            "exp": exp,
        },
        secret,
        algorithm="HS256",
    )
    if isinstance(token, bytes):
        token = token.decode("utf-8")

    return _auth_response(
        request,
        token,
        {
            "id": uid,
            "first_name": body.first_name or "",
            "username": body.username,
            "photo_url": body.photo_url,
        },
    )


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


@app.get("/api/user/subscription")
async def user_subscription(ctx: JwtCtx):
    pro_un, white_un = await _panel_vpn_usernames(ctx)
    result_pro = await x3.activ(pro_un)
    result_white = await x3.activ(white_un)
    pa, pe = _activ_block(result_pro)
    ma, me = _activ_block(result_white)
    return {
        "pro": {"active": pa, "expires": pe},
        "mobile": {"active": ma, "expires": me},
    }


@app.get("/api/user/keys")
async def user_keys(ctx: JwtCtx):
    pro_un, white_un = await _panel_vpn_usernames(ctx)
    sub_url = await x3.sublink(pro_un)
    sub_white = await x3.sublink(white_un)
    return {
        "pro_url": sub_url or None,
        "mobile_url": sub_white or None,
    }


@app.get("/api/user/account")
async def user_account(ctx: JwtCtx):
    row = await _user_row_from_jwt(ctx)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    tg_col = row[1]
    linked = row[28]
    email = row[18]
    auth_type = ctx.get("auth", "telegram")

    tg_id: Optional[int] = None
    if tg_col is not None and int(tg_col) > 0:
        tg_id = int(tg_col)
    elif linked is not None and int(linked) > 0:
        tg_id = int(linked)

    has_telegram = tg_id is not None
    has_email = email is not None and str(email).strip() != ""

    return {
        "auth_type": auth_type,
        "has_telegram": has_telegram,
        "has_email": has_email,
        "email": email if has_email else None,
        "telegram_id": tg_id,
    }


@app.get("/api/user/referrals")
async def user_referrals(ctx: JwtCtx):
    user_id = await resolve_telegram_user_id(ctx)
    count = await sql.select_ref_count(user_id)
    base = BOT_URL.rstrip("/")
    link = f"{base}?start=ref{user_id}"
    return {"count": count, "referral_link": link}


@app.get("/api/config/tariffs")
async def config_tariffs():
    out: list[dict[str, Any]] = []
    for tid, label, devices, first_only in TARIFF_PUBLIC:
        if tid not in dct_price:
            continue
        item: dict[str, Any] = {
            "id": tid,
            "label": label,
            "price": dct_price[tid],
            "devices": devices,
        }
        if first_only:
            item["first_payment_only"] = True
        out.append(item)
    return out


@app.post("/api/trial/activate")
async def trial_activate(ctx: JwtCtx):
    if ctx.get("auth") == "email":
        row = await _user_row_from_jwt(ctx)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        email = row[18] or ctx.get("username")
        if not email:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нет email в профиле")
        em = _norm_email(str(email))
        in_panel = bool(row[4])
        if in_panel:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "Триал уже взят"},
            )
        billing_uid = int(row[1])
        panel_un = panel_username_for_site_user(billing_uid, False)
        existing_panel = await x3.get_user_by_username(panel_un)
        if existing_panel and existing_panel.get("response"):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "Триал уже взят"},
            )
        day = 5
        ok = await x3.add_client_site(day, em, False, billing_uid)
        if not ok:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Не удалось активировать триал",
            )
        logger.info("trial site user {} panel username={}", billing_uid, panel_un)
        result_active = await x3.activ(panel_un)
        time_str = result_active["time"]

        if await sql.get_user(billing_uid) is not None:
            await sql.update_in_panel(billing_uid)
        else:
            await sql.add_user(billing_uid, True)

        sub_url = await x3.sublink(panel_un)
        await post_user_trial(billing_uid)
        return {
            "success": True,
            "expires": time_str,
            "subscription_url": sub_url or None,
        }

    user_id = await resolve_telegram_user_id(ctx)
    user_data = await sql.get_user(user_id)
    in_panel = False
    if user_data is not None and len(user_data) > 4:
        in_panel = user_data[4]
    if in_panel:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "Вы уже убедились в надежности нашего VPN"},
        )

    day = 5
    logger.info(await x3.addClient(day, str(user_id), user_id))
    result_active = await x3.activ(str(user_id))
    time_str = result_active["time"]

    if await sql.get_user(user_id) is not None:
        await sql.update_in_panel(user_id)
    else:
        await sql.add_user(user_id, True)

    sub_url = await x3.sublink(str(user_id))
    await post_user_trial(user_id)
    return {
        "success": True,
        "expires": time_str,
        "subscription_url": sub_url or None,
    }


@app.post("/api/payments/create")
async def payments_create(ctx: JwtCtx, body: CreatePaymentIn):
    row = await _user_row_from_jwt(ctx)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if ctx.get("auth") == "email":
        em = row[18] or ctx.get("username")
        if not em:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нет email в профиле")
        payload_user = _norm_email(str(em))
        billing_user_id = int(row[1])
    else:
        billing_user_id = await resolve_telegram_user_id(ctx)
        payload_user = str(billing_user_id)
    tariff_id = body.tariff_id
    if tariff_id not in dct_price:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown tariff")

    desc_key, duration_str, white = _tariff_parts(tariff_id)
    price = dct_price[tariff_id]
    if billing_user_id in ADMIN_IDS:
        price = 1

    if body.method == "sbp" and (not API_FREEKASSA or SHOP_ID_FREEKASSA is None):
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "FreeKassa is not configured")
    if body.method == "card" and (not API_FREEKASSA or SHOP_ID_FREEKASSA is None):
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "FreeKassa is not configured")

    description = (
        f"Подписка в подарок {dct_desc[desc_key]}" if body.is_gift else dct_desc[desc_key]
    )

    site_uname = ctx.get("username")
    if not isinstance(site_uname, str):
        site_uname = None

    result = await pay_site(
        val=str(price),
        des=description,
        payload_user=payload_user,
        billing_user_id=billing_user_id,
        duration=duration_str,
        white=white,
        is_gift=body.is_gift,
        kind=body.method,
        telegram_username=site_uname,
    )

    if result["status"] == "rate_limited":
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            lexicon["payment_too_many_pending"].format(PAYMENT_MAX_PENDING_PER_USER),
        )
    if result["status"] != "pending":
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Не удалось создать платёж")

    return {
        "payment_url": result.get("url") or "",
        "payment_id": result.get("id") or "",
    }


@app.post("/api/v1/sub_page/pay/fk_sbp")
async def sub_page_pay_fk_sbp(body: SubPagePayIn, request: Request, _: SubPageAuth):
    _rate_limit_or_raise(_client_ip_for_rate_limit(request), "sub_page_fk_sbp", max_req=20, window=300)
    if not API_FREEKASSA or SHOP_ID_FREEKASSA is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "FreeKassa не настроена")

    desc_key, duration_str, white = _tariff_parts(body.duration)
    price = dct_price[body.duration]
    if body.user_id in ADMIN_IDS:
        price = 1

    result = await pay_site(
        val=str(price),
        des=dct_desc[desc_key],
        payload_user=str(body.user_id),
        billing_user_id=body.user_id,
        duration=duration_str,
        white=white,
        is_gift=False,
        kind="sbp",
        telegram_username=None,
        payload_source=SUB_PAGE_PAYLOAD_SOURCE,
    )
    if result["status"] == "rate_limited":
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            lexicon["payment_too_many_pending"].format(PAYMENT_MAX_PENDING_PER_USER),
        )
    if result["status"] != "pending":
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Не удалось создать платёж FreeKassa (СБП)")

    return {
        "payment_url": result.get("url") or "",
        "payment_id": result.get("id") or "",
    }


@app.post("/api/v1/sub_page/pay/fk_card")
async def sub_page_pay_fk_card(body: SubPagePayIn, request: Request, _: SubPageAuth):
    _rate_limit_or_raise(_client_ip_for_rate_limit(request), "sub_page_fk_card", max_req=20, window=300)
    if not API_FREEKASSA or SHOP_ID_FREEKASSA is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "FreeKassa не настроена")

    desc_key, duration_str, white = _tariff_parts(body.duration)
    price = dct_price[body.duration]
    if body.user_id in ADMIN_IDS:
        price = 1

    result = await pay_site(
        val=str(price),
        des=dct_desc[desc_key],
        payload_user=str(body.user_id),
        billing_user_id=body.user_id,
        duration=duration_str,
        white=white,
        is_gift=False,
        kind="card",
        telegram_username=None,
        payload_source=SUB_PAGE_PAYLOAD_SOURCE,
    )
    if result["status"] == "rate_limited":
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            lexicon["payment_too_many_pending"].format(PAYMENT_MAX_PENDING_PER_USER),
        )
    if result["status"] != "pending":
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Не удалось создать платёж FreeKassa (карта)")

    return {
        "payment_url": result.get("url") or "",
        "payment_id": result.get("id") or "",
    }


@app.post("/api/v1/sub_page/pay/stars")
async def sub_page_pay_stars(body: SubPagePayIn, request: Request, _: SubPageAuth):
    _rate_limit_or_raise(_client_ip_for_rate_limit(request), "sub_page_stars", max_req=20, window=300)

    desc_key, duration_str, white = _tariff_parts(body.duration)
    stars_amount = int(get_stars_amount("Stars", body.duration))
    if body.user_id in ADMIN_IDS:
        stars_amount = 1

    gift_flag = False
    payload = (
        f"user_id:{body.user_id},duration:{duration_str},white:{white},gift:{gift_flag},"
        f"method:stars,amount:{stars_amount},source:{SUB_PAGE_PAYLOAD_SOURCE}"
    )
    prices = [LabeledPrice(label="XTR", amount=stars_amount)]
    dur_label = "30" if duration_str == "30secret" else duration_str
    title = f"Оплата подписки на {dur_label} дней."
    description = lexicon["payment_link_white"] if white else lexicon["payment_link"]

    try:
        await bot.send_invoice(
            body.user_id,
            title=title,
            description=description,
            prices=prices,
            provider_token="",
            payload=payload,
            currency="XTR",
            reply_markup=keyboard_payment_stars(stars_amount),
        )
    except Exception as e:
        logger.error("sub_page stars send_invoice user_id={}: {}", body.user_id, e)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Не удалось отправить счёт в Telegram (возможно, бот заблокирован или нет диалога).",
        )

    bot_url = await _bot_deeplink_for_sub_page()
    return {"bot_url": bot_url, "stars_amount": stars_amount}


@app.post("/api/v1/sub_page/pay/cryptobot")
async def sub_page_pay_cryptobot(body: SubPagePayIn, request: Request, _: SubPageAuth):
    _rate_limit_or_raise(_client_ip_for_rate_limit(request), "sub_page_cryptobot", max_req=20, window=300)
    if not CRYPTOBOT_API_TOKEN:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "CryptoBot не настроен")

    desc_key, duration_str, white = _tariff_parts(body.duration)
    price = dct_price[body.duration]
    if body.user_id in ADMIN_IDS:
        price = 1

    result = await create_cryptobot_payment(
        rub_amount=price,
        description=dct_desc[desc_key],
        user_id=body.user_id,
        duration=duration_str,
        white=white,
        is_gift=False,
        telegram_username=None,
        payload_source=SUB_PAGE_PAYLOAD_SOURCE,
    )
    if result.get("status") == "rate_limited":
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            lexicon["payment_too_many_pending"].format(PAYMENT_MAX_PENDING_PER_USER),
        )
    if result.get("status") != "pending":
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Не удалось создать счёт CryptoBot")

    return {
        "payment_url": result.get("url") or "",
        "invoice_id": result.get("invoice_id"),
    }


@app.get("/api/payments/{transaction_id}/status")
async def payment_status(ctx: JwtCtx, transaction_id: str):
    row = await _user_row_from_jwt(ctx)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    billing_uid = int(row[1])
    st = await sql.get_payment_by_transaction_id(transaction_id, billing_uid)
    if st is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    return {"status": st}


@app.post("/api/gifts/{gift_id}/activate")
async def gift_activate(ctx: JwtCtx, gift_id: str):
    user_id = await resolve_telegram_user_id(ctx)
    result = await sql.activate_gift(gift_id, user_id)

    if not result[0]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=lexicon["gift_no"])

    duration = result[1]
    white_flag = result[2]

    user_id_str = str(user_id)
    if white_flag:
        user_id_str += "_white"

    was_in_db = await sql.get_user(user_id) is not None
    if not was_in_db:
        await sql.add_user(user_id, False)

    existing_user = await x3.get_user_by_username(user_id_str)
    if existing_user and "response" in existing_user and existing_user["response"]:
        response = await x3.updateClient(duration, user_id_str, user_id)
    else:
        response = await x3.addClient(duration, user_id_str, user_id)

    if not response:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=lexicon["gift_error"])

    result_active = await x3.activ(user_id_str)
    subscription_time = result_active.get("time", "-")
    await sql.update_in_panel(user_id)

    return {
        "success": True,
        "days_added": duration,
        "expires": subscription_time,
    }


def _send_smtp_verification_email(to_email: str, code: str) -> None:
    if not SMTP_HOST or not SMTP_FROM:
        raise RuntimeError("SMTP not configured")
    body = f"Ваш код подтверждения: {code}\n\nКод действителен 15 минут."
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = "Подтверждение email — ZoomerVPN"
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
        if SMTP_USER and SMTP_PASSWORD:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASSWORD)
        s.send_message(msg)


async def _send_verification_code(email: str) -> str:
    code = _random_reset_code()
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    activation_value = f"{code}:{int(expires.timestamp())}"
    await sql.set_activation_pass_by_email(email, activation_value)
    try:
        await asyncio.to_thread(_send_smtp_verification_email, email, code)
    except Exception as e:
        logger.warning("SMTP verification email failed: {}", e)
    return code


@app.post("/api/auth/register")
async def auth_register(body: RegisterIn, request: Request):
    client_ip = request.headers.get("x-real-ip", request.client.host)
    _rate_limit_or_raise(client_ip, "register", max_req=5, window=300)
    existing = await sql.get_user_by_email(str(body.email))
    if existing:
        email_verified = bool(existing[24])
        if email_verified:
            raise HTTPException(status.HTTP_409_CONFLICT, "Email уже зарегистрирован")
        # Not verified yet — resend code
        await _send_verification_code(str(body.email))
        return {"success": True, "requires_verification": True, "email": str(body.email).strip().lower()}
    h = _hash_password(body.password)
    internal_id = await sql.register_email_user(str(body.email), h)
    em = str(body.email).strip().lower()
    await _send_verification_code(em)
    return {"success": True, "requires_verification": True, "email": em}


@app.post("/api/auth/verify-email")
async def auth_verify_email(body: VerifyEmailIn, request: Request):
    client_ip = request.headers.get("x-real-ip", request.client.host)
    _rate_limit_or_raise(client_ip, "verify", max_req=10, window=300)
    if not body.code.isdigit() or len(body.code) != 6:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неверный код")
    row = await sql.get_user_by_email(str(body.email))
    if row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пользователь не найден")
    activation = row[20]
    if not activation or ":" not in str(activation):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Код не был отправлен")
    stored_code, expires_ts = str(activation).rsplit(":", 1)
    try:
        if int(time.time()) > int(expires_ts):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Код истёк, запросите новый")
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неверный код")
    if stored_code != body.code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неверный код")
    internal_id = int(row[0])
    await sql.set_email_verified(internal_id, True)
    await sql.set_activation_pass_by_email(str(body.email), None)
    em = row[18] or str(body.email).strip().lower()
    token = _issue_jwt(user_id=internal_id, auth="email", username=em)
    return _auth_response(request, token, {"id": internal_id, "email": em}, success=True)


@app.post("/api/auth/resend-code")
async def auth_resend_code(body: ResendCodeIn, request: Request):
    client_ip = request.headers.get("x-real-ip", request.client.host)
    _rate_limit_or_raise(client_ip, "resend", max_req=3, window=300)
    row = await sql.get_user_by_email(str(body.email))
    if row is None:
        return {"success": True}
    if bool(row[24]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email уже подтверждён")
    await _send_verification_code(str(body.email))
    return {"success": True}


@app.post("/api/auth/google")
async def auth_google(body: GoogleAuthIn, request: Request):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google login not configured")
    # Verify Google ID token via Google's tokeninfo endpoint
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://oauth2.googleapis.com/tokeninfo?id_token={body.credential}"
        ) as resp:
            if resp.status != 200:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Google token")
            payload = await resp.json()

    if payload.get("aud") != GOOGLE_CLIENT_ID:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Google token audience")

    google_email = payload.get("email")
    if not google_email or not payload.get("email_verified"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Google email not verified")

    em = google_email.strip().lower()

    # Check if user exists
    row = await sql.get_user_by_email(em)
    if row is None:
        # Create new user, already verified (Google verified email)
        h = _hash_password(secrets.token_hex(32))  # random password
        internal_id = await sql.register_email_user(em, h)
        await sql.set_email_verified(internal_id, True)
    else:
        internal_id = int(row[0])
        # Ensure email_verified is set
        if not bool(row[24]):
            await sql.set_email_verified(internal_id, True)

    token = _issue_jwt(user_id=internal_id, auth="email", username=em)
    return _auth_response(
        request,
        token,
        {
            "id": internal_id,
            "email": em,
            "first_name": payload.get("given_name", ""),
            "photo_url": payload.get("picture"),
        },
    )


@app.post("/api/auth/login")
async def auth_login(body: LoginIn, request: Request):
    client_ip = request.headers.get("x-real-ip", request.client.host)
    _rate_limit_or_raise(client_ip, "login", max_req=10, window=300)
    row = await sql.get_user_by_email(str(body.email))
    if row is None or not _verify_password(body.password, row[27]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный email или пароль")
    email_verified = bool(row[24])
    if not email_verified:
        await _send_verification_code(str(body.email))
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": "Email не подтверждён", "requires_verification": True, "email": str(body.email).strip().lower()},
        )
    internal_id = int(row[0])
    em = row[18] or str(body.email).strip().lower()
    token = _issue_jwt(user_id=internal_id, auth="email", username=em)
    return _auth_response(request, token, {"id": internal_id, "email": em})


@app.post("/api/auth/reset-password")
async def auth_reset_password(body: ResetPasswordIn):
    row = await sql.get_user_by_email(str(body.email))
    if row is None:
        return {"success": True}
    code = _random_reset_code()
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    await sql.replace_password_reset_codes(str(body.email), code, expires)
    await _deliver_reset_code(str(body.email), code, row)
    return {"success": True}


@app.post("/api/auth/confirm-reset")
async def auth_confirm_reset(body: ConfirmResetIn, request: Request):
    client_ip = request.headers.get("x-real-ip", request.client.host)
    _rate_limit_or_raise(client_ip, "reset_confirm", max_req=10, window=300)
    if not body.code.isdigit() or len(body.code) != 6:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid code")
    if not await sql.verify_password_reset_code(str(body.email), body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired code")
    row = await sql.get_user_by_email(str(body.email))
    if row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired code")
    await sql.set_password_hash_by_internal_id(int(row[0]), _hash_password(body.new_password))
    await sql.delete_password_reset_codes_for_email(str(body.email))
    return {"success": True}


@app.post("/api/auth/generate-linking-code")
async def auth_generate_linking_code(ctx: JwtCtx):
    row = await _user_row_from_jwt(ctx)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    code = ""
    last_err: Optional[Exception] = None
    for _ in range(12):
        code = _random_linking_code()
        try:
            await sql.replace_linking_code(int(row[0]), code, expires)
            last_err = None
            break
        except IntegrityError as e:
            last_err = e
            continue
    if last_err is not None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Could not generate code")
    return {"success": True, "linkingCode": code}


@app.post("/api/auth/link")
async def auth_link(ctx: JwtCtx, body: LinkIn):
    if ctx.get("auth") != "email":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Доступно только для входа по email",
        )
    row_e = await sql.get_user_by_internal_id(ctx["user_id"])
    if row_e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if row_e[1] is not None and int(row_e[1]) > 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Telegram уже привязан")

    raw = body.code.strip().upper()
    hit = await sql.get_valid_linking_code(raw)
    if hit is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неверный или просроченный код")
    code_id, creator_internal_id = hit
    if creator_internal_id == row_e[0]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нельзя использовать свой код")

    creator = await sql.get_user_by_internal_id(creator_internal_id)
    if creator is None:
        await sql.delete_linking_code_by_id(code_id)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неверный или просроченный код")
    if creator[1] is None or int(creator[1]) < 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Отправьте этот код боту в Telegram",
        )

    ok = await sql.merge_email_placeholder_into_telegram(row_e[0], int(creator[1]))
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Не удалось объединить аккаунты")
    await sql.delete_linking_code_by_id(code_id)
    return {"success": True, "linkedTelegramId": int(creator[1])}


@app.get("/api/auth/me")
async def auth_me(ctx: JwtCtx):
    return await user_profile(ctx)


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    resp = JSONResponse(content={"success": True})
    _clear_auth_cookie(request, resp)
    return resp


@app.get("/api/user/profile")
async def user_profile(ctx: JwtCtx):
    if ctx.get("auth") == "email":
        user = await sql.get_user_object_by_internal_id(int(ctx["user_id"]))
    else:
        user = await sql.get_user_object_by_user_id(int(ctx["user_id"]))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user_row_to_api_dict(user)


@app.post("/api/user/change-password")
async def user_change_password(ctx: JwtCtx, body: ChangePasswordIn):
    row = await _user_row_from_jwt(ctx)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if not row[27]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пароль не установлен")
    if not _verify_password(body.current_password, row[27]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неверный текущий пароль")
    await sql.set_password_hash_by_internal_id(int(row[0]), _hash_password(body.new_password))
    return {"success": True}
