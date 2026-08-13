"""Блокировка покупки мобильной (white) подписки."""


def is_mobile_tariff_key(key: str) -> bool:
    """True для white_30, r_white_30, gift_r_white_30 и аналогичных ключей."""
    return "white" in key


def normalize_tariff_duration_key(key: str) -> str:
    """30old → 30; остальные ключи (в т.ч. 5000sale) без изменений."""
    if key.endswith("old"):
        return key[:-3]
    return key
