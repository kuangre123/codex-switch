#!/usr/bin/env python3
"""Generate the Codex Switch macOS app icon."""

from __future__ import annotations

import math
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "assets"
ICONSET = ASSET_DIR / "app-icon.iconset"
ICNS = ASSET_DIR / "app-icon.icns"


def rounded_rectangle_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def gradient(size: int, start: tuple[int, int, int], end: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGB", (size, size), start)
    pixels = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            pixels[x, y] = tuple(round(start[i] * (1 - t) + end[i] * t) for i in range(3))
    return img


def ring_segment_mask(size: int, bbox: tuple[int, int, int, int], width: int, start: int, end: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.arc(bbox, start=start, end=end, fill=255, width=width)
    radius = width // 2
    for angle in (start, end):
        cx = (bbox[0] + bbox[2]) / 2
        cy = (bbox[1] + bbox[3]) / 2
        rx = (bbox[2] - bbox[0]) / 2
        ry = (bbox[3] - bbox[1]) / 2
        radians = math.radians(angle)
        x = cx + rx * math.cos(radians)
        y = cy + ry * math.sin(radians)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)
    return mask


def make_icon(size: int) -> Image.Image:
    scale = size / 1024
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    bg = gradient(size, (17, 24, 39), (8, 15, 30)).convert("RGBA")
    bg.putalpha(rounded_rectangle_mask(size, round(220 * scale)))
    canvas.alpha_composite(bg)

    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.ellipse(
        tuple(round(v * scale) for v in (164, 190, 860, 858)),
        outline=(0, 0, 0, 80),
        width=max(1, round(132 * scale)),
    )
    canvas.alpha_composite(shadow)

    bbox = tuple(round(v * scale) for v in (206, 206, 818, 818))
    width = max(2, round(118 * scale))
    mint = gradient(size, (88, 240, 194), (45, 166, 255)).convert("RGBA")
    amber = gradient(size, (255, 178, 74), (255, 107, 61)).convert("RGBA")

    mint_mask = ring_segment_mask(size, bbox, width, 132, 408)
    amber_mask = ring_segment_mask(size, bbox, width, -48, 228)
    canvas.alpha_composite(Image.composite(mint, Image.new("RGBA", (size, size), (0, 0, 0, 0)), mint_mask))
    canvas.alpha_composite(Image.composite(amber, Image.new("RGBA", (size, size), (0, 0, 0, 0)), amber_mask))

    draw = ImageDraw.Draw(canvas)
    white = (248, 250, 252, 255)
    line_width = max(2, round(74 * scale))
    draw.line(
        [(round(352 * scale), round(512 * scale)), (round(672 * scale), round(512 * scale))],
        fill=white,
        width=line_width,
        joint="curve",
    )
    arrow = [
        (round(628 * scale), round(420 * scale)),
        (round(728 * scale), round(512 * scale)),
        (round(628 * scale), round(604 * scale)),
    ]
    draw.line(arrow, fill=white, width=line_width, joint="curve")
    cap_radius = line_width // 2
    for x, y in [(352, 512), (672, 512), (628, 420), (728, 512), (628, 604)]:
        cx = round(x * scale)
        cy = round(y * scale)
        draw.ellipse((cx - cap_radius, cy - cap_radius, cx + cap_radius, cy + cap_radius), fill=white)

    return canvas


def save_iconset() -> None:
    if ICONSET.exists():
        shutil.rmtree(ICONSET)
    ICONSET.mkdir(parents=True)
    outputs = [
        ("icon_16x16.png", 16),
        ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32),
        ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128),
        ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256),
        ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512),
        ("icon_512x512@2x.png", 1024),
    ]
    for filename, size in outputs:
        make_icon(size).save(ICONSET / filename)


def main() -> int:
    save_iconset()
    if not shutil.which("iconutil"):
        print("iconutil is required on macOS", file=sys.stderr)
        return 2
    subprocess.run(["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS)], check=True)
    print(ICNS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

