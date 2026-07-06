"""One-off: overlay «Вы выиграли!» on the Zoomer promo image."""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(
    r"C:\Users\nusht\.cursor\projects\c-Users-nusht-PycharmProjects-PortfolioFreelance-BotForSale-Elvis-Zoomer"
    r"\assets\c__Users_nusht_AppData_Roaming_Cursor_User_workspaceStorage_a5e09776bbab656d4fb9cc87c04f212e_images_"
    r"zoomer-b41b94f8-13c9-403a-971e-c1a88b39c10f.png"
)
OUT = ROOT / "assets" / "discount_push_winner.png"


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for fp in (
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\calibrib.ttf",
    ):
        if os.path.exists(fp):
            return ImageFont.truetype(fp, size)
    return ImageFont.load_default()


def main() -> None:
    img = Image.open(SRC).convert("RGBA")
    w, h = img.size

    bar_height = 200
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw_ov = ImageDraw.Draw(overlay)
    for i in range(bar_height):
        y = h - bar_height + i
        alpha = int(230 * (i + 1) / bar_height)
        draw_ov.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(img, overlay)

    text = "Вы выиграли!"
    font = _load_font(78)
    text_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    td = ImageDraw.Draw(text_layer)

    bbox = td.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (w - tw) // 2
    ty = h - bar_height + (bar_height - th) // 2 + 8

    # Полная заливка: сплошной неоново-зелёный без прозрачности и свечения
    td.text((tx, ty), text, font=font, fill=(0, 255, 100, 255))

    img = Image.alpha_composite(img, text_layer)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(OUT, "PNG", optimize=True)
    print(f"Saved: {OUT} ({img.size[0]}x{img.size[1]})")


if __name__ == "__main__":
    main()
