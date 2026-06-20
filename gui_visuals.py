"""Canvas-drawn brand badges and chart accents for the native GUI."""

from __future__ import annotations

import math
from typing import Any

GUI_APP_BRAND: dict[str, dict[str, str]] = {
    "codex": {
        "label": "Codex",
        "accent": "#9d7cff",
        "glow": "#6d4aff",
        "surface": "#1a1528",
    },
    "claude": {
        "label": "Claude",
        "accent": "#f59e6c",
        "glow": "#e07a45",
        "surface": "#241812",
    },
    "cursor": {
        "label": "Cursor",
        "accent": "#36cfe8",
        "glow": "#0ea5c7",
        "surface": "#0f1a20",
    },
    "all": {
        "label": "All apps",
        "accent": "#64748b",
        "glow": "#475569",
        "surface": "#151820",
    },
}


def app_key_from_label(label: str) -> str:
    normalized = str(label or "").strip().lower()
    if "codex" in normalized:
        return "codex"
    if "claude" in normalized:
        return "claude"
    if "cursor" in normalized:
        return "cursor"
    if "all" in normalized:
        return "all"
    return "all"


def brand_for_app(app_key: str) -> dict[str, str]:
    return GUI_APP_BRAND.get(app_key, GUI_APP_BRAND["all"])


def draw_accent_header_strip(
    canvas: Any,
    *,
    width: int,
    height: int = 5,
    bg: str = "#0b0c0f",
) -> None:
    canvas.delete("all")
    canvas.configure(height=height, bg=bg, highlightthickness=0, bd=0)
    if width <= 0:
        return
    keys = ("codex", "claude", "cursor")
    segment = max(width // len(keys), 1)
    for index, app_key in enumerate(keys):
        accent = brand_for_app(app_key)["accent"]
        x0 = index * segment
        x1 = width if index == len(keys) - 1 else (index + 1) * segment
        canvas.create_rectangle(x0, 0, x1, height, fill=accent, outline="")


def draw_app_badge(
    canvas: Any,
    *,
    app_key: str,
    size: int = 44,
    bg: str = "#151820",
) -> None:
    canvas.delete("all")
    canvas.configure(
        width=size,
        height=size,
        bg=bg,
        highlightthickness=0,
        bd=0,
    )
    brand = brand_for_app(app_key)
    pad = max(2, size // 14)
    outer = size - pad
    canvas.create_oval(
        pad,
        pad,
        outer,
        outer,
        fill=brand["surface"],
        outline=brand["accent"],
        width=max(2, size // 18),
    )
    inner_pad = pad + max(4, size // 7)
    inner = size - inner_pad
    canvas.create_oval(
        inner_pad,
        inner_pad,
        inner,
        inner,
        fill=brand["glow"],
        outline="",
    )

    cx = size / 2
    cy = size / 2
    if app_key == "codex":
        radius = size * 0.17
        for angle_index in range(6):
            angle = angle_index * 60 - 90
            rad = math.radians(angle)
            x = cx + radius * math.cos(rad)
            y = cy + radius * math.sin(rad)
            dot = max(2, size // 16)
            canvas.create_oval(x - dot, y - dot, x + dot, y + dot, fill="#f8fafc", outline="")
    elif app_key == "claude":
        arm = size * 0.18
        for angle_index in range(8):
            angle = angle_index * 45
            rad = math.radians(angle)
            x = cx + arm * math.cos(rad)
            y = cy + arm * math.sin(rad)
            canvas.create_line(cx, cy, x, y, fill="#fff7ed", width=max(2, size // 18), capstyle="round")
        canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill="#fff7ed", outline="")
    elif app_key == "cursor":
        pointer = size * 0.2
        canvas.create_polygon(
            cx - pointer * 0.55,
            cy - pointer * 0.9,
            cx + pointer * 0.95,
            cy + pointer * 0.15,
            cx - pointer * 0.05,
            cy + pointer * 0.15,
            fill="#ecfeff",
            outline="",
        )
        canvas.create_rectangle(
            cx - pointer * 0.55,
            cy + pointer * 0.05,
            cx + pointer * 0.35,
            cy + pointer * 0.75,
            fill="#ecfeff",
            outline="",
        )
    else:
        canvas.create_text(
            cx,
            cy,
            text="∞",
            fill="#eef2f7",
            font=("Segoe UI Semibold", max(10, size // 3)),
        )


def draw_token_mix_bar(
    canvas: Any,
    *,
    width: int,
    height: int = 8,
    usage: dict[str, int] | None,
    accent: str,
    subtle: str,
    muted: str,
    bg: str,
) -> None:
    canvas.delete("all")
    canvas.configure(width=width, height=height + 14, bg=bg, highlightthickness=0, bd=0)
    usage = usage or {}
    input_tokens = max(int(usage.get("input_tokens") or 0), 0)
    cached_tokens = max(int(usage.get("cached_input_tokens") or 0), 0)
    output_tokens = max(int(usage.get("output_tokens") or 0), 0)
    total = input_tokens + cached_tokens + output_tokens
    track_y = 0
    canvas.create_rectangle(0, track_y, width, track_y + height, fill=subtle, outline="")
    if total <= 0:
        canvas.create_text(0, track_y + height + 10, anchor="w", text="No token mix yet", fill=muted, font=("Segoe UI", 8))
        return

    segments = [
        (input_tokens, accent),
        (cached_tokens, "#67e8f9"),
        (output_tokens, "#3ecf8e"),
    ]
    x = 0
    for amount, color in segments:
        if amount <= 0:
            continue
        segment_width = max(2, int((amount / total) * width))
        canvas.create_rectangle(x, track_y, x + segment_width, track_y + height, fill=color, outline="")
        x += segment_width
    legend = f"In {input_tokens:,}  ·  Cached {cached_tokens:,}  ·  Out {output_tokens:,}"
    canvas.create_text(0, track_y + height + 10, anchor="w", text=legend, fill=muted, font=("Segoe UI", 8))


def draw_share_donut(
    canvas: Any,
    *,
    size: int,
    slices: list[tuple[str, int, str]],
    bg: str,
    muted: str,
) -> None:
    canvas.delete("all")
    canvas.configure(width=size, height=size, bg=bg, highlightthickness=0, bd=0)
    total = sum(max(value, 0) for _, value, _ in slices)
    if total <= 0:
        canvas.create_text(
            size / 2,
            size / 2,
            text="No usage",
            fill=muted,
            font=("Segoe UI", 9),
        )
        return

    pad = max(8, size // 10)
    bbox = (pad, pad, size - pad, size - pad)
    start = 90
    for _label, value, color in slices:
        if value <= 0:
            continue
        extent = -360 * (value / total)
        canvas.create_arc(bbox, start=start, extent=extent, fill=color, outline=bg, width=2, style="pieslice")
        start += extent

    inner = size * 0.34
    canvas.create_oval(
        size / 2 - inner,
        size / 2 - inner,
        size / 2 + inner,
        size / 2 + inner,
        fill=bg,
        outline="",
    )
    canvas.create_text(
        size / 2,
        size / 2,
        text=f"{total:,}",
        fill="#eef2f7",
        font=("Segoe UI Semibold", max(9, size // 11)),
    )
