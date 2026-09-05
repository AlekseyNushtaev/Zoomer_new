"""Landing site API — /api/landing/* (OTP email, Google, tariffs, payments)."""
from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal, Optional
from urllib.parse import urlparse

import aiohttp
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field

from bot import sql
from config import (
    ADMIN_IDS,
    JWT_SECRET,
    LANDING_GOOGLE_CLIENT_ID,
    LANDING_SITE_URL,
    PAYMENT_MAX_PENDING_PER_USER,
    PLATEGA_API_KEY,
    PLATEGA_MERCHANT_ID,
)
from config_bd.utils import _norm_email
from lexicon import dct_desc, dct_price, lexicon
from logging_config import logger
from payments.payload_source import SITE
from payments.pay_platega import pay_site_card, pay_site_sbp
from services.unisender import send_email as send_unisender_email

landing_router = APIRouter(prefix="/api/landing", tags=["landing"])

LANDING_JWT_MAX_AGE = 365 * 24 * 3600  # 1 year
LANDING_COOKIE = "landing_auth"

TARIFF_PUBLIC = [
    ("7", "7 дней", 5, False),
    ("30", "30 дней", 5, False),
    ("90", "90 дней", 5, False),
    ("180", "180 дней", 5, False),
    ("365", "365 дней", 5, False),
    ("5000", "Навсегда", 5, False),
]

_rate_limits: dict[str, list[float]] = {}
bearer_scheme = HTTPBearer(auto_error=False)


def _rate_check(key: str, max_requests: int, window_sec: int) -> bool:
    now = time.time()
    timestamps = [t for t in _rate_limits.get(key, []) if now - t < window_sec]
    if len(timestamps) >= max_requests:
        _rate_limits[key] = timestamps
        return False
    timestamps.append(now)
    _rate_limits[key] = timestamps
    return True


def _rate_limit_or_raise(request_ip: str, action: str, max_req: int = 5, window: int = 300):
    key = f"landing:{action}:{request_ip}"
    if not _rate_check(key, max_req, window):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много попыток. Подождите 5 минут.")


def _site_url_from_request(request: Request) -> Optional[str]:
    origin = (request.headers.get("origin") or "").strip()
    if origin:
        return origin.rstrip("/")
    referer = (request.headers.get("referer") or "").strip()
    if referer:
        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return None


def _client_ip(request: Request) -> str:
    x_real = (request.headers.get("x-real-ip") or "").strip()
    if x_real:
        return x_real
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if xff:
        return xff
    return request.client.host if request.client else ""


def _client_is_https(request: Request) -> bool:
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if proto == "https":
        return True
    return request.url.scheme == "https"


def _cookie_params(request: Request) -> tuple[Literal["lax", "none"], bool]:
    if _client_is_https(request):
        return "none", True
    return "lax", False


def _require_jwt_secret() -> str:
    if not JWT_SECRET:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "JWT_SECRET is not configured")
    return JWT_SECRET


def _issue_landing_jwt(*, user_id: int, auth: str, username: Optional[str]) -> str:
    secret = _require_jwt_secret()
    exp = datetime.now(timezone.utc) + timedelta(seconds=LANDING_JWT_MAX_AGE)
    payload: dict[str, Any] = {
        "user_id": user_id,
        "auth": auth,
        "site": "landing",
        "exp": exp,
    }
    if username is not None:
        payload["username"] = username
    token = jwt.encode(payload, secret, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token


def _set_landing_cookie(request: Request, response, token: str) -> None:
    samesite, secure = _cookie_params(request)
    response.set_cookie(
        key=LANDING_COOKIE,
        value=token,
        httponly=True,
        secure=secure,
        samesite=samesite,
        max_age=LANDING_JWT_MAX_AGE,
        path="/",
    )


def _clear_landing_cookie(request: Request, response) -> None:
    samesite, secure = _cookie_params(request)
    response.delete_cookie(key=LANDING_COOKIE, path="/", secure=secure, httponly=True, samesite=samesite)


def _auth_response(request: Request, token: str, user: dict, **extra) -> JSONResponse:
    body = {"token": token, "user": user, **extra}
    resp = JSONResponse(content=body)
    resp.headers["X-Auth-Token"] = token
    _set_landing_cookie(request, resp, token)
    return resp


async def get_landing_jwt_context(
    request: Request,
    cred: Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearer_scheme)],
) -> dict[str, Any]:
    raw_token = None
    if cred and cred.credentials:
        raw_token = cred.credentials
    else:
        raw_token = request.cookies.get(LANDING_COOKIE)
    if not raw_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    secret = _require_jwt_secret()
    try:
        payload = jwt.decode(raw_token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    if payload.get("site") != "landing":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    uid = payload.get("user_id")
    if isinstance(uid, (int, float)):
        uid = int(uid)
    elif isinstance(uid, str) and uid.isdigit():
        uid = int(uid)
    else:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    auth = payload.get("auth") or "email"
    if auth not in ("email", "google"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    return {"user_id": uid, "username": payload.get("username"), "auth": auth}


LandingCtx = Annotated[dict[str, Any], Depends(get_landing_jwt_context)]


def _random_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


async def _send_landing_otp(email: str) -> None:
    code = _random_otp_code()
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    activation_value = f"{code}:{int(expires.timestamp())}"
    await sql.set_landing_activation_pass_by_email(email, activation_value)
    body = f"Ваш код для входа: {code}\n\nКод действителен 15 минут."
    try:
        await send_unisender_email(
            to_email=email,
            subject="Код входа — Зумерский VPN",
            text=body,
        )
    except Exception as e:
        logger.warning("Landing OTP email failed: {}", e)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Не удалось отправить код на почту. Попробуйте позже.",
        ) from e


def _landing_user_dict(user, site) -> dict[str, Any]:
    return {
        "id": int(user.id),
        "email": site.email,
        "auth": "google" if site.google_sub else "email",
        "billing_user_id": int(user.user_id),
    }


def _tariff_parts(tariff_id: str) -> tuple[str, str, bool]:
    white = "white" in tariff_id
    desc_key = tariff_id.replace("_white", "")
    return desc_key, desc_key, white


def _reject_mobile_purchase(tariff_id: str) -> None:
    if _tariff_parts(tariff_id)[2]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, lexicon["mobile_purchase_disabled"])


class EmailIn(BaseModel):
    email: EmailStr


class VerifyCodeIn(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class GoogleAuthIn(BaseModel):
    credential: str


class CreatePaymentIn(BaseModel):
    tariff_id: str
    method: Literal["sbp", "card"]
    is_gift: bool = False


@landing_router.post("/auth/send-code")
async def landing_send_code(body: EmailIn, request: Request):
    _rate_limit_or_raise(_client_ip(request), "send-code", max_req=5, window=300)
    em = str(body.email).strip().lower()
    existing = await sql.get_landing_user_by_email(em)
    if existing is None:
        await sql.register_landing_email_user(em, site_url=_site_url_from_request(request))
    await _send_landing_otp(em)
    return {"success": True, "email": em}


@landing_router.post("/auth/verify-code")
async def landing_verify_code(body: VerifyCodeIn, request: Request):
    _rate_limit_or_raise(_client_ip(request), "verify-code", max_req=10, window=300)
    if not body.code.isdigit() or len(body.code) != 6:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неверный код")
    pair = await sql.get_landing_user_by_email(str(body.email))
    if pair is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пользователь не найден")
    user, site = pair
    activation = site.activation_pass
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
    internal_id = int(user.id)
    await sql.set_landing_email_verified(internal_id, True)
    await sql.set_landing_activation_pass_by_email(str(body.email), None)
    em = site.email or str(body.email).strip().lower()
    token = _issue_landing_jwt(user_id=internal_id, auth="email", username=em)
    return _auth_response(request, token, _landing_user_dict(user, site), success=True)


@landing_router.post("/auth/resend-code")
async def landing_resend_code(body: EmailIn, request: Request):
    _rate_limit_or_raise(_client_ip(request), "resend-code", max_req=3, window=300)
    em = str(body.email).strip().lower()
    pair = await sql.get_landing_user_by_email(em)
    if pair is None:
        return {"success": True}
    await _send_landing_otp(em)
    return {"success": True}


@landing_router.post("/auth/google")
async def landing_google(body: GoogleAuthIn, request: Request):
    if not LANDING_GOOGLE_CLIENT_ID:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google login not configured")
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://oauth2.googleapis.com/tokeninfo?id_token={body.credential}"
        ) as resp:
            if resp.status != 200:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Google token")
            payload = await resp.json()
    if payload.get("aud") != LANDING_GOOGLE_CLIENT_ID:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Google token audience")
    google_email = payload.get("email")
    google_sub = payload.get("sub")
    if not google_email or not payload.get("email_verified") or not google_sub:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Google email not verified")
    em = google_email.strip().lower()
    pair = await sql.get_landing_user_by_google_sub(google_sub)
    if pair is None:
        by_email = await sql.get_landing_user_by_email(em)
        if by_email:
            user, site = by_email
            internal_id = int(user.id)
        else:
            internal_id = await sql.register_landing_google_user(
                em, google_sub, site_url=_site_url_from_request(request)
            )
            pair = await sql.get_landing_user_by_internal_id(internal_id)
            if pair is None:
                raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "User creation failed")
            user, site = pair
    else:
        user, site = pair
    internal_id = int(user.id)
    token = _issue_landing_jwt(user_id=internal_id, auth="google", username=em)
    return _auth_response(request, token, _landing_user_dict(user, site), success=True)


@landing_router.post("/auth/logout")
async def landing_logout(request: Request):
    resp = JSONResponse(content={"success": True})
    _clear_landing_cookie(request, resp)
    return resp


@landing_router.get("/auth/me")
async def landing_me(ctx: LandingCtx):
    pair = await sql.get_landing_user_by_internal_id(ctx["user_id"])
    if pair is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user, site = pair
    return _landing_user_dict(user, site)


@landing_router.get("/config/tariffs")
async def landing_tariffs():
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


@landing_router.post("/payments/create")
async def landing_payments_create(ctx: LandingCtx, body: CreatePaymentIn):
    pair = await sql.get_landing_user_by_internal_id(ctx["user_id"])
    if pair is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user, site = pair
    billing_user_id = int(user.user_id)
    payload_user = str(billing_user_id)
    tariff_id = body.tariff_id
    if tariff_id not in dct_price:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown tariff")
    _reject_mobile_purchase(tariff_id)
    desc_key, duration_str, white = _tariff_parts(tariff_id)
    price = dct_price[tariff_id]
    if billing_user_id in ADMIN_IDS:
        price = 1
    if body.method == "sbp" and (not PLATEGA_API_KEY or not PLATEGA_MERCHANT_ID):
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Platega is not configured")
    if body.method == "card" and (not PLATEGA_API_KEY or not PLATEGA_MERCHANT_ID):
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Platega is not configured")
    description = (
        f"Подписка в подарок {dct_desc[desc_key]}" if body.is_gift else dct_desc[desc_key]
    )
    success_url = f"{LANDING_SITE_URL}/success"
    fail_url = f"{LANDING_SITE_URL}/checkout"
    site_uname = ctx.get("username") if isinstance(ctx.get("username"), str) else None
    if body.method == "card":
        result = await pay_site_card(
            val=str(price),
            des=description,
            payload_user=payload_user,
            billing_user_id=billing_user_id,
            duration=duration_str,
            white=white,
            is_gift=body.is_gift,
            telegram_username=site_uname,
            payload_source=SITE,
            return_url=success_url,
            failed_url=fail_url,
        )
    else:
        result = await pay_site_sbp(
            val=str(price),
            des=description,
            payload_user=payload_user,
            billing_user_id=billing_user_id,
            duration=duration_str,
            white=white,
            is_gift=body.is_gift,
            telegram_username=site_uname,
            payload_source=SITE,
            return_url=success_url,
            failed_url=fail_url,
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


@landing_router.get("/payments/{transaction_id}/status")
async def landing_payment_status(ctx: LandingCtx, transaction_id: str):
    pair = await sql.get_landing_user_by_internal_id(ctx["user_id"])
    if pair is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    billing_uid = int(pair[0].user_id)
    st = await sql.get_payment_by_transaction_id(transaction_id, billing_uid)
    if st is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    return {"status": st}
