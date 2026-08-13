"""Тексты с данными по трафику Антиглушилка."""
from __future__ import annotations

from lexicon import lexicon

from wl_traffic.service import (
    is_forever_duration,
    is_forever_end_date,
    subscription_bonus_gb,
)


def format_pro_payment_link(duration_days: int) -> str:
    bonus = subscription_bonus_gb(duration_days)
    wl_bonus = ""
    if bonus > 0:
        if is_forever_duration(duration_days):
            wl_bonus = lexicon["wl_bonus_line_forever"].format(gb=bonus)
        else:
            wl_bonus = lexicon["wl_bonus_line"].format(gb=bonus)
    return lexicon["payment_link"].format(wl_bonus=wl_bonus)


def format_wl_limit_exceeded(
    limit_gb: float,
    used_gb: float,
    *,
    forever: bool = False,
) -> str:
    base = lexicon["wl_limit_exceeded"].format(
        limit_gb=limit_gb,
        used_gb=used_gb,
    )
    if forever:
        return base + lexicon["wl_forever_limit_note"]
    return base


def format_wl_traffic_low_warning(
    limit_gb: float,
    used_gb: float,
    *,
    forever: bool = False,
) -> str:
    base = lexicon["wl_traffic_low_warning"].format(
        limit_gb=limit_gb,
        used_gb=used_gb,
    )
    if forever:
        return base + lexicon["wl_forever_limit_note"]
    return base


def format_wl_forever_monthly_credit() -> str:
    return lexicon["wl_forever_monthly_credit"]


def format_wl_checker_exceeded_report(
    exceeded: list[tuple[int, float, float]],
) -> str:
    lines = [
        f"{user_id} - {used_gb:.2f} GB - {limit_gb:.2f} GB"
        for user_id, used_gb, limit_gb in exceeded
    ]
    return "📡 WL: превышение лимита\n\n" + "\n".join(lines)


def format_wl_checker_traffic_purchase(
    user_id: int,
    gb: int,
    used_gb: float,
    limit_gb: float,
) -> str:
    return (
        f"{user_id} купил трафик {gb} GB\n"
        f"Текущий расход - {used_gb:.2f} GB\n"
        f"Текущий лимит - {limit_gb:.2f} GB"
    )


def forever_flag_from_end_date(end_date) -> bool:
    return is_forever_end_date(end_date)
