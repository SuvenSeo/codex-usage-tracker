#!/usr/bin/env python3
"""Generate bundled GUI brand PNGs from official favicons and local app icons."""

from __future__ import annotations

import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "assets" / "gui" / "brands"

CURSOR_CANDIDATES = [
    Path.home() / "AppData/Local/Programs/cursor/resources/app/out/vs/glass/browser/media/cursor-splash-logo-normal.png",
    Path.home() / "AppData/Local/Programs/Cursor/resources/app/out/vs/glass/browser/media/cursor-splash-logo-normal.png",
    Path.home() / "AppData/Local/Programs/cursor/resources/app/resources/win32/code.ico",
    Path.home() / "AppData/Local/Programs/Cursor/resources/app/resources/win32/code.ico",
]

DOWNLOADS = {
    "openai.ico": "https://openai.com/favicon.ico",
    "anthropic.ico": "https://claude.ai/favicon.ico",
    "cursor-web.ico": "https://cursor.com/favicon.ico",
}

OUTPUTS = {
    "codex.png": "openai.ico",
    "claude.png": "anthropic.ico",
    "cursor.png": "cursor-web.ico",
}


def fetch_url(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "codex-usage-tracker/0.2.5"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def largest_frame(path: Path) -> Image.Image:
    image = Image.open(path)
    frames: list[Image.Image] = []
    try:
        for index in range(getattr(image, "n_frames", 1)):
            image.seek(index)
            frames.append(image.copy())
    except EOFError:
        pass
    if not frames:
        return image.convert("RGBA")
    return max(frames, key=lambda frame: frame.size[0] * frame.size[1]).convert("RGBA")


def square_icon(image: Image.Image, size: int = 128) -> Image.Image:
    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    cropped = image.crop((left, top, left + side, top + side))
    return cropped.resize((size, size), Image.Resampling.LANCZOS)


def resolve_cursor_source() -> Path:
    for candidate in CURSOR_CANDIDATES:
        if candidate.exists():
            return candidate
    return OUT_DIR / "cursor-web.ico"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in DOWNLOADS.items():
        target = OUT_DIR / name
        target.write_bytes(fetch_url(url))
        print(f"downloaded {name} ({target.stat().st_size} bytes)")

    source_map = {
        "codex.png": OUT_DIR / "openai.ico",
        "claude.png": OUT_DIR / "anthropic.ico",
        "cursor.png": resolve_cursor_source(),
    }

    for output_name, source_path in source_map.items():
        if not source_path.exists():
            raise FileNotFoundError(f"Missing brand source: {source_path}")
        icon = square_icon(largest_frame(source_path))
        target = OUT_DIR / output_name
        icon.save(target, format="PNG")
        print(f"saved {target} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
