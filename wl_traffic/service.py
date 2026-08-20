"""Бизнес-логика лимита трафика Антиглушилка."""
from __future__ import annotations

import asyncio
import random
from datetime import date, datetime, timedelta
from typing import Optional

from X3 import panel_username_for_site_user

from wl_traffic.constants import (
    FOREVER_DURATION_DAYS,
    FOREVER_END_CUTOFF,
    WL_DAY_RESET_HOUR,
    WL_GB_PER_MONTH,
    WL_LEGACY_RETRIES,
    WL_LOW_TRAFFIC_WARNING_GB,
    WL_NODE_NAME,
    WL_SQUAD_ACTIVE,
    WL_SQUAD_LIMITED,
    WL_SUBSCRIPTION_MONTHS,
    WL_TIMEZONE,
)

_BYTES_PER_GB = 1024 ** 3


def bytes_to_gb(value: float) -> float:
    return round(value / _BYTES_PER_GB, 2)


def wl_traffic_day(now: datetime | None = None) -> date:
    """Текущий WL-день (МСК): до 03:00 — предыдущий календарный день."""
    if now is None:
        now = datetime.now(WL_TIMEZONE)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=WL_TIMEZONE)
    else:
        now = now.astimezone(WL_TIMEZONE)
    day = now.date()
    if now.hour < WL_DAY_RESET_HOUR:
        day -= timedelta(days=1)
    return day


def is_wl_check_skip_window(now: datetime | None = None) -> bool:
    """02:57–03:05 МСК — окно ежедневного накопления trafic_wl, проверку лимита пропускаем."""
    if now is None:
        now = datetime.now(WL_TIMEZONE)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=WL_TIMEZONE)
    else:
        now = now.astimezone(WL_TIMEZONE)
    if now.hour == 2 and now.minute >= 57:
        return True
    if now.hour == 3 and now.minute <= 5:
        return True
    return False


def is_forever_duration(duration_days: int) -> bool:
    """Тариф «Навсегда» (5000+ дней)."""
    return int(duration_days) >= FOREVER_DURATION_DAYS


def is_forever_end_date(end_date: datetime | date | None) -> bool:
    """Подписка «Навсегда»: дата окончания с 2030 года и далее."""
    if end_date is None:
        return False
    if isinstance(end_date, date) and not isinstance(end_date, datetime):
        return end_date >= FOREVER_END_CUTOFF.date()
    dt = end_date
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.replace(tzinfo=None)
    return dt >= FOREVER_END_CUTOFF


def subscription_bonus_gb(duration_days: int) -> float:
    if duration_days == 7:
        return 3.0
    if is_forever_duration(duration_days):
        return float(WL_GB_PER_MONTH)
    months = WL_SUBSCRIPTION_MONTHS.get(duration_days, max(0, duration_days // 30))
    return float(months * WL_GB_PER_MONTH)


async def credit_wl_subscription_bonus(sql, user_id: int, duration_days: int) -> None:
    """Начисляет WL-трафик по тарифу подписки (как при оплате)."""
    bonus_gb = subscription_bonus_gb(duration_days)
    if bonus_gb > 0:
        await sql.add_wl_limit(user_id, bonus_gb)


def parse_traffic_duration(duration: str) -> Optional[int]:
    """duration:traffic10 -> 10 GB; иначе None."""
    if not duration.startswith("traffic"):
        return None
    try:
        return int(duration.replace("traffic", ""))
    except ValueError:
        return None


def extract_squad_uuids(panel_user: dict) -> list[str]:
    raw = panel_user.get("activeInternalSquads") or []
    uuids: list[str] = []
    for s in raw:
        if isinstance(s, dict):
            uuids.append(str(s.get("uuid", "")))
        else:
            uuids.append(str(s))
    return [u for u in uuids if u]


def user_on_limited_squad(panel_user: dict) -> bool:
    squads = set(extract_squad_uuids(panel_user))
    return bool(squads & set(WL_SQUAD_LIMITED))


def user_on_active_squad(panel_user: dict) -> bool:
    """Сквад с белой нодой (Антиглушилка)."""
    squads = set(extract_squad_uuids(panel_user))
    return bool(squads & set(WL_SQUAD_ACTIVE))


def panel_username_for_billing_uid(billing_uid: int, white: bool = False) -> str:
    if billing_uid <= 0:
        return panel_username_for_site_user(billing_uid, white)
    return f"{billing_uid}_white" if white else str(billing_uid)


async def resolve_panel_username(sql, billing_uid: int, white: bool = False) -> str:
    """Username в панели: TG id, site negative id, либо gift_N из field_str_2."""
    if billing_uid > 0:
        return panel_username_for_billing_uid(billing_uid, white)
    if sql is not None:
        user = await sql.get_user(billing_uid)
        if user is not None and len(user) > 22:
            stamp = user[14]
            field_str_2 = user[22]
            if stamp == "gift" and field_str_2:
                base = str(field_str_2)
                return f"{base}_white" if white else base
    return panel_username_for_site_user(billing_uid, white)


def billing_uid_from_panel_username(username: str) -> Optional[int]:
    if not username or username.endswith("_white"):
        return None
    try:
        return int(username)
    except ValueError:
        if username.startswith("n"):
            try:
                return int(username[1:])
            except ValueError:
                return None
        return None


def _record_date(item: dict) -> Optional[date]:
    raw = item.get("date")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def filter_records_for_day(records: list[dict], day: date) -> list[dict]:
    filtered = [r for r in records if _record_date(r) == day]
    return filtered if filtered else records


def aggregate_bandwidth_by_username(records: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        username = str(item.get("username") or "")
        if not username:
            continue
        totals[username] = totals.get(username, 0.0) + float(item.get("total") or 0)
    return totals


def aggregate_bandwidth_by_user_uuid(records: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        user_uuid = str(item.get("userId") or item.get("userUuid") or "")
        if not user_uuid:
            continue
        totals[user_uuid] = totals.get(user_uuid, 0.0) + float(item.get("total") or 0)
    return totals


async def fetch_wl_traffic_gb_for_day(
    x3,
    day: date | None = None,
    *,
    retries: int = WL_LEGACY_RETRIES,
) -> tuple[dict[str, float], dict[str, float]]:
    """
    Bulk legacy за один WL-день: (by_username_gb, by_user_uuid_gb).
    retries — повторы при пустом ответе (для крона накопления).
    """
    day = day or wl_traffic_day()
    day_str = day.isoformat()

    node_uuid = await x3.get_node_uuid_by_name(WL_NODE_NAME)
    if not node_uuid:
        return {}, {}

    for attempt in range(max(1, retries)):
        records = await x3.get_node_users_bandwidth_legacy(node_uuid, day_str, day_str)
        if records:
            filtered = filter_records_for_day(records, day)
            by_username = {
                u: bytes_to_gb(b) for u, b in aggregate_bandwidth_by_username(filtered).items()
            }
            by_uuid = {
                u: bytes_to_gb(b) for u, b in aggregate_bandwidth_by_user_uuid(filtered).items()
            }
            return by_username, by_uuid
        if attempt < retries - 1:
            await asyncio.sleep(2.0 * (attempt + 1))

    return {}, {}


def wl_traffic_gb_for_panel_user(
    panel_user: dict,
    traffic_by_username: dict[str, float],
    traffic_by_uuid: dict[str, float],
) -> float:
    """Расход за WL-день из bulk-мапы: сначала по id панели, затем по username."""
    panel_user_id = panel_user.get("id")
    if panel_user_id is not None:
        key = str(panel_user_id)
        if key in traffic_by_uuid:
            return traffic_by_uuid[key]

    username = str(panel_user.get("username") or "")
    if username and username in traffic_by_username:
        return traffic_by_username[username]

    return 0.0


def compute_wl_used_gb(trafic_wl_db: float, day_gb: float) -> float:
    """Итого использовано: накопленный trafic_wl + расход за текущий WL-день."""
    return round(float(trafic_wl_db or 0.0) + float(day_gb or 0.0), 2)


def should_send_wl_low_traffic_warning(used_gb: float, limit_gb: float) -> bool:
    """used < limit и до исчерпания осталось меньше 1 GB."""
    return (
        used_gb < limit_gb
        and used_gb + WL_LOW_TRAFFIC_WARNING_GB > limit_gb
    )


async def get_wl_used_gb_for_user(
    x3,
    billing_uid: int,
    trafic_wl_db: float,
    *,
    day: date | None = None,
    traffic_by_username: dict[str, float] | None = None,
    traffic_by_uuid: dict[str, float] | None = None,
    sql=None,
) -> float:
    """trafic_wl из БД + расход за WL-день с панели."""
    if traffic_by_username is None or traffic_by_uuid is None:
        traffic_by_username, traffic_by_uuid = await fetch_wl_traffic_gb_for_day(
            x3, day, retries=1,
        )

    panel_user = await fetch_panel_user(x3, billing_uid, sql=sql)
    if not panel_user:
        return round(float(trafic_wl_db or 0.0), 2)

    day_gb = wl_traffic_gb_for_panel_user(panel_user, traffic_by_username, traffic_by_uuid)
    return compute_wl_used_gb(trafic_wl_db, day_gb)


async def reassign_squad(x3, panel_user: dict, pool: tuple[str, ...]) -> bool:
    panel_user_id = x3._panel_user_id(panel_user)
    if panel_user_id is None:
        return False
    squad = [random.choice(pool)]
    return await x3.update_user_squads(panel_user_id, squad)


async def reassign_to_active_squad(x3, panel_user: dict) -> bool:
    return await reassign_squad(x3, panel_user, WL_SQUAD_ACTIVE)


async def reassign_to_limited_squad(x3, panel_user: dict) -> bool:
    return await reassign_squad(x3, panel_user, WL_SQUAD_LIMITED)


async def fetch_panel_user(
    x3,
    billing_uid: int,
    white: bool = False,
    sql=None,
) -> Optional[dict]:
    if sql is not None:
        username = await resolve_panel_username(sql, billing_uid, white)
    else:
        username = panel_username_for_billing_uid(billing_uid, white)
    data = await x3.get_user_by_username(username)
    if not data:
        return None
    return x3._panel_user_from_response(data)
