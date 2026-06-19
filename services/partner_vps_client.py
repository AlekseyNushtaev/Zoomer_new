from typing import Any, Dict, Optional

import asyncio
import time

import aiohttp

from config import PARTNER_VPS_API_KEY, PARTNER_VPS_IP
from logging_config import logger


class PartnerVpsError(Exception):
    pass


_RUNNING_STATUSES = frozenset({"running", "active", "ready", "ok", "up", "started", "deployed"})


def _is_running_status(data: Dict[str, Any]) -> bool:
    if data.get("ready") is True or data.get("running") is True:
        return True
    status = str(data.get("status") or data.get("state") or "").lower()
    return status in _RUNNING_STATUSES


def _headers() -> Dict[str, str]:
    if not PARTNER_VPS_API_KEY:
        raise PartnerVpsError("PARTNER_VPS_API_KEY not configured")
    return {"X-Api-Key": PARTNER_VPS_API_KEY, "Content-Type": "application/json"}


async def deploy_bot(
    bot_id: int,
    token: str,
    partner_tg_id: int,
    bot_username: str,
) -> Dict[str, Any]:
    if not PARTNER_VPS_IP:
        raise PartnerVpsError("PARTNER_VPS_IP not configured")
    url = f"{PARTNER_VPS_IP}/bots/deploy"
    body = {
        "bot_id": bot_id,
        "token": token,
        "partner_tg_id": partner_tg_id,
        "bot_username": bot_username.lstrip("@"),
    }
    logger.info(
        "partner VPS deploy request: bot_id={} partner_tg_id={} username=@{}",
        bot_id,
        partner_tg_id,
        bot_username.lstrip("@"),
    )
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body, headers=_headers(), timeout=aiohttp.ClientTimeout(total=120)) as resp:
            data = await resp.json()
            logger.info(
                "partner VPS deploy response: bot_id={} http_status={} body={}",
                bot_id,
                resp.status,
                data,
            )
            if resp.status >= 400:
                detail = data.get("detail", data)
                logger.error(
                    "partner VPS deploy failed: bot_id={} http_status={} detail={}",
                    bot_id,
                    resp.status,
                    detail,
                )
                raise PartnerVpsError(str(detail))
            return data


async def wait_bot_running(
    bot_id: int,
    deploy_result: Optional[Dict[str, Any]] = None,
    *,
    timeout: float = 90.0,
    interval: float = 3.0,
) -> Dict[str, Any]:
    """Ожидает подтверждение от VPS, что бот запущен."""
    if deploy_result and _is_running_status(deploy_result):
        logger.info("partner VPS bot ready from deploy response: bot_id={}", bot_id)
        return deploy_result

    logger.info("partner VPS waiting for bot start: bot_id={} timeout={}s", bot_id, int(timeout))
    deadline = time.monotonic() + timeout
    last: Dict[str, Any] = deploy_result or {}
    while time.monotonic() < deadline:
        try:
            last = await bot_status(bot_id)
            logger.info("partner VPS bot status poll: bot_id={} data={}", bot_id, last)
            if _is_running_status(last):
                logger.info("partner VPS bot running: bot_id={}", bot_id)
                return last
        except PartnerVpsError as e:
            logger.warning("partner VPS bot status poll error: bot_id={} err={}", bot_id, e)
        await asyncio.sleep(interval)

    if deploy_result:
        logger.warning(
            "partner VPS wait timeout, using deploy response: bot_id={} last={}",
            bot_id,
            last,
        )
        return deploy_result
    raise PartnerVpsError(
        f"бот #{bot_id} не перешёл в running за {int(timeout)} с (последний ответ: {last})"
    )


async def stop_bot(bot_id: int) -> Dict[str, Any]:
    return await _post(f"/bots/{bot_id}/stop")


async def restart_bot(bot_id: int) -> Dict[str, Any]:
    return await _post(f"/bots/{bot_id}/restart")


async def bot_status(bot_id: int) -> Dict[str, Any]:
    return await _get(f"/bots/{bot_id}/status")


async def bot_stats(bot_id: int) -> Dict[str, Any]:
    return await _get(f"/bots/{bot_id}/stats")


async def _post(path: str) -> Dict[str, Any]:
    if not PARTNER_VPS_IP:
        raise PartnerVpsError("PARTNER_VPS_IP not configured")
    url = f"{PARTNER_VPS_IP}{path}"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=_headers(), timeout=aiohttp.ClientTimeout(total=60)) as resp:
            data = await resp.json()
            if resp.status >= 400:
                raise PartnerVpsError(str(data.get("detail", data)))
            return data


async def _get(path: str) -> Dict[str, Any]:
    if not PARTNER_VPS_IP:
        raise PartnerVpsError("PARTNER_VPS_IP not configured")
    url = f"{PARTNER_VPS_IP}{path}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=_headers(), timeout=aiohttp.ClientTimeout(total=30)) as resp:
            data = await resp.json()
            if resp.status >= 400:
                raise PartnerVpsError(str(data.get("detail", data)))
            return data
