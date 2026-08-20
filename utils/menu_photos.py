"""Telegram file_id for menu screens — profile depends on bot username."""
from __future__ import annotations

from typing import Optional

from aiogram import Bot

from logging_config import logger

ZOOMER_BOT_USERNAME = "zoomerskyvpn_bot"

PHOTO_KEYS = (
    "profile",
    "subscription_manage",
    "buy_subscription",
    "buy_traffic",
    "manage_devices",
    "our_site",
    "earn_with_us",
    "about_service",
    "faq",
)

_MENU_PHOTOS_ZOOMER = {
    "buy_subscription": "AgACAgIAAxkBAAHGzB9qheO1f9UEJInxkBwacBCNvT0v6gACkBtrG-ZnMEilVjh20iYxRwEAAwIAA3kAAz0E",
    "earn_with_us": "AgACAgQAAxkBAAHGzCdqheRQE-VU0hVheN2-Eo27sT20WAACghBrGy9oMFB5Qrc4sbkkCwEAAwIAA3kAAz0E",
    "our_site": "AgACAgQAAxkBAAHGzClqheRrIcXjsEhtx2ekkk9jsjc_zwACgxBrGy9oMFCmB7v2qss0fgEAAwIAA3kAAz0E",
    "about_service": "AgACAgQAAxkBAAHGzCtqheSIdmH-v7SzkNImoJZICtfNtwAChBBrGy9oMFBPYmc6t61ZgAEAAwIAA3kAAz0E",
    "buy_traffic": "AgACAgQAAxkBAAHGzC5qheShyYIQpb3M69UIE1iK1D0engAChRBrGy9oMFB9vjt8X7tWdAEAAwIAA3kAAz0E",
    "profile": "AgACAgQAAxkBAAHGzDBqheS5JppQOR5_wkSQY1b9p3-08wAChhBrGy9oMFCdMNtGwRWh8AEAAwIAA3kAAz0E",
    "manage_devices": "AgACAgQAAxkBAAHGzDJqheTOXDD2pAXlzLcXh_33til3GgAChxBrGy9oMFCJBaUGllvc-QEAAwIAA3kAAz0E",
    "subscription_manage": "AgACAgQAAxkBAAHGzDVqheTt-3Fxx8FpQhxHxDQPpEEdVAACiBBrGy9oMFCyjmq4VaWu1AEAAwIAA3kAAz0E",
    "faq": "AgACAgQAAxkBAAHGzExqheUBkQGCZFCPU7H_3lCJCSDldgACiRBrGy9oMFB04GytLshfygEAAwIAA3kAAz0E",
}

_MENU_PHOTOS_DEFAULT = {
    "buy_subscription": "AgACAgIAAxkBAAIJ9mqF5lUJIMEIDUKqKt25pCvtTIIhAAKQG2sb5mcwSNCCgIGv1Lq7AQADAgADeQADPQQ",
    "earn_with_us": "AgACAgQAAxkBAAIJ-GqF5l5s0D1uhpUqZbLYCpZktCRGAAKCEGsbL2gwUN0L29QwE5_wAQADAgADeQADPQQ",
    "our_site": "AgACAgQAAxkBAAIJ-mqF5mPe5fVuEQ9h1lyUh994jKvxAAKDEGsbL2gwUMpW0qFtheXcAQADAgADeQADPQQ",
    "about_service": "AgACAgQAAxkBAAIJ_GqF5mlaZcV698JtvbwScW5h7-eGAAKEEGsbL2gwUINcuOa9gIPTAQADAgADeQADPQQ",
    "buy_traffic": "AgACAgQAAxkBAAIJ_mqF5m-FAzniHDyW323svhcMz3MdAAKFEGsbL2gwUIS2TBsj9C2HAQADAgADeQADPQQ",
    "profile": "AgACAgQAAxkBAAIKAAFqheZ0XGgiFMf8WjhALiGaVSm9mgAChhBrGy9oMFCh9IGWnfa5SgEAAwIAA3kAAz0E",
    "manage_devices": "AgACAgQAAxkBAAIKBGqF5n9sAzq_L_FBwGtWX8kyPSOxAAKHEGsbL2gwUP-SRGUMz3mQAQADAgADeQADPQQ",
    "subscription_manage": "AgACAgQAAxkBAAIKAmqF5nnhVgrW_COw5IB1byECnlYKAAKIEGsbL2gwUGnZVzBK5AXEAQADAgADeQADPQQ",
    "faq": "AgACAgQAAxkBAAIKBmqF5oimmahlUDb2p9MZ5v5z7auzAAKJEGsbL2gwUEVW7xzsvBGIAQADAgADeQADPQQ",
}

_IMPORT_PHOTOS_ZOOMER = {
    "incy": [
        "AgACAgQAAxkBAAGKrdNqQiUf_p1S_47iMASx26z1h-vIYwADDmsbah0YUqdKC9q7VzbCAQADAgADeAADPAQ",
        "AgACAgQAAxkBAAGKrd1qQiU5wcFKAX2nsvvka2PvqxfZoAACAQ5rG2odGFIhi-_aVyh4JwEAAwIAA3gAAzwE",
    ],
    "happ": [
        "AgACAgIAAxkBAAEQ72Rpu6TFlYB57q-1ovQZamC8oCuvIwACSRdrG_ly2ElqTaWIZs_b5wEAAwIAA3kAAzoE",
        "AgACAgIAAxkBAAEQ72Zpu6TTtTiuL0Z1lFD3v9pFrjcyyQACShdrG_ly2EnjX6j31mWvqwEAAwIAA3kAAzoE",
    ],
    "v2": [
        "AgACAgIAAxkBAAEQ73Npu6UVieJU3Bd-TaeF-lhHFaam5AACTRdrG_ly2El7qYfhiDllAAEBAAMCAAN5AAM6BA",
        "AgACAgIAAxkBAAEQ73Vpu6UdeEpyg_2bF0v4BqGiqs2MdQACThdrG_ly2EnTgHMXZPyj_QEAAwIAA3kAAzoE",
        "AgACAgIAAxkBAAEQ73tpu6UvSnw8j_IJRSGhIRwpVBGz2AACTxdrG_ly2EkeG-IsWsAVkQEAAwIAA3kAAzoE",
    ],
}

_IMPORT_PHOTOS_DEFAULT = {
    "incy": [
        "AgACAgQAAxkBAAIJ6GqF5XXsK4vcxDAdoD1STfZ2BFkgAALWEWsbwKwoUN9jMr-TPCdoAQADAgADeAADPQQ",
        "AgACAgQAAxkBAAIJ6mqF5XvYi7yeIuXgQzqd8eJxJi4-AALXEWsbwKwoUMGDtiEI4jZ1AQADAgADeAADPQQ",
    ],
    "happ": [
        "AgACAgQAAxkBAAIJ7GqF5asZxbktqgMLOh9d-PMBDpmJAALYEWsbwKwoULUWwVveBAIWAQADAgADeQADPQQ",
        "AgACAgQAAxkBAAIJ7mqF5bDEMLiMyjVIYG6XofvOT6LgAALZEWsbwKwoUKhgggwn6Rw8AQADAgADeQADPQQ",
    ],
    "v2": [
        "AgACAgQAAxkBAAIJ8GqF5fR2nOUAAa-vey4Ib00GQ9svqgAC2hFrG8CsKFBtHU1zvp6h5AEAAwIAA3kAAz0E",
        "AgACAgQAAxkBAAIJ8mqF5frh3XxDYeprn2EidMqFBqzBAALbEWsbwKwoUNf8Lzeo5C3IAQADAgADeQADPQQ",
        "AgACAgQAAxkBAAIJ9GqF5gABLGTb9h42LfpKR0DKGsIcewAC3BFrG8CsKFCmJlJPlGjBTgEAAwIAA3kAAz0E",
    ],
}

_cached_username: Optional[str] = None


def _username_from_bot_url() -> str:
    from config import BOT_URL

    slug = (BOT_URL or "").rstrip("/").split("/")[-1]
    return slug.lstrip("@").lower()


def _active_bot_username() -> str:
    return (_cached_username or _username_from_bot_url()).lower()


def is_zoomer_bot() -> bool:
    return _active_bot_username() == ZOOMER_BOT_USERNAME


def _menu_photos_map() -> dict[str, str]:
    if is_zoomer_bot():
        return _MENU_PHOTOS_ZOOMER
    return _MENU_PHOTOS_DEFAULT


def _import_photos_map() -> dict[str, list[str]]:
    if is_zoomer_bot():
        return _IMPORT_PHOTOS_ZOOMER
    return _IMPORT_PHOTOS_DEFAULT


def menu_photo(key: str) -> str:
    photos = _menu_photos_map()
    if key not in photos:
        raise KeyError(f"Unknown menu photo key: {key}")
    return photos[key]


def import_photos(app_key: str) -> list[str]:
    photos = _import_photos_map()
    if app_key not in photos:
        raise KeyError(f"Unknown import photo app key: {app_key}")
    return photos[app_key]


async def init_menu_photos(bot: Bot) -> None:
    global _cached_username
    me = await bot.get_me()
    if me and me.username:
        _cached_username = me.username.lower()
        profile = "zoomer" if is_zoomer_bot() else "default"
        logger.info("Menu photos: @{} ({})", _cached_username, profile)
