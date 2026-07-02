"""Блокировка покупки мобильной (white) подписки."""


def is_mobile_tariff_key(key: str) -> bool:
    """True для white_30, r_white_30, gift_r_white_30 и аналогичных ключей."""
    return "white" in key
