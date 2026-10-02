#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成应用图标 assets/icon.ico（indigo→pink 渐变底 + 换色箭头圆环）。
用法: python tools/make_icon.py
产物: assets/icon.ico 以及 web/static/favicon.ico（页面图标）。
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent

INDIGO = (99, 102, 241)   # #6366f1（与 README frontmatter colorFrom 一致）
PINK = (236, 72, 153)     # #ec4899（colorTo）
WHITE = (255, 255, 255)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make(size=256):
    S = size * 4  # 4x 超采样后缩小，抗锯齿
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角方形渐变底
    radius = int(S * 0.22)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=radius, fill=255)
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        c = lerp(INDIGO, PINK, y / (S - 1))
        gd.line([(0, y), (S, y)], fill=c + (255,))
    img.paste(grad, (0, 0), mask)

    # 白色圆环（换色循环）
    cx, cy, r = S / 2, S / 2, S * 0.30
    w = int(S * 0.075)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=WHITE + (255,), width=w)
    # 箭头：两个半圆弧段 + 三角箭头，分别用对比色
    import math

    def arc_arrow(color, start_deg):
        d.arc([cx - r, cy - r, cx + r, cy + r], start=start_deg, end=start_deg + 150,
              fill=color + (255,), width=w)
        a = math.radians(start_deg)
        ax, ay = cx + r * math.cos(a), cy + r * math.sin(a)
        t = math.radians(start_deg - 90)  # 切线方向
        s = S * 0.075
        d.polygon([
            (ax + s * math.cos(t + 0.5), ay + s * math.sin(t + 0.5)),
            (ax + s * math.cos(t - 0.5), ay + s * math.sin(t - 0.5)),
            (ax + s * 1.5 * math.cos(t), ay + s * 1.5 * math.sin(t)),
        ], fill=color + (255,))

    arc_arrow(lerp(PINK, WHITE, 0.65), 200)
    arc_arrow(lerp(INDIGO, WHITE, 0.35), 20)

    return img.resize((size, size), Image.LANCZOS)


def main():
    img = make(256)
    out_assets = ROOT / "assets"
    out_assets.mkdir(exist_ok=True)
    img.save(out_assets / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.save(ROOT / "web" / "static" / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    img.save(out_assets / "icon.png")
    print("OK ->", out_assets / "icon.ico", "and web/static/favicon.ico")


if __name__ == "__main__":
    main()
