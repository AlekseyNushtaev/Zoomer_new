"""Unisender Go transactional email (HTTP API, not SMTP)."""
from __future__ import annotations

import html as html_lib
from typing import Any, Optional

import aiohttp

from config import SMTP_FROM, UNISENDER_API_KEY, UNISENDER_API_URL, UNISENDER_FROM_NAME

_SEND_URL = f"{UNISENDER_API_URL}/email/send.json"
_TIMEOUT = aiohttp.ClientTimeout(total=20)


def is_configured() -> bool:
    return bool(UNISENDER_API_KEY and SMTP_FROM)


def _html_from_text(text: str) -> str:
    escaped = html_lib.escape(text).replace("\n", "<br>\n")
    return f"<p>{escaped}</p>"


def _payload(*, to_email: str, subject: str, text: str, from_name: str, skip_unsubscribe: int) -> dict[str, Any]:
    return {
        "message": {
            "recipients": [{"email": to_email}],
            "from_email": SMTP_FROM,
            "from_name": from_name,
            "subject": subject,
            "body": {
                "plaintext": text,
                "html": _html_from_text(text),
            },
            "skip_unsubscribe": skip_unsubscribe,
            "template_engine": "none",
            "global_language": "ru",
        }
    }


async def _post_send(payload: dict[str, Any]) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-API-KEY": UNISENDER_API_KEY or "",
    }
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.post(_SEND_URL, json=payload, headers=headers) as resp:
            try:
                data = await resp.json(content_type=None)
            except Exception:
                raw = await resp.text()
                raise RuntimeError(f"Unisender HTTP {resp.status}: {raw[:500]}") from None
            if resp.status != 200:
                raise RuntimeError(f"Unisender HTTP {resp.status}: {data}")
            if not isinstance(data, dict):
                raise RuntimeError(f"Unisender unexpected response: {data}")
            if data.get("status") != "success":
                raise RuntimeError(f"Unisender error: {data}")
            failed = data.get("failed_emails") or {}
            if failed:
                raise RuntimeError(f"Unisender rejected recipient: {failed}")
            return data


async def send_email(
    *,
    to_email: str,
    subject: str,
    text: str,
    from_name: Optional[str] = None,
) -> None:
    if not is_configured():
        raise RuntimeError("Unisender is not configured (UNISENDER_API_KEY / SMTP_FROM)")
    name = (from_name or UNISENDER_FROM_NAME).strip() or "Зумерский VPN"
    try:
        await _post_send(_payload(to_email=to_email, subject=subject, text=text, from_name=name, skip_unsubscribe=1))
    except RuntimeError as e:
        if "skip_unsubscribe" not in str(e).lower():
            raise
        await _post_send(_payload(to_email=to_email, subject=subject, text=text, from_name=name, skip_unsubscribe=0))
