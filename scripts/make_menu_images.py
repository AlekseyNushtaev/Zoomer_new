"""Generate menu banner images: black background, white title text."""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "assets" / "menu"

TITLES = (
    "Профиль",
    "Управление подпиской",
    "Покупка подписки",
    "Покупка трафика",
    "Управление устройствами",
    "Наш сайт",
    "Зарабатывай с нами",
    "О сервисе",
    "FAQ",
)

SIZE = (1280, 720)
BG = (0, 0, 0)
FG = (255, 255, 255)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for fp in (
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\calibrib.ttf",
    ):
        if os.path.exists(fp):
            return ImageFont.truetype(fp, size)
    return ImageFont.load_default()


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for size in range(96, 24, -4):
        font = _load_font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return font
    return _load_font(36)


def _slug(title: str) -> str:
    mapping = {
        "Профиль": "profile",
        "Управление подпиской": "subscription_manage",
        "Покупка подписки": "buy_subscription",
        "Покупка трафика": "buy_traffic",
        "Управление устройствами": "manage_devices",
        "Наш сайт": "our_site",
        "Зарабатывай с нами": "earn_with_us",
        "О сервисе": "about_service",
        "FAQ": "faq",
    }
    return mapping[title]


def render(title: str, path: Path) -> None:
    img = Image.new("RGB", SIZE, BG)
    draw = ImageDraw.Draw(img)
    font = _fit_font(draw, title, SIZE[0] - 120)
    bbox = draw.textbbox((0, 0), title, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (SIZE[0] - tw) // 2
    ty = (SIZE[1] - th) // 2 - bbox[1]
    draw.text((tx, ty), title, font=font, fill=FG)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)
    print(f"Saved: {path}")


def main() -> None:
    for title in TITLES:
        render(title, OUT_DIR / f"{_slug(title)}.png")


if __name__ == "__main__":
    main()
