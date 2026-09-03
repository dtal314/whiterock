"""Derive the shipped logo files from site/assets/logo_raw.png.

Flattens any transparency onto pure white, trims to the rock with a margin,
and writes logo.png (512), logo-192.png, apple-touch-icon.png (180) and
favicon.ico (16..64).  Run from the project folder: python tools/make_icons.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops

ASSETS = Path(__file__).resolve().parent.parent / "site" / "assets"
RAW = ASSETS / "logo_raw.png"


def flatten_white(img: Image.Image) -> Image.Image:
    img = img.convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.alpha_composite(img)
    return bg.convert("RGB")


def trim(img: Image.Image, margin_frac: float = 0.12) -> Image.Image:
    bg = Image.new("RGB", img.size, (255, 255, 255))
    diff = ImageChops.difference(img, bg).convert("L").point(lambda v: 255 if v > 12 else 0)
    box = diff.getbbox()
    if not box:
        return img
    l, t, r, b = box
    side = max(r - l, b - t)
    m = int(side * margin_frac)
    side += 2 * m
    cx, cy = (l + r) // 2, (t + b) // 2
    canvas = Image.new("RGB", (side, side), (255, 255, 255))
    canvas.paste(img.crop((l - m, t - m, l - m + side, t - m + side)) if False else img,
                 (side // 2 - cx, side // 2 - cy))
    return canvas


def main() -> None:
    img = trim(flatten_white(Image.open(RAW)))
    for name, size in (("logo.png", 512), ("logo-192.png", 192), ("apple-touch-icon.png", 180)):
        img.resize((size, size), Image.Resampling.LANCZOS).save(ASSETS / name, optimize=True)
    sizes = [16, 24, 32, 48, 64]
    frames = [img.resize((s, s), Image.Resampling.LANCZOS) for s in sizes]
    frames[-1].save(ASSETS / "favicon.ico", format="ICO", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    print("wrote", sorted(p.name for p in ASSETS.iterdir()))


if __name__ == "__main__":
    main()
