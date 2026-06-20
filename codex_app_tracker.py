#!/usr/bin/env python3
"""
Local AI coding usage tracker.

Reads Codex desktop/app rollout logs, Claude Code transcripts, and Cursor AI
tracking metadata, generates CSV/JSON/HTML reports, and can send conservative
WakaTime "ai coding" heartbeats for recent Codex app activity.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import http.server
import json
import mimetypes
import os
import queue
import re
import shutil
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from gui_visuals import (
    BrandIconManager,
    app_key_from_label,
    brand_for_app,
    draw_accent_header_strip,
    draw_app_badge,
    draw_share_donut,
    draw_token_mix_bar,
)

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python without zoneinfo support.
    ZoneInfo = None  # type: ignore[assignment]


VERSION = "0.2.5"
TRACKER_DIR = Path(__file__).resolve().parent
DEFAULT_CODEX_HOME = Path.home() / ".codex"
DEFAULT_CLAUDE_HOME = Path.home() / ".claude"
DEFAULT_CURSOR_AI_DB = Path.home() / ".cursor" / "ai-tracking" / "ai-code-tracking.db"
DEFAULT_CURSOR_STATE_DB = (
    Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    / "Cursor" / "User" / "globalStorage" / "state.vscdb"
)
DEFAULT_CURSOR_PROJECTS_HOME = Path.home() / ".cursor" / "projects"
CURSOR_CHARS_PER_TOKEN_ESTIMATE = 4.0
CURSOR_WORKSPACE_PATH_RE = re.compile(r"Workspace Path:\s*(.+?)(?:\n|$)", re.IGNORECASE)
CURSOR_TIMESTAMP_TAG_RE = re.compile(r"<timestamp>(.+?)</timestamp>", re.IGNORECASE | re.DOTALL)
DEFAULT_OUTPUT_DIR = Path.cwd() / "out"
DEFAULT_STATE_FILE = Path.home() / ".codex-usage-tracker" / "state.json"
PRICING_SOURCE_DATE = "2026-05-31"
CODEX_RATE_CARD_URL = "https://help.openai.com/en/articles/20001106-codex-rate-card"
API_PRICING_URL = "https://developers.openai.com/api/docs/pricing"
ANTHROPIC_PRICING_URL = "https://platform.claude.com/docs/en/about-claude/pricing?hsLang=en"
CURSOR_PRICING_URL = "https://cursor.com/en-US/pricing"
OPENAI_USAGE_API_URL = "https://platform.openai.com/docs/api-reference/usage"
OPENAI_COSTS_API_URL = "https://platform.openai.com/docs/api-reference/usage/costs"
ANTHROPIC_USAGE_COST_API_URL = "https://platform.claude.com/docs/en/build-with-claude/usage-cost-api"
ANTHROPIC_CLAUDE_CODE_ANALYTICS_URL = "https://platform.claude.com/docs/en/manage-claude/claude-code-analytics-api"
CURSOR_ADMIN_API_URL = "https://docs.cursor.com/account/teams/admin-api"
API_PRICING_BASIS = "standard short-context text token rates"

USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_creation_input_tokens",
    "cache_creation_1h_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)

SUPPORTED_SOURCES = ("codex", "claude", "cursor")
SOURCE_LABELS = {
    "codex": "Codex",
    "claude": "Claude Code",
    "cursor": "Cursor",
    "demo": "Demo",
    "(unknown)": "(unknown)",
}
DARK_THEME = {
    "mode": "dark",
    "bg": "#0b0c0f",
    "panel": "#12141a",
    "panel_alt": "#181b22",
    "card": "#151820",
    "card_hover": "#1a1e28",
    "field": "#0f1116",
    "ink": "#eef2f7",
    "muted": "#8b95a5",
    "subtle": "#1e222b",
    "border": "#2a3140",
    "border_soft": "#1f2430",
    "blue": "#5eb3ff",
    "green": "#3ecf8e",
    "amber": "#e7b86a",
    "rose": "#f07178",
    "selected": "#3b82f6",
    "hero": "#f8fafc",
}
GUI_APP_ACCENTS = {
    "codex": "#9d7cff",
    "claude": "#f59e6c",
    "cursor": "#36cfe8",
    "all": "#64748b",
}
GUI_CHART_COLORS = {
    "sources": GUI_APP_ACCENTS["codex"],
    "daily": DARK_THEME["blue"],
    "projects": DARK_THEME["green"],
    "models": DARK_THEME["amber"],
}
GUI_FONTS = {
    "title": ("Segoe UI", 20, "bold"),
    "hero": ("Segoe UI Semibold", 26, "bold"),
    "section": ("Segoe UI Semibold", 11, "bold"),
    "metric": ("Segoe UI Semibold", 13, "bold"),
    "body": ("Segoe UI", 10),
    "caption": ("Segoe UI", 9),
    "mono": ("Consolas", 9),
}
GUI_TABLE_ROW_LIMITS: dict[str, int] = {
    "threads": 400,
    "projects": 150,
    "daily": 90,
}

# Rates verified against official OpenAI/Anthropic pages on 2026-05-31.
# Codex credit estimates use the token-based Codex rate card. USD estimates are
# pricing-equivalent calculations, not an official invoice or live balance.
MODEL_RATES: dict[str, dict[str, dict[str, float]]] = {
    "gpt-5.5": {
        "codex_credits": {"input": 125.0, "cached_input": 12.5, "output": 750.0},
        "api_usd_standard_short": {"input": 5.0, "cached_input": 0.5, "output": 30.0},
    },
    "gpt-5.4": {
        "codex_credits": {"input": 62.5, "cached_input": 6.25, "output": 375.0},
        "api_usd_standard_short": {"input": 2.5, "cached_input": 0.25, "output": 15.0},
    },
    "gpt-5.4-mini": {
        "codex_credits": {"input": 18.75, "cached_input": 1.875, "output": 113.0},
        "api_usd_standard_short": {"input": 0.75, "cached_input": 0.075, "output": 4.5},
    },
    "gpt-5.3-codex": {
        "codex_credits": {"input": 43.75, "cached_input": 4.375, "output": 350.0},
        "api_usd_standard_short": {"input": 1.75, "cached_input": 0.175, "output": 14.0},
    },
    "gpt-5.2": {
        "codex_credits": {"input": 43.75, "cached_input": 4.375, "output": 350.0},
        "api_usd_standard_short": {"input": 1.75, "cached_input": 0.175, "output": 14.0},
    },
    "claude-opus-4-8": {
        "anthropic_usd": {"input": 5.0, "cache_creation": 6.25, "cache_creation_1h": 10.0, "cached_input": 0.50, "output": 25.0},
    },
    "claude-opus-4-7": {
        "anthropic_usd": {"input": 5.0, "cache_creation": 6.25, "cache_creation_1h": 10.0, "cached_input": 0.50, "output": 25.0},
    },
    "claude-opus-4-6": {
        "anthropic_usd": {"input": 5.0, "cache_creation": 6.25, "cache_creation_1h": 10.0, "cached_input": 0.50, "output": 25.0},
    },
    "claude-opus-4-5": {
        "anthropic_usd": {"input": 5.0, "cache_creation": 6.25, "cache_creation_1h": 10.0, "cached_input": 0.50, "output": 25.0},
    },
    "claude-opus-4-1": {
        "anthropic_usd": {"input": 15.0, "cache_creation": 18.75, "cache_creation_1h": 30.0, "cached_input": 1.50, "output": 75.0},
    },
    "claude-opus-4": {
        "anthropic_usd": {"input": 15.0, "cache_creation": 18.75, "cache_creation_1h": 30.0, "cached_input": 1.50, "output": 75.0},
    },
    "claude-sonnet-4-6": {
        "anthropic_usd": {"input": 3.0, "cache_creation": 3.75, "cache_creation_1h": 6.0, "cached_input": 0.30, "output": 15.0},
    },
    "claude-sonnet-4-5": {
        "anthropic_usd": {"input": 3.0, "cache_creation": 3.75, "cache_creation_1h": 6.0, "cached_input": 0.30, "output": 15.0},
    },
    "claude-sonnet-4": {
        "anthropic_usd": {"input": 3.0, "cache_creation": 3.75, "cache_creation_1h": 6.0, "cached_input": 0.30, "output": 15.0},
    },
    "claude-haiku-4-5": {
        "anthropic_usd": {"input": 1.0, "cache_creation": 1.25, "cache_creation_1h": 2.0, "cached_input": 0.10, "output": 5.0},
    },
    "claude-haiku-3-5": {
        "anthropic_usd": {"input": 0.80, "cache_creation": 1.0, "cache_creation_1h": 1.6, "cached_input": 0.08, "output": 4.0},
    },
    "cursor-auto": {
        "cursor_usd": {"input": 1.25, "cached_input": 0.25, "output": 6.0},
    },
    "composer-1": {
        "cursor_usd": {"input": 1.25, "cached_input": 0.25, "output": 6.0},
    },
    "composer-2": {
        "cursor_usd": {"input": 1.25, "cached_input": 0.25, "output": 6.0},
    },
    "composer-2.5": {
        "cursor_usd": {"input": 1.25, "cached_input": 0.25, "output": 6.0},
    },
}


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def fmt_dt(value: datetime | None, report_tz: Any = None) -> str:
    if not value:
        return ""
    return value.astimezone(report_tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def local_day(value: datetime, report_tz: Any = None) -> str:
    return value.astimezone(report_tz).strftime("%Y-%m-%d")


def clean_windows_path(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def project_name_from_cwd(cwd: str) -> str:
    cwd = clean_windows_path(cwd)
    if not cwd:
        return "(unknown)"
    if "\\" in cwd:
        trimmed = cwd.rstrip("\\/")
        if trimmed:
            return re.split(r"[\\/]+", trimmed)[-1] or trimmed
    try:
        return Path(cwd).name or cwd
    except Exception:
        return cwd


def zero_usage() -> dict[str, int]:
    return {field: 0 for field in USAGE_FIELDS}


def nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return 0


def normalize_usage(value: Any) -> dict[str, int]:
    usage = zero_usage()
    if not isinstance(value, dict):
        return usage
    for field in USAGE_FIELDS:
        usage[field] = nonnegative_int(value.get(field, 0))
    if usage["total_tokens"] == 0:
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return usage


def normalize_claude_usage(value: Any) -> dict[str, int]:
    usage = zero_usage()
    if not isinstance(value, dict):
        return usage

    base_input = nonnegative_int(value.get("input_tokens", 0))
    cache_creation = nonnegative_int(value.get("cache_creation_input_tokens", 0))
    cache_read = nonnegative_int(value.get("cache_read_input_tokens", 0))
    cache_creation_1h = 0
    cache_creation_meta = value.get("cache_creation")
    if isinstance(cache_creation_meta, dict):
        cache_creation_1h = min(
            cache_creation,
            nonnegative_int(cache_creation_meta.get("ephemeral_1h_input_tokens", 0)),
        )
    output = nonnegative_int(value.get("output_tokens", 0))

    usage["input_tokens"] = base_input + cache_creation + cache_read
    usage["cached_input_tokens"] = cache_read
    usage["cache_creation_input_tokens"] = cache_creation
    usage["cache_creation_1h_input_tokens"] = cache_creation_1h
    usage["output_tokens"] = output
    usage["total_tokens"] = usage["input_tokens"] + output
    return usage


def add_usage(target: dict[str, int], delta: dict[str, int]) -> None:
    for field in USAGE_FIELDS:
        target[field] = target.get(field, 0) + int(delta.get(field, 0))


def diff_usage(current: dict[str, int], previous: dict[str, int]) -> dict[str, int]:
    delta = zero_usage()
    for field in USAGE_FIELDS:
        cur = int(current.get(field, 0))
        prev = int(previous.get(field, 0))
        delta[field] = cur - prev if cur >= prev else cur
    return delta


def usage_total(usage: dict[str, int]) -> int:
    return int(usage.get("total_tokens", 0))


def parse_epoch_timestamp(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    seconds = float(value)
    if seconds > 10_000_000_000:
        seconds /= 1000.0
    try:
        return datetime.fromtimestamp(seconds, timezone.utc)
    except Exception:
        return None


def parse_source_filter(value: str | None) -> set[str]:
    raw = (value or "codex").strip().lower()
    if raw in {"", "codex"}:
        return {"codex"}
    parts = {part.strip().lower() for part in raw.split(",") if part.strip()}
    if "all" in parts:
        return set(SUPPORTED_SOURCES)
    unknown = sorted(parts - set(SUPPORTED_SOURCES))
    if unknown:
        known = ", ".join((*SUPPORTED_SOURCES, "all"))
        raise ValueError(f"unknown source(s): {', '.join(unknown)}. Use one of: {known}")
    return parts or {"codex"}


def normalize_app(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        return "(unknown)"
    if text in SOURCE_LABELS:
        return text
    if text.startswith("codex") or "openai" in text:
        return "codex"
    if text.startswith("claude") or "anthropic" in text:
        return "claude"
    if text.startswith("cursor"):
        return "cursor"
    if text.startswith("demo"):
        return "demo"
    return text


def thread_app(thread: dict[str, Any]) -> str:
    app = normalize_app(thread.get("app"))
    if app != "(unknown)":
        return app
    return normalize_app(thread.get("source"))


def app_label(app: Any) -> str:
    key = normalize_app(app)
    return SOURCE_LABELS.get(key, str(app or key))


def normalize_model(model: str | None) -> str:
    if not model:
        return ""
    return model.strip().lower().replace("_", "-")


def rates_for_model(model: str | None) -> dict[str, dict[str, float]] | None:
    normalized = normalize_model(model)
    if normalized in MODEL_RATES:
        return MODEL_RATES[normalized]
    # Handle suffixes like gpt-5.5-something conservatively.
    for known, rates in MODEL_RATES.items():
        if normalized.startswith(known):
            return rates
    return None


def estimate_amount(usage: dict[str, int], model: str | None, rate_kind: str) -> float | None:
    rates = rates_for_model(model)
    if not rates or rate_kind not in rates:
        return None
    rate = rates[rate_kind]
    cached = int(usage.get("cached_input_tokens", 0))
    cache_creation = int(usage.get("cache_creation_input_tokens", 0))
    cache_creation_1h = min(cache_creation, int(usage.get("cache_creation_1h_input_tokens", 0)))
    cache_creation_5m = max(0, cache_creation - cache_creation_1h)
    input_total = int(usage.get("input_tokens", 0))
    uncached_input = max(0, input_total - cached - cache_creation)
    output = int(usage.get("output_tokens", 0))
    return (
        (uncached_input / 1_000_000.0) * rate["input"]
        + (cached / 1_000_000.0) * rate.get("cached_input", rate["input"])
        + (cache_creation_5m / 1_000_000.0) * rate.get("cache_creation", rate["input"])
        + (cache_creation_1h / 1_000_000.0) * rate.get("cache_creation_1h", rate.get("cache_creation", rate["input"]))
        + (output / 1_000_000.0) * rate["output"]
    )


def pricing_metadata() -> dict[str, Any]:
    return {
        "source_date": PRICING_SOURCE_DATE,
        "codex_rate_card_url": CODEX_RATE_CARD_URL,
        "api_pricing_url": API_PRICING_URL,
        "anthropic_pricing_url": ANTHROPIC_PRICING_URL,
        "cursor_pricing_url": CURSOR_PRICING_URL,
        "api_pricing_basis": API_PRICING_BASIS,
        "caveat": "Codex credits and Claude USD are estimated from local token logs and official token rates. Cursor totals include Claude-style context cache replay estimates from Agent transcripts, composer bubbles, and agentKv blobs (character-based when exact counts are missing). Official billing, remaining credits, fast mode uplifts, taxes, and plan exceptions must be checked with the vendor.",
    }


def read_thread_db(codex_home: Path) -> dict[str, dict[str, Any]]:
    db_path = codex_home / "state_5.sqlite"
    if not db_path.exists():
        return {}

    result: dict[str, dict[str, Any]] = {}
    uri = f"file:{db_path}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=2)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            select id, title, cwd, model, reasoning_effort, created_at, updated_at,
                   tokens_used, source, thread_source, preview
            from threads
            """
        ).fetchall()
        for row in rows:
            result[row["id"]] = dict(row)
        con.close()
    except Exception as exc:
        print(f"warning: could not read {db_path}: {exc}", file=sys.stderr)
    return result


def iter_rollout_files(codex_home: Path) -> list[Path]:
    roots = [codex_home / "sessions", codex_home / "archived_sessions"]
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(root.rglob("rollout-*.jsonl"))
    return sorted(set(files), key=lambda p: str(p).lower())


def session_id_from_filename(path: Path) -> str:
    match = re.search(r"(019[0-9a-f-]{32,})", path.name, re.IGNORECASE)
    if match:
        return match.group(1)
    return path.stem


def estimate_active_seconds(
    timestamps: list[datetime],
    max_gap_seconds: int = 15 * 60,
    report_tz: Any = None,
) -> tuple[int, dict[str, int]]:
    unique = sorted(set(timestamps))
    if not unique:
        return 0, {}

    total = 60
    daily: dict[str, int] = defaultdict(int)
    daily[local_day(unique[0], report_tz)] += 60
    previous = unique[0]

    for current in unique[1:]:
        delta = int((current - previous).total_seconds())
        if 0 < delta <= max_gap_seconds:
            add = delta
        elif delta > max_gap_seconds:
            add = 60
        else:
            add = 0
        if add:
            total += add
            daily[local_day(current, report_tz)] += add
        previous = current

    return total, dict(daily)


def parse_rollout(path: Path, db_meta: dict[str, dict[str, Any]], report_tz: Any = None) -> dict[str, Any]:
    session_id = ""
    cwd = ""
    title = ""
    source = ""
    model = ""
    reasoning_effort = ""
    cli_version = ""
    timestamps: list[datetime] = []
    usage_events: list[tuple[datetime, dict[str, int]]] = []
    tool_counts: dict[str, int] = defaultdict(int)
    line_count = 0

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            line_count += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = parse_ts(obj.get("timestamp"))
            if ts:
                timestamps.append(ts)

            payload = obj.get("payload")
            if not isinstance(payload, dict):
                continue

            event_type = obj.get("type")
            if event_type == "session_meta":
                session_id = str(payload.get("id") or session_id)
                cwd = clean_windows_path(payload.get("cwd") or cwd)
                source = str(payload.get("source") or source)
                cli_version = str(payload.get("cli_version") or cli_version)
            elif event_type == "turn_context":
                cwd = clean_windows_path(payload.get("cwd") or cwd)
                model = str(payload.get("model") or model)
                reasoning_effort = str(payload.get("effort") or reasoning_effort)
                collab = payload.get("collaboration_mode")
                if isinstance(collab, dict):
                    settings = collab.get("settings")
                    if isinstance(settings, dict):
                        model = str(settings.get("model") or model)
                        reasoning_effort = str(settings.get("reasoning_effort") or reasoning_effort)

            payload_model = payload.get("model")
            if isinstance(payload_model, str):
                model = payload_model

            info = payload.get("info")
            if isinstance(info, dict) and ts:
                total_usage = info.get("total_token_usage")
                if isinstance(total_usage, dict):
                    usage_events.append((ts, normalize_usage(total_usage)))

            item_type = payload.get("type")
            if isinstance(item_type, str) and item_type:
                if item_type in {"function_call", "function_call_output", "tool_call", "tool_result"}:
                    tool_name = str(payload.get("name") or payload.get("tool_name") or item_type)
                    tool_counts[tool_name] += 1

    if not session_id:
        session_id = session_id_from_filename(path)

    meta = db_meta.get(session_id, {})
    title = str(meta.get("title") or title or "")
    cwd = clean_windows_path(meta.get("cwd") or cwd)
    model = str(meta.get("model") or model or "")
    reasoning_effort = str(meta.get("reasoning_effort") or reasoning_effort or "")
    source = str(meta.get("source") or source or "")

    usage_events.sort(key=lambda item: item[0])
    latest_usage = zero_usage()
    daily_usage: dict[str, dict[str, int]] = defaultdict(zero_usage)
    previous_usage = zero_usage()
    for ts, current_usage in usage_events:
        delta = diff_usage(current_usage, previous_usage)
        if usage_total(delta) > 0:
            add_usage(daily_usage[local_day(ts, report_tz)], delta)
        previous_usage = current_usage
        latest_usage = current_usage

    if usage_total(latest_usage) == 0 and isinstance(meta.get("tokens_used"), int):
        latest_usage["total_tokens"] = max(0, int(meta["tokens_used"]))

    active_seconds, active_daily = estimate_active_seconds(timestamps, report_tz=report_tz)

    started_at = min(timestamps) if timestamps else None
    ended_at = max(timestamps) if timestamps else None

    return {
        "thread_id": session_id,
        "title": title,
        "cwd": cwd,
        "project": project_name_from_cwd(cwd),
        "app": "codex",
        "source": source,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "cli_version": cli_version,
        "path": str(path),
        "line_count": line_count,
        "event_count": line_count,
        "request_count": len(usage_events),
        "started_at": started_at,
        "ended_at": ended_at,
        "usage": latest_usage,
        "daily_usage": dict(daily_usage),
        "event_timestamps": timestamps,
        "active_seconds": active_seconds,
        "active_daily": active_daily,
        "tool_counts": dict(tool_counts),
        "estimated_codex_credits": estimate_amount(latest_usage, model, "codex_credits"),
        "estimated_api_usd_equiv": estimate_amount(latest_usage, model, "api_usd_standard_short"),
    }


def load_threads(codex_home: Path, days: int | None = None, report_tz: Any = None) -> list[dict[str, Any]]:
    db_meta = read_thread_db(codex_home)
    parsed: dict[str, dict[str, Any]] = {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None

    for path in iter_rollout_files(codex_home):
        try:
            thread = parse_rollout(path, db_meta, report_tz=report_tz)
        except Exception as exc:
            print(f"warning: could not parse {path}: {exc}", file=sys.stderr)
            continue

        ended_at = thread.get("ended_at")
        if cutoff and isinstance(ended_at, datetime) and ended_at < cutoff:
            continue

        key = thread["thread_id"]
        existing = parsed.get(key)
        if not existing:
            parsed[key] = thread
            continue

        existing_total = usage_total(existing["usage"])
        current_total = usage_total(thread["usage"])
        existing_end = existing.get("ended_at") or datetime.min.replace(tzinfo=timezone.utc)
        current_end = thread.get("ended_at") or datetime.min.replace(tzinfo=timezone.utc)
        if (current_total, current_end) >= (existing_total, existing_end):
            parsed[key] = thread

    return sorted(parsed.values(), key=lambda item: item.get("ended_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def iter_claude_files(claude_home: Path) -> list[Path]:
    projects = claude_home / "projects"
    if not projects.exists():
        return []
    return sorted(set(projects.rglob("*.jsonl")), key=lambda p: str(p).lower())


def parse_claude_jsonl(path: Path, report_tz: Any = None) -> dict[str, Any]:
    session_id = path.stem
    cwd = ""
    title = ""
    cli_version = ""
    timestamps: list[datetime] = []
    daily_usage: dict[str, dict[str, int]] = defaultdict(zero_usage)
    total_usage = zero_usage()
    tool_counts: dict[str, int] = defaultdict(int)
    model_counts: Counter[str] = Counter()
    seen_message_ids: set[str] = set()
    line_count = 0
    request_count = 0

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            line_count += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = parse_ts(obj.get("timestamp"))
            if ts:
                timestamps.append(ts)

            if isinstance(obj.get("sessionId"), str):
                session_id = obj["sessionId"] or session_id
            if isinstance(obj.get("cwd"), str):
                cwd = clean_windows_path(obj.get("cwd") or cwd)
            if isinstance(obj.get("version"), str):
                cli_version = obj.get("version") or cli_version
            if isinstance(obj.get("aiTitle"), str) and obj.get("aiTitle"):
                title = obj.get("aiTitle") or title

            message = obj.get("message")
            if not isinstance(message, dict):
                continue

            model = message.get("model")
            if isinstance(model, str) and model:
                model_counts[model] += 1

            usage = normalize_claude_usage(message.get("usage"))
            if usage_total(usage) > 0:
                message_id = str(message.get("id") or obj.get("requestId") or obj.get("uuid") or "")
                if message_id and message_id in seen_message_ids:
                    continue
                if message_id:
                    seen_message_ids.add(message_id)
                add_usage(total_usage, usage)
                request_count += 1
                if ts:
                    add_usage(daily_usage[local_day(ts, report_tz)], usage)

            content = message.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_use":
                        tool_name = str(item.get("name") or "tool_use")
                        tool_counts[tool_name] += 1

    active_seconds, active_daily = estimate_active_seconds(timestamps, report_tz=report_tz)
    started_at = min(timestamps) if timestamps else None
    ended_at = max(timestamps) if timestamps else None
    model = model_counts.most_common(1)[0][0] if model_counts else ""
    if not title:
        title = f"Claude Code session {session_id[:8]}"

    return {
        "thread_id": session_id,
        "title": title,
        "cwd": cwd,
        "project": project_name_from_cwd(cwd),
        "app": "claude",
        "source": "claude_code",
        "model": model,
        "reasoning_effort": "",
        "cli_version": cli_version,
        "path": str(path),
        "line_count": line_count,
        "event_count": line_count,
        "request_count": request_count,
        "started_at": started_at,
        "ended_at": ended_at,
        "usage": total_usage,
        "daily_usage": dict(daily_usage),
        "event_timestamps": timestamps,
        "active_seconds": active_seconds,
        "active_daily": active_daily,
        "tool_counts": dict(tool_counts),
        "estimated_codex_credits": 0.0,
        "estimated_api_usd_equiv": estimate_amount(total_usage, model, "anthropic_usd") or 0.0,
    }


def load_claude_threads(claude_home: Path, days: int | None = None, report_tz: Any = None) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    threads: list[dict[str, Any]] = []
    for path in iter_claude_files(claude_home):
        try:
            thread = parse_claude_jsonl(path, report_tz=report_tz)
        except Exception as exc:
            print(f"warning: could not parse {path}: {exc}", file=sys.stderr)
            continue
        ended_at = thread.get("ended_at")
        if cutoff and isinstance(ended_at, datetime) and ended_at < cutoff:
            continue
        threads.append(thread)
    return sorted(threads, key=lambda item: item.get("ended_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def cursor_model_name(model: str | None) -> str:
    normalized = normalize_model(model)
    if not normalized or normalized in {"default", "none", "auto"}:
        return "cursor-auto"
    return normalized


def estimate_tokens_from_chars(chars: int) -> int:
    if chars <= 0:
        return 0
    return max(1, int(chars / CURSOR_CHARS_PER_TOKEN_ESTIMATE))


def cursor_message_usage_with_context(
    role: str | None,
    text: str,
    context_tokens: int,
    *,
    assistant_replays_context: bool = True,
) -> tuple[dict[str, int], int]:
    """Estimate per-message usage with Claude-style context cache replay."""
    usage = zero_usage()
    if not text:
        return usage, context_tokens
    tokens = estimate_tokens_from_chars(len(text))
    if role == "user":
        usage["cached_input_tokens"] = context_tokens
        usage["input_tokens"] = context_tokens + tokens
        context_tokens += tokens
    elif role == "assistant":
        if assistant_replays_context and context_tokens > 0:
            usage["cached_input_tokens"] = context_tokens
            usage["input_tokens"] = context_tokens
        usage["output_tokens"] = tokens
        context_tokens += tokens
    elif role in {"system", "tool"}:
        usage["input_tokens"] = tokens
        context_tokens += tokens
    else:
        return usage, context_tokens
    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return usage, context_tokens


def parse_cursor_agentkv_blob(value: Any) -> tuple[str, str]:
    raw = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
    text = raw.strip()
    if not text:
        return "", ""
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            role = str(data.get("role") or "")
            content = data.get("content")
            if isinstance(content, str):
                return role or "assistant", content
            if isinstance(content, list):
                return role or "user", extract_cursor_message_text({"message": {"content": content}})
    if not cursor_blob_is_mostly_text(text):
        return "", ""
    if "MASTER PROMPT" in text[:200] or text.startswith("You are "):
        return "system", text
    return "assistant", text


def cursor_state_db_for_agentkv(cursor_state_db: Path) -> Path:
  backup = cursor_state_db.parent / f"{cursor_state_db.name}.backup"
  if backup.exists():
      return backup
  return cursor_state_db


def cursor_blob_is_mostly_text(text: str) -> bool:
    if not text or len(text) > 250_000:
        return False
    sample = text[:8000]
    printable = sum(1 for char in sample if char.isprintable() or char in "\n\r\t")
    if printable / max(len(sample), 1) < 0.92:
        return False
    if "\x00" in text[:200]:
        return False
    return True


def cursor_text_from_agentkv_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return extract_cursor_message_text({"message": {"content": content}})
    return str(content or "")


def cursor_estimated_usd(usage: dict[str, int], model: str | None) -> float:
    resolved = cursor_model_name(model)
    for rate_kind in ("cursor_usd", "anthropic_usd", "api_usd_standard_short"):
        amount = estimate_amount(usage, resolved, rate_kind)
        if amount:
            return amount
    for known in MODEL_RATES:
        if resolved.startswith(known):
            for rate_kind in ("cursor_usd", "anthropic_usd", "api_usd_standard_short"):
                amount = estimate_amount(usage, known, rate_kind)
                if amount:
                    return amount
    return estimate_amount(usage, "cursor-auto", "cursor_usd") or 0.0


def decode_sqlite_json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return json.loads(value)
    return value


def open_readonly_sqlite(path: Path, *, allow_backup: bool = False) -> sqlite3.Connection | None:
    candidates = [path]
    if allow_backup:
        backup = path.parent / f"{path.name}.backup"
        if backup.exists():
            candidates.append(backup)
    for candidate in candidates:
        if not candidate.exists():
            continue
        for uri_flags in ("?mode=ro&immutable=1", "?mode=ro"):
            try:
                return sqlite3.connect(f"file:{candidate}{uri_flags}", uri=True, timeout=3)
            except Exception:
                continue
    return None


def extract_cursor_workspace_path(text: str) -> str:
    match = CURSOR_WORKSPACE_PATH_RE.search(text or "")
    if not match:
        return ""
    return clean_windows_path(match.group(1).strip())


def cursor_project_label_from_folder(folder_name: str) -> str:
    text = str(folder_name or "").strip()
    if not text:
        return "Cursor workspace"
    if text.isdigit():
        return f"cursor-project-{text}"
    if text.startswith("c-"):
        parts = text[2:].split("-")
        for marker in ("Documents", "Projects", "Desktop"):
            if marker in parts:
                idx = parts.index(marker)
                tail = parts[idx + 1 :]
                if tail:
                    return "-".join(tail)
        if len(parts) >= 3:
            return "-".join(parts[-3:])
    return text


def extract_cursor_message_text(row: dict[str, Any]) -> str:
    content = row.get("message", {}).get("content", [])
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            parts.append(str(block.get("text") or ""))
        elif block_type == "tool_use":
            parts.append(json.dumps(block.get("input") or {}, default=str, ensure_ascii=False))
    return "\n".join(part for part in parts if part)


def parse_cursor_timestamp_tag(text: str) -> datetime | None:
    match = CURSOR_TIMESTAMP_TAG_RE.search(text or "")
    if not match:
        return None
    raw = match.group(1).strip()
    try:
        normalized = raw.replace(" (UTC", "+00:00").replace("UTC+5:30)", "+05:30")
        if normalized.endswith(")"):
            normalized = normalized[:-1]
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def make_cursor_thread(
    *,
    conversation_id: str,
    title: str,
    cwd: str,
    model: str,
    source: str,
    path: str,
    usage: dict[str, int],
    daily_usage: dict[str, dict[str, int]],
    timestamps: list[datetime],
    event_count: int,
    request_count: int,
    report_tz: Any = None,
    token_basis: str = "",
) -> dict[str, Any]:
    active_seconds, active_daily = estimate_active_seconds(timestamps, report_tz=report_tz)
    started_at = min(timestamps) if timestamps else None
    ended_at = max(timestamps) if timestamps else None
    resolved_model = cursor_model_name(model)
    thread = {
        "thread_id": f"cursor-{conversation_id}",
        "title": title,
        "cwd": cwd,
        "project": project_name_from_cwd(cwd) if cwd else "Cursor workspace",
        "app": "cursor",
        "source": source,
        "model": resolved_model,
        "reasoning_effort": "",
        "cli_version": "",
        "path": path,
        "line_count": event_count,
        "event_count": event_count,
        "request_count": request_count,
        "started_at": started_at,
        "ended_at": ended_at,
        "usage": usage,
        "daily_usage": daily_usage,
        "event_timestamps": timestamps,
        "active_seconds": active_seconds,
        "active_daily": active_daily,
        "tool_counts": {
            "ai_code_events": event_count,
            "requests": request_count,
        },
        "estimated_codex_credits": 0.0,
        "estimated_api_usd_equiv": cursor_estimated_usd(usage, resolved_model),
    }
    if token_basis:
        thread["cursor_token_basis"] = token_basis
    return thread


def cursor_transcript_conversation_id(path: Path, projects_home: Path) -> str:
    try:
        relative = path.relative_to(projects_home)
    except ValueError:
        relative = path
    return relative.with_suffix("").as_posix().replace("/", "--")


def parse_cursor_transcript_file(path: Path, projects_home: Path, report_tz: Any = None) -> dict[str, Any] | None:
    conversation_id = cursor_transcript_conversation_id(path, projects_home)
    project_folder = ""
    for part in path.parts:
        if part == "agent-transcripts" and path.parts.index(part) > 0:
            project_folder = path.parts[path.parts.index(part) - 1]
            break

    cwd = ""
    title = f"Cursor Agent {conversation_id[-8:]}"
    model_counts: Counter[str] = Counter()
    tool_counts: Counter[str] = Counter()
    timestamps: list[datetime] = []
    total_usage = zero_usage()
    daily_usage: dict[str, dict[str, int]] = defaultdict(zero_usage)
    turn_user_chars = 0
    turn_assistant_chars = 0
    turn_timestamp: datetime | None = None
    event_count = 0
    request_count = 0

    fallback_mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)

    def add_message_usage(role: str | None, text: str, ts: datetime | None) -> None:
        nonlocal request_count, context_tokens
        if not text:
            return
        message_usage, context_tokens = cursor_message_usage_with_context(role, text, context_tokens)
        if usage_total(message_usage) == 0:
            return
        add_usage(total_usage, message_usage)
        request_count += 1
        event_ts = ts or turn_timestamp or fallback_mtime
        timestamps.append(event_ts)
        day = local_day(event_ts, report_tz)
        add_usage(daily_usage[day], message_usage)

    context_tokens = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            event_count += 1
            role = row.get("role")
            text = extract_cursor_message_text(row)
            if role == "user":
                if not cwd:
                    cwd = extract_cursor_workspace_path(text)
                turn_timestamp = parse_cursor_timestamp_tag(text) or turn_timestamp
            row_ts = parse_cursor_timestamp_tag(text) if role == "user" else None
            add_message_usage(role, text, row_ts)
            if role == "assistant":
                for block in row.get("message", {}).get("content", []) or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_name = str(block.get("name") or "tool")
                        tool_counts[tool_name] += 1
            model_name = str(row.get("model") or row.get("modelName") or "")
            if model_name:
                model_counts[model_name] += 1

    if usage_total(total_usage) == 0:
        return None

    model = model_counts.most_common(1)[0][0] if model_counts else "cursor-auto"
    if project_folder and not cwd:
        cwd = cursor_project_label_from_folder(project_folder)
    thread = make_cursor_thread(
        conversation_id=conversation_id,
        title=title,
        cwd=cwd,
        model=model,
        source="cursor_transcript",
        path=str(path),
        usage=total_usage,
        daily_usage=dict(daily_usage),
        timestamps=timestamps or [fallback_mtime],
        event_count=event_count,
        request_count=max(request_count, 1),
        report_tz=report_tz,
        token_basis="context_cache_estimated",
    )
    thread["tool_counts"].update(dict(tool_counts))
    return thread


def load_cursor_transcript_threads(
    projects_home: Path,
    days: int | None = None,
    report_tz: Any = None,
) -> dict[str, dict[str, Any]]:
    if not projects_home.exists():
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    threads: dict[str, dict[str, Any]] = {}
    for path in sorted(projects_home.rglob("*.jsonl")):
        if "agent-transcripts" not in path.parts:
            continue
        try:
            thread = parse_cursor_transcript_file(path, projects_home, report_tz=report_tz)
        except Exception as exc:
            print(f"warning: could not parse {path}: {exc}", file=sys.stderr)
            continue
        if not thread:
            continue
        ended_at = thread.get("ended_at")
        if cutoff and isinstance(ended_at, datetime) and ended_at < cutoff:
            continue
        conversation_id = cursor_transcript_conversation_id(path, projects_home)
        threads[conversation_id] = thread
    return threads


def load_cursor_bubble_threads(
    cursor_state_db: Path,
    *,
    days: int | None = None,
    report_tz: Any = None,
) -> dict[str, dict[str, Any]]:
    con = open_readonly_sqlite(cursor_state_db, allow_backup=True)
    if con is None:
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    raw_groups: dict[str, dict[str, Any]] = {}
    try:
        tables = {
            row[0]
            for row in con.execute("select name from sqlite_master where type='table'").fetchall()
        }
        if "cursorDiskKV" not in tables:
            con.close()
            return {}

        for key, value in con.execute(
            "select key, value from cursorDiskKV where key like 'bubbleId:%'"
        ):
            parts = str(key).split(":")
            if len(parts) < 3:
                continue
            conversation_id = parts[1]
            try:
                data = decode_sqlite_json_value(value)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue

            text = str(data.get("text") or "")
            if not text:
                continue
            group = raw_groups.setdefault(conversation_id, {
                "bubbles": [],
                "cwd": "",
                "title": f"Cursor chat {conversation_id[:8]}",
            })
            if not group["cwd"]:
                group["cwd"] = extract_cursor_workspace_path(text)
            created_at = parse_epoch_timestamp(data.get("createdAt"))
            group["bubbles"].append({
                "text": text,
                "bubble_type": data.get("type"),
                "token_count": data.get("tokenCount") or {},
                "created_at": created_at,
                "model_name": (data.get("modelInfo") or {}).get("modelName"),
            })
    except Exception as exc:
        print(f"warning: could not read {cursor_state_db}: {exc}", file=sys.stderr)
    finally:
        con.close()

    threads: dict[str, dict[str, Any]] = {}
    for conversation_id, group in raw_groups.items():
        usage = zero_usage()
        daily_usage: dict[str, dict[str, int]] = defaultdict(zero_usage)
        timestamps: list[datetime] = []
        model_counts: Counter[str] = Counter()
        context_tokens = 0
        request_count = 0
        event_count = len(group["bubbles"])
        token_basis = "context_cache_estimated"

        for bubble in sorted(
            group["bubbles"],
            key=lambda item: (
                item["created_at"] or datetime.min.replace(tzinfo=timezone.utc),
                str(item["bubble_type"] or ""),
            ),
        ):
            text = str(bubble["text"] or "")
            bubble_type = bubble["bubble_type"]
            role = "user" if bubble_type == 1 else "assistant"
            tc = bubble["token_count"] or {}
            inp = int(tc.get("inputTokens") or 0)
            out = int(tc.get("outputTokens") or 0)
            if inp or out:
                bubble_usage = zero_usage()
                if role == "user":
                    bubble_usage["cached_input_tokens"] = context_tokens
                    bubble_usage["input_tokens"] = context_tokens + inp
                    context_tokens += inp
                else:
                    if context_tokens > 0:
                        bubble_usage["cached_input_tokens"] = context_tokens
                        bubble_usage["input_tokens"] = context_tokens
                    bubble_usage["output_tokens"] = out
                    context_tokens += out
                bubble_usage["total_tokens"] = bubble_usage["input_tokens"] + bubble_usage["output_tokens"]
                token_basis = "bubble_explicit"
            else:
                bubble_usage, context_tokens = cursor_message_usage_with_context(role, text, context_tokens)

            if usage_total(bubble_usage) == 0:
                continue
            add_usage(usage, bubble_usage)
            request_count += 1
            created_at = bubble["created_at"]
            if created_at:
                timestamps.append(created_at)
                day = local_day(created_at, report_tz)
                add_usage(daily_usage[day], bubble_usage)
            model_name = bubble.get("model_name")
            if model_name:
                model_counts[str(model_name)] += 1

        if usage_total(usage) == 0:
            continue
        ended_at = max(timestamps) if timestamps else None
        if cutoff and ended_at and ended_at < cutoff:
            continue
        model = model_counts.most_common(1)[0][0] if model_counts else "cursor-auto"
        threads[f"bubble-{conversation_id}"] = make_cursor_thread(
            conversation_id=f"bubble-{conversation_id}",
            title=group["title"],
            cwd=group["cwd"],
            model=model,
            source="cursor_bubble",
            path=str(cursor_state_db),
            usage=usage,
            daily_usage=dict(daily_usage),
            timestamps=timestamps or [datetime.now(timezone.utc)],
            event_count=event_count,
            request_count=max(request_count, 1),
            report_tz=report_tz,
            token_basis=token_basis,
        )
    return threads


def load_cursor_agentkv_threads(
    cursor_state_db: Path,
    *,
    days: int | None = None,
    report_tz: Any = None,
) -> dict[str, dict[str, Any]]:
    agentkv_db = cursor_state_db_for_agentkv(cursor_state_db)
    con = open_readonly_sqlite(agentkv_db, allow_backup=False)
    if con is None:
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    usage = zero_usage()
    daily_usage: dict[str, dict[str, int]] = defaultdict(zero_usage)
    timestamps: list[datetime] = []
    context_tokens = 0
    request_count = 0
    event_count = 0

    try:
        tables = {
            row[0]
            for row in con.execute("select name from sqlite_master where type='table'").fetchall()
        }
        if "cursorDiskKV" not in tables:
            con.close()
            return {}

        for _key, value in con.execute(
            "select key, value from cursorDiskKV where key like 'agentKv:blob:%' order by key"
        ):
            role, text = parse_cursor_agentkv_blob(value)
            if not text or role not in {"user", "assistant", "system", "tool"}:
                continue
            event_count += 1
            if role in {"user", "system"} and context_tokens > 0:
                context_tokens = 0
            message_usage, context_tokens = cursor_message_usage_with_context(role, text, context_tokens)
            if usage_total(message_usage) == 0:
                continue
            add_usage(usage, message_usage)
            request_count += 1
            event_ts = datetime.now(timezone.utc)
            timestamps.append(event_ts)
            day = local_day(event_ts, report_tz)
            add_usage(daily_usage[day], message_usage)
    except Exception as exc:
        print(f"warning: could not read Cursor agentKv from {cursor_state_db}: {exc}", file=sys.stderr)
    finally:
        con.close()

    if usage_total(usage) == 0:
        return {}

    ended_at = max(timestamps) if timestamps else datetime.now(timezone.utc)
    if cutoff and ended_at < cutoff:
        return {}

    thread = make_cursor_thread(
        conversation_id="agentkv-context-estimate",
        title="Cursor agent context cache estimate",
        cwd="",
        model="cursor-auto",
        source="cursor_agentkv",
        path=str(agentkv_db),
        usage=usage,
        daily_usage=dict(daily_usage),
        timestamps=timestamps or [ended_at],
        event_count=event_count,
        request_count=max(request_count, 1),
        report_tz=report_tz,
        token_basis="context_cache_estimated",
    )
    return {"agentkv-context-estimate": thread}


def load_cursor_tracking_map(
    cursor_db: Path,
    days: int | None = None,
    report_tz: Any = None,
) -> dict[str, dict[str, Any]]:
    if not cursor_db.exists():
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    groups: dict[str, dict[str, Any]] = {}
    summaries: dict[str, str] = {}

    try:
        con = sqlite3.connect(f"file:{cursor_db}?mode=ro", uri=True, timeout=3)
        tables = {
            row[0]
            for row in con.execute("select name from sqlite_master where type='table'").fetchall()
        }
        if "conversation_summaries" in tables:
            for conversation_id, title in con.execute("select conversationId, title from conversation_summaries"):
                if conversation_id and title:
                    summaries[str(conversation_id)] = str(title)
        if "ai_code_hashes" not in tables:
            con.close()
            return {}

        hash_columns = {
            row[1] for row in con.execute("pragma table_info(ai_code_hashes)").fetchall()
        }
        if "fileName" in hash_columns:
            select_sql = """
                select conversationId, requestId, timestamp, source, fileExtension, fileName, model
                from ai_code_hashes
                order by timestamp
            """
        else:
            select_sql = """
                select conversationId, requestId, timestamp, source, fileExtension, null, model
                from ai_code_hashes
                order by timestamp
            """
        for conversation_id, request_id, timestamp, source, file_extension, file_name, model in con.execute(select_sql):
            conversation_id = str(conversation_id or request_id or f"cursor-{timestamp}")
            group = groups.setdefault(conversation_id, {
                "conversation_id": conversation_id,
                "timestamps": [],
                "requests": set(),
                "model_counts": Counter(),
                "source_counts": Counter(),
                "extension_counts": Counter(),
                "line_count": 0,
                "cwd": "",
            })
            ts = parse_epoch_timestamp(timestamp)
            if ts:
                group["timestamps"].append(ts)
            if request_id:
                group["requests"].add(str(request_id))
            if source:
                group["source_counts"][str(source)] += 1
            if file_extension:
                group["extension_counts"][str(file_extension)] += 1
            if model:
                group["model_counts"][str(model)] += 1
            if file_name and not group["cwd"]:
                group["cwd"] = clean_windows_path(str(Path(str(file_name)).parent))
            group["line_count"] += 1
        con.close()
    except Exception as exc:
        print(f"warning: could not read {cursor_db}: {exc}", file=sys.stderr)
        return {}

    threads: dict[str, dict[str, Any]] = {}
    for group in groups.values():
        timestamps = list(group["timestamps"])
        if not timestamps:
            continue
        started_at = min(timestamps)
        ended_at = max(timestamps)
        if cutoff and ended_at < cutoff:
            continue
        conversation_id = group["conversation_id"]
        model_counts: Counter[str] = group["model_counts"]
        source_counts: Counter[str] = group["source_counts"]
        model = model_counts.most_common(1)[0][0] if model_counts else ""
        source = source_counts.most_common(1)[0][0] if source_counts else "cursor"
        request_count = len(group["requests"]) or group["line_count"]
        cwd = group.get("cwd") or ""
        threads[conversation_id] = make_cursor_thread(
            conversation_id=conversation_id,
            title=summaries.get(conversation_id) or f"Cursor AI edits {conversation_id[:8]}",
            cwd=cwd,
            model=model or "cursor-auto",
            source=f"cursor_{source}",
            path=str(cursor_db),
            usage=zero_usage(),
            daily_usage={},
            timestamps=timestamps,
            event_count=group["line_count"],
            request_count=request_count,
            report_tz=report_tz,
            token_basis="activity_only",
        )
    return threads


def max_usage(target: dict[str, int], candidate: dict[str, int]) -> None:
    for field in USAGE_FIELDS:
        target[field] = max(int(target.get(field, 0)), int(candidate.get(field, 0)))


def merge_cursor_thread_maps(
    *maps: dict[str, dict[str, Any]],
    report_tz: Any = None,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    token_threads_by_uuid: dict[str, str] = {}

    def register_uuid(thread: dict[str, Any], merge_key: str) -> None:
        for uuid in re.findall(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            str(thread.get("thread_id") or merge_key),
            re.IGNORECASE,
        ):
            if usage_total(thread.get("usage") or {}) > 0:
                token_threads_by_uuid[uuid.lower()] = merge_key

    def attach_tracking_metadata(existing: dict[str, Any], thread: dict[str, Any]) -> None:
        existing["event_count"] = max(int(existing.get("event_count") or 0), int(thread.get("event_count") or 0))
        existing["request_count"] = max(int(existing.get("request_count") or 0), int(thread.get("request_count") or 0))
        existing["line_count"] = max(int(existing.get("line_count") or 0), int(thread.get("line_count") or 0))
        if thread.get("title") and (
            not existing.get("title")
            or str(existing.get("title", "")).startswith("Cursor ")
        ):
            existing["title"] = thread["title"]
        if thread.get("cwd") and not existing.get("cwd"):
            existing["cwd"] = thread["cwd"]
            existing["project"] = project_name_from_cwd(thread["cwd"])
        for tool, count in (thread.get("tool_counts") or {}).items():
            existing_tools = existing.setdefault("tool_counts", {})
            existing_tools[tool] = int(existing_tools.get(tool, 0)) + int(count)
        all_timestamps = list(existing.get("event_timestamps") or []) + list(thread.get("event_timestamps") or [])
        if all_timestamps:
            active_seconds, active_daily = estimate_active_seconds(all_timestamps, report_tz=report_tz)
            existing["event_timestamps"] = sorted(set(all_timestamps))
            existing["started_at"] = min(all_timestamps)
            existing["ended_at"] = max(all_timestamps)
            existing["active_seconds"] = active_seconds
            existing["active_daily"] = active_daily

    for thread_map in maps:
        for conversation_id, thread in thread_map.items():
            merge_key = conversation_id
            thread_usage = usage_total(thread.get("usage") or {})
            if thread_usage == 0:
                target_key = ""
                for uuid in re.findall(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    conversation_id,
                    re.IGNORECASE,
                ):
                    target_key = token_threads_by_uuid.get(uuid.lower(), "")
                    if target_key:
                        break
                if target_key and target_key in merged:
                    attach_tracking_metadata(merged[target_key], thread)
                    continue
                merge_key = f"tracking-{conversation_id}"

            existing = merged.get(merge_key)
            if existing is None:
                merged[merge_key] = thread
                register_uuid(thread, merge_key)
                continue

            existing_source = str(existing.get("source") or "")
            incoming_source = str(thread.get("source") or "")
            if thread_usage > 0 and existing_source != incoming_source:
                if "agentkv" in {existing_source, incoming_source}:
                    if thread_usage > usage_total(existing.get("usage") or {}):
                        existing["usage"] = dict(thread["usage"])
                        existing["daily_usage"] = dict(thread.get("daily_usage") or {})
                        existing["estimated_api_usd_equiv"] = thread.get("estimated_api_usd_equiv") or 0.0
                        existing["source"] = incoming_source or existing_source
                        existing["cursor_token_basis"] = thread.get("cursor_token_basis") or existing.get("cursor_token_basis")
                else:
                    max_usage(existing["usage"], thread.get("usage") or zero_usage())
                    for day, usage in (thread.get("daily_usage") or {}).items():
                        daily_usage = existing.setdefault("daily_usage", {})
                        if day not in daily_usage:
                            daily_usage[day] = zero_usage()
                        max_usage(daily_usage[day], usage)
                    existing["estimated_api_usd_equiv"] = max(
                        float(existing.get("estimated_api_usd_equiv") or 0.0),
                        float(thread.get("estimated_api_usd_equiv") or 0.0),
                    )
                existing["cursor_token_basis"] = "context_cache_estimated"
            elif thread_usage > usage_total(existing.get("usage") or {}):
                existing["usage"] = dict(thread["usage"])
                existing["daily_usage"] = dict(thread.get("daily_usage") or {})
                existing["estimated_api_usd_equiv"] = thread.get("estimated_api_usd_equiv") or 0.0
                existing["source"] = incoming_source or existing_source
                existing["cursor_token_basis"] = thread.get("cursor_token_basis") or existing.get("cursor_token_basis")
                if thread.get("model"):
                    existing["model"] = thread["model"]

            attach_tracking_metadata(existing, thread)
            register_uuid(existing, merge_key)

    return sorted(
        merged.values(),
        key=lambda item: item.get("ended_at") or item.get("started_at") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def load_cursor_threads(
    cursor_db: Path,
    *,
    cursor_state_db: Path | None = None,
    cursor_projects_home: Path | None = None,
    days: int | None = None,
    report_tz: Any = None,
) -> list[dict[str, Any]]:
    projects_home = cursor_projects_home or DEFAULT_CURSOR_PROJECTS_HOME
    state_db = cursor_state_db or DEFAULT_CURSOR_STATE_DB

    transcript_threads = load_cursor_transcript_threads(projects_home, days=days, report_tz=report_tz)
    bubble_threads = load_cursor_bubble_threads(state_db, days=days, report_tz=report_tz)
    agentkv_threads = load_cursor_agentkv_threads(state_db, days=days, report_tz=report_tz)
    tracking_threads = load_cursor_tracking_map(cursor_db, days=days, report_tz=report_tz)
    return merge_cursor_thread_maps(
        transcript_threads,
        bubble_threads,
        agentkv_threads,
        tracking_threads,
        report_tz=report_tz,
    )


def read_cursor_daily_stats(cursor_state_db: Path) -> list[dict[str, Any]]:
    if not cursor_state_db.exists():
        return []

    rows: list[dict[str, Any]] = []
    try:
        con = sqlite3.connect(f"file:{cursor_state_db}?mode=ro", uri=True, timeout=3)
        tables = {
            row[0]
            for row in con.execute("select name from sqlite_master where type='table'").fetchall()
        }
        if "ItemTable" not in tables:
            con.close()
            return []

        for key, value in con.execute(
            "select key, value from ItemTable where key like 'aiCodeTracking.dailyStats.%' order by key"
        ):
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
            try:
                data = json.loads(value)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue

            date = str(data.get("date") or "")
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
                match = re.search(r"(\d{4}-\d{2}-\d{2})$", str(key))
                date = match.group(1) if match else ""
            if not date:
                continue

            rows.append({
                "date": date,
                "tab_suggested_lines": nonnegative_int(data.get("tabSuggestedLines")),
                "tab_accepted_lines": nonnegative_int(data.get("tabAcceptedLines")),
                "composer_suggested_lines": nonnegative_int(data.get("composerSuggestedLines")),
                "composer_accepted_lines": nonnegative_int(data.get("composerAcceptedLines")),
            })
        con.close()
    except Exception as exc:
        print(f"warning: could not read {cursor_state_db}: {exc}", file=sys.stderr)
        return []

    return sorted(rows, key=lambda row: row["date"])


def load_selected_threads(args: argparse.Namespace, report_tz: Any = None) -> list[dict[str, Any]]:
    sources = parse_source_filter(getattr(args, "sources", None))
    threads: list[dict[str, Any]] = []
    if "codex" in sources:
        threads.extend(load_threads(Path(args.codex_home).expanduser(), days=args.days, report_tz=report_tz))
    if "claude" in sources:
        threads.extend(load_claude_threads(Path(args.claude_home).expanduser(), days=args.days, report_tz=report_tz))
    if "cursor" in sources:
        threads.extend(
            load_cursor_threads(
                Path(args.cursor_db).expanduser(),
                cursor_state_db=Path(args.cursor_state_db).expanduser(),
                cursor_projects_home=Path(args.cursor_projects_home).expanduser(),
                days=args.days,
                report_tz=report_tz,
            )
        )
    return sorted(
        threads,
        key=lambda item: item.get("ended_at") or item.get("started_at") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def resolve_timezone(name: str | None) -> Any:
    if not name:
        return None
    if ZoneInfo is None:
        print(f"warning: timezone '{name}' ignored because zoneinfo is unavailable", file=sys.stderr)
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        print(f"warning: timezone '{name}' not found; using local timezone", file=sys.stderr)
        return None


def parse_date_bound(value: str | None, report_tz: Any = None, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            parsed = datetime.strptime(value, "%Y-%m-%d")
            if end_of_day:
                parsed = parsed + timedelta(days=1) - timedelta(microseconds=1)
        else:
            normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
            parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            if report_tz is not None:
                parsed = parsed.replace(tzinfo=report_tz)
            else:
                parsed = parsed.astimezone().replace(tzinfo=datetime.now().astimezone().tzinfo)
        return parsed.astimezone(timezone.utc)
    except ValueError as exc:
        raise ValueError(f"invalid date '{value}'. Use YYYY-MM-DD or ISO datetime.") from exc


def filter_threads_by_date(
    threads: list[dict[str, Any]],
    since: str | None = None,
    until: str | None = None,
    report_tz: Any = None,
) -> list[dict[str, Any]]:
    since_dt = parse_date_bound(since, report_tz=report_tz, end_of_day=False)
    until_dt = parse_date_bound(until, report_tz=report_tz, end_of_day=True)
    if not since_dt and not until_dt:
        return threads

    filtered: list[dict[str, Any]] = []
    for thread in threads:
        started = thread.get("started_at")
        ended = thread.get("ended_at")
        activity = ended or started
        if not isinstance(activity, datetime):
            continue
        if since_dt and activity < since_dt:
            continue
        if until_dt and activity > until_dt:
            continue
        filtered.append(thread)
    return filtered


def anonymized_project_label(value: str, index: int) -> str:
    source = value or f"unknown-{index}"
    digest = hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest()[:8]
    return f"project-{digest}"


def copy_thread(thread: dict[str, Any]) -> dict[str, Any]:
    clone = dict(thread)
    clone["usage"] = dict(thread.get("usage") or zero_usage())
    clone["daily_usage"] = {
        day: dict(usage)
        for day, usage in (thread.get("daily_usage") or {}).items()
    }
    clone["active_daily"] = dict(thread.get("active_daily") or {})
    clone["tool_counts"] = dict(thread.get("tool_counts") or {})
    clone["event_timestamps"] = list(thread.get("event_timestamps") or [])
    return clone


def apply_privacy(
    threads: list[dict[str, Any]],
    redact: bool = False,
    hash_projects: bool = False,
) -> list[dict[str, Any]]:
    if not redact and not hash_projects:
        return threads

    project_labels: dict[str, str] = {}
    result: list[dict[str, Any]] = []
    for index, thread in enumerate(threads, start=1):
        clone = copy_thread(thread)
        project_key = str(thread.get("cwd") or thread.get("project") or thread.get("thread_id") or index)

        if redact:
            clone["title"] = "(redacted)"
            clone["path"] = ""

        if hash_projects:
            label = project_labels.setdefault(project_key, anonymized_project_label(project_key, index))
            clone["project"] = label
            clone["cwd"] = ""
            clone["path"] = ""
        elif redact:
            clone["project"] = "(redacted-project)"
            clone["cwd"] = ""

        result.append(clone)
    return result


def aggregate_threads(threads: list[dict[str, Any]]) -> dict[str, Any]:
    total_usage = zero_usage()
    total_credits = 0.0
    total_usd = 0.0
    total_active_seconds = 0
    total_event_count = 0
    total_request_count = 0
    daily: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "date": "",
        "usage": zero_usage(),
        "estimated_codex_credits": 0.0,
        "estimated_api_usd_equiv": 0.0,
        "active_seconds": 0,
        "threads": set(),
    })
    projects: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "app": "",
        "project": "",
        "cwd": "",
        "usage": zero_usage(),
        "estimated_codex_credits": 0.0,
        "estimated_api_usd_equiv": 0.0,
        "active_seconds": 0,
        "event_count": 0,
        "request_count": 0,
        "thread_count": 0,
    })
    models: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "app": "",
        "model": "",
        "usage": zero_usage(),
        "estimated_codex_credits": 0.0,
        "estimated_api_usd_equiv": 0.0,
        "active_seconds": 0,
        "event_count": 0,
        "request_count": 0,
        "thread_count": 0,
    })
    sources: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "app": "",
        "usage": zero_usage(),
        "estimated_codex_credits": 0.0,
        "estimated_api_usd_equiv": 0.0,
        "active_seconds": 0,
        "event_count": 0,
        "request_count": 0,
        "thread_count": 0,
    })

    for thread in threads:
        usage = thread["usage"]
        app = thread_app(thread)
        app_name = app_label(app)
        model = thread.get("model") or "(unknown)"
        project = thread.get("project") or "(unknown)"
        cwd = thread.get("cwd") or ""
        credits = thread.get("estimated_codex_credits") or 0.0
        usd = thread.get("estimated_api_usd_equiv") or 0.0
        active_seconds = int(thread.get("active_seconds") or 0)
        event_count = int(thread.get("event_count") or thread.get("line_count") or 0)
        request_count = int(thread.get("request_count") or 0)

        add_usage(total_usage, usage)
        total_credits += credits
        total_usd += usd
        total_active_seconds += active_seconds
        total_event_count += event_count
        total_request_count += request_count

        source_row = sources[app]
        source_row["app"] = app_name
        add_usage(source_row["usage"], usage)
        source_row["estimated_codex_credits"] += credits
        source_row["estimated_api_usd_equiv"] += usd
        source_row["active_seconds"] += active_seconds
        source_row["event_count"] += event_count
        source_row["request_count"] += request_count
        source_row["thread_count"] += 1

        project_row = projects[f"{app}\0{cwd or project}"]
        project_row["app"] = app_name
        project_row["project"] = project
        project_row["cwd"] = cwd
        add_usage(project_row["usage"], usage)
        project_row["estimated_codex_credits"] += credits
        project_row["estimated_api_usd_equiv"] += usd
        project_row["active_seconds"] += active_seconds
        project_row["event_count"] += event_count
        project_row["request_count"] += request_count
        project_row["thread_count"] += 1

        model_row = models[f"{app}\0{model}"]
        model_row["app"] = app_name
        model_row["model"] = model
        add_usage(model_row["usage"], usage)
        model_row["estimated_codex_credits"] += credits
        model_row["estimated_api_usd_equiv"] += usd
        model_row["active_seconds"] += active_seconds
        model_row["event_count"] += event_count
        model_row["request_count"] += request_count
        model_row["thread_count"] += 1

        for day, day_usage in thread.get("daily_usage", {}).items():
            day_row = daily[day]
            day_row["date"] = day
            add_usage(day_row["usage"], day_usage)
            if app == "codex":
                day_row["estimated_codex_credits"] += estimate_amount(day_usage, model, "codex_credits") or 0.0
                day_row["estimated_api_usd_equiv"] += estimate_amount(day_usage, model, "api_usd_standard_short") or 0.0
            elif app == "claude":
                day_row["estimated_api_usd_equiv"] += estimate_amount(day_usage, model, "anthropic_usd") or 0.0
            elif app == "cursor":
                day_row["estimated_api_usd_equiv"] += cursor_estimated_usd(day_usage, model)
            day_row["threads"].add(thread["thread_id"])

        for day, seconds in thread.get("active_daily", {}).items():
            daily[day]["date"] = day
            daily[day]["active_seconds"] += int(seconds)
            daily[day]["threads"].add(thread["thread_id"])

    daily_rows = []
    for row in daily.values():
        row = dict(row)
        row["thread_count"] = len(row.pop("threads"))
        daily_rows.append(row)

    return {
        "generated_at": datetime.now(timezone.utc),
        "thread_count": len(threads),
        "usage": total_usage,
        "estimated_codex_credits": total_credits,
        "estimated_api_usd_equiv": total_usd,
        "active_seconds": total_active_seconds,
        "event_count": total_event_count,
        "request_count": total_request_count,
        "pricing": pricing_metadata(),
        "daily": sorted(daily_rows, key=lambda item: item["date"]),
        "sources": sorted(
            sources.values(),
            key=lambda item: (usage_total(item["usage"]), item["event_count"], item["active_seconds"]),
            reverse=True,
        ),
        "projects": sorted(
            projects.values(),
            key=lambda item: (usage_total(item["usage"]), item["event_count"], item["active_seconds"]),
            reverse=True,
        ),
        "models": sorted(
            models.values(),
            key=lambda item: (usage_total(item["usage"]), item["event_count"], item["active_seconds"]),
            reverse=True,
        ),
    }


def serializable_thread(thread: dict[str, Any]) -> dict[str, Any]:
    result = dict(thread)
    result["started_at"] = thread["started_at"].isoformat() if thread.get("started_at") else None
    result["ended_at"] = thread["ended_at"].isoformat() if thread.get("ended_at") else None
    result.pop("event_timestamps", None)
    return result


def serializable_summary(summary: dict[str, Any]) -> dict[str, Any]:
    result = dict(summary)
    result["generated_at"] = summary["generated_at"].isoformat()
    return result


def number(value: Any) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def minutes(seconds: int | float) -> str:
    return f"{(float(seconds) / 60.0):,.1f}"


def percent(value: float) -> str:
    return f"{value:.1f}%"


def cache_hit_rate(usage: dict[str, int]) -> float:
    input_tokens = int(usage.get("input_tokens", 0))
    if input_tokens <= 0:
        return 0.0
    return (int(usage.get("cached_input_tokens", 0)) / input_tokens) * 100.0


def output_ratio(usage: dict[str, int]) -> float:
    total_tokens = int(usage.get("total_tokens", 0))
    if total_tokens <= 0:
        return 0.0
    return (int(usage.get("output_tokens", 0)) / total_tokens) * 100.0


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def flatten_thread_for_csv(thread: dict[str, Any], report_tz: Any = None) -> dict[str, Any]:
    usage = thread["usage"]
    return {
        "thread_id": thread["thread_id"],
        "app": app_label(thread_app(thread)),
        "source": thread.get("source", ""),
        "title": thread.get("title", ""),
        "project": thread.get("project", ""),
        "cwd": thread.get("cwd", ""),
        "model": thread.get("model", ""),
        "reasoning_effort": thread.get("reasoning_effort", ""),
        "started_at": fmt_dt(thread.get("started_at"), report_tz),
        "ended_at": fmt_dt(thread.get("ended_at"), report_tz),
        "active_minutes_est": round(float(thread.get("active_seconds") or 0) / 60.0, 2),
        "input_tokens": usage["input_tokens"],
        "cached_input_tokens": usage["cached_input_tokens"],
        "output_tokens": usage["output_tokens"],
        "reasoning_output_tokens": usage["reasoning_output_tokens"],
        "total_tokens": usage["total_tokens"],
        "estimated_codex_credits": round(thread.get("estimated_codex_credits") or 0.0, 4),
        "estimated_api_usd_equiv": round(thread.get("estimated_api_usd_equiv") or 0.0, 4),
        "event_count": thread.get("event_count", thread.get("line_count", "")),
        "request_count": thread.get("request_count", ""),
        "path": thread.get("path", ""),
    }


def flatten_thread_for_cli(thread: dict[str, Any], report_tz: Any = None) -> dict[str, Any]:
    usage = thread["usage"]
    return {
        "thread_id": str(thread.get("thread_id") or "")[:12],
        "app": app_label(thread_app(thread)),
        "title": thread.get("title") or "(untitled)",
        "project": thread.get("project", ""),
        "model": thread.get("model", ""),
        "ended_at": fmt_dt(thread.get("ended_at"), report_tz),
        "active_min": round(float(thread.get("active_seconds") or 0) / 60.0, 1),
        "tokens": usage_total(usage),
        "input": usage["input_tokens"],
        "cached": usage["cached_input_tokens"],
        "output": usage["output_tokens"],
        "cache_hit": percent(cache_hit_rate(usage)),
        "credits": round(thread.get("estimated_codex_credits") or 0.0, 2),
        "api_usd": round(thread.get("estimated_api_usd_equiv") or 0.0, 2),
    }


def flatten_group_for_csv(row: dict[str, Any], group_fields: dict[str, Any]) -> dict[str, Any]:
    usage = row["usage"]
    result = dict(group_fields)
    result.update({
        "thread_count": row.get("thread_count", ""),
        "event_count": row.get("event_count", ""),
        "request_count": row.get("request_count", ""),
        "active_minutes_est": round(float(row.get("active_seconds") or 0) / 60.0, 2),
        "input_tokens": usage["input_tokens"],
        "cached_input_tokens": usage["cached_input_tokens"],
        "output_tokens": usage["output_tokens"],
        "reasoning_output_tokens": usage["reasoning_output_tokens"],
        "total_tokens": usage["total_tokens"],
        "estimated_codex_credits": round(row.get("estimated_codex_credits") or 0.0, 4),
        "estimated_api_usd_equiv": round(row.get("estimated_api_usd_equiv") or 0.0, 4),
    })
    return result


def flatten_summary_row_for_cli(row: dict[str, Any], group_fields: dict[str, Any]) -> dict[str, Any]:
    usage = row["usage"]
    result = dict(group_fields)
    result.update({
        "threads": row.get("thread_count", ""),
        "events": row.get("event_count", ""),
        "requests": row.get("request_count", ""),
        "active_min": round(float(row.get("active_seconds") or 0) / 60.0, 1),
        "tokens": usage_total(usage),
        "input": usage["input_tokens"],
        "cached": usage["cached_input_tokens"],
        "output": usage["output_tokens"],
        "cache_hit": percent(cache_hit_rate(usage)),
        "credits": round(row.get("estimated_codex_credits") or 0.0, 2),
        "api_usd": round(row.get("estimated_api_usd_equiv") or 0.0, 2),
    })
    return result


def period_key(date_value: str, period: str) -> str:
    day = datetime.strptime(date_value, "%Y-%m-%d").date()
    if period == "weekly":
        iso_year, iso_week, _ = day.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if period == "monthly":
        return f"{day.year:04d}-{day.month:02d}"
    return date_value


def grouped_daily_rows(summary: dict[str, Any], period: str) -> list[dict[str, Any]]:
    if period == "daily":
        return [
            flatten_summary_row_for_cli(row, {"date": row["date"]})
            for row in sorted(summary["daily"], key=lambda item: item["date"], reverse=True)
        ]

    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "period": "",
        "usage": zero_usage(),
        "estimated_codex_credits": 0.0,
        "estimated_api_usd_equiv": 0.0,
        "active_seconds": 0,
        "thread_count": 0,
    })
    for row in summary["daily"]:
        key = period_key(row["date"], period)
        grouped_row = grouped[key]
        grouped_row["period"] = key
        add_usage(grouped_row["usage"], row["usage"])
        grouped_row["estimated_codex_credits"] += row.get("estimated_codex_credits") or 0.0
        grouped_row["estimated_api_usd_equiv"] += row.get("estimated_api_usd_equiv") or 0.0
        grouped_row["active_seconds"] += int(row.get("active_seconds") or 0)
        grouped_row["thread_count"] += int(row.get("thread_count") or 0)

    return [
        flatten_summary_row_for_cli(row, {"period": row["period"]})
        for row in sorted(grouped.values(), key=lambda item: item["period"], reverse=True)
    ]


def validate_refresh_seconds(value: int) -> int:
    if value < 2:
        raise ValueError("refresh interval must be at least 2 seconds")
    return value


def parse_refresh_seconds(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("refresh interval must be an integer") from exc
    try:
        return validate_refresh_seconds(seconds)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def latest_thread_activity(threads: list[dict[str, Any]]) -> datetime | None:
    latest: datetime | None = None
    for thread in threads:
        activity = thread.get("ended_at") or thread.get("started_at")
        if isinstance(activity, datetime) and (latest is None or activity > latest):
            latest = activity
    return latest


def app_label_to_key(label: str) -> str:
    for key, app_label_value in SOURCE_LABELS.items():
        if app_label_value == label:
            return key
    return normalize_app(label)


def estimate_app_day_usd(app: str, usage: dict[str, int], model: str | None) -> float:
    if app == "codex":
        return estimate_amount(usage, model, "api_usd_standard_short") or 0.0
    if app == "claude":
        return estimate_amount(usage, model, "anthropic_usd") or 0.0
    if app == "cursor":
        return cursor_estimated_usd(usage, model)
    return 0.0


def build_provider_summaries(
    threads: list[dict[str, Any]],
    summary: dict[str, Any],
    report_tz: Any = None,
) -> list[dict[str, Any]]:
    today = local_day(datetime.now(timezone.utc), report_tz)
    today_usage: dict[str, dict[str, int]] = {app: zero_usage() for app in SUPPORTED_SOURCES}
    today_usd: dict[str, float] = {app: 0.0 for app in SUPPORTED_SOURCES}

    for thread in threads:
        app = thread_app(thread)
        if app not in today_usage:
            continue
        model = thread.get("model")
        for day, usage in (thread.get("daily_usage") or {}).items():
            if day != today:
                continue
            add_usage(today_usage[app], usage)
            today_usd[app] += estimate_app_day_usd(app, usage, model)

    source_by_app: dict[str, dict[str, Any]] = {}
    for row in summary.get("sources", []):
        source_by_app[app_label_to_key(str(row.get("app") or ""))] = row

    providers: list[dict[str, Any]] = []
    for app in SUPPORTED_SOURCES:
        row = source_by_app.get(app, {})
        usage = row.get("usage") or zero_usage()
        lifetime_tokens = usage_total(usage)
        today_usage_row = today_usage.get(app, zero_usage())
        if app == "codex":
            token_basis = "exact"
        elif app == "claude":
            token_basis = "exact"
        elif app == "cursor":
            token_basis = "context_cache_estimated" if lifetime_tokens else "activity_only"
        else:
            token_basis = "unknown"
        providers.append({
            "app": app_label(app),
            "app_key": app,
            "lifetime_tokens": lifetime_tokens,
            "lifetime_input_tokens": int(usage.get("input_tokens", 0)),
            "lifetime_cached_tokens": int(usage.get("cached_input_tokens", 0)),
            "lifetime_output_tokens": int(usage.get("output_tokens", 0)),
            "today_tokens": usage_total(today_usage_row),
            "today_input_tokens": int(today_usage_row.get("input_tokens", 0)),
            "today_output_tokens": int(today_usage_row.get("output_tokens", 0)),
            "lifetime_usd": float(row.get("estimated_api_usd_equiv") or 0.0),
            "today_usd": today_usd.get(app, 0.0),
            "lifetime_credits": float(row.get("estimated_codex_credits") or 0.0),
            "threads": int(row.get("thread_count") or 0),
            "requests": int(row.get("request_count") or 0),
            "events": int(row.get("event_count") or 0),
            "active_seconds": int(row.get("active_seconds") or 0),
            "active_minutes": minutes(int(row.get("active_seconds") or 0)),
            "token_basis": token_basis,
        })
    return providers


def build_combined_totals(summary: dict[str, Any], provider_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    usage = summary.get("usage") or zero_usage()
    today_tokens = sum(int(row.get("today_tokens") or 0) for row in provider_summaries)
    today_input = sum(int(row.get("today_input_tokens") or 0) for row in provider_summaries)
    today_output = sum(int(row.get("today_output_tokens") or 0) for row in provider_summaries)
    today_usd = sum(float(row.get("today_usd") or 0.0) for row in provider_summaries)
    return {
        "app": "All Apps Combined",
        "app_key": "all",
        "lifetime_tokens": usage_total(usage),
        "lifetime_input_tokens": int(usage.get("input_tokens", 0)),
        "lifetime_cached_tokens": int(usage.get("cached_input_tokens", 0)),
        "lifetime_output_tokens": int(usage.get("output_tokens", 0)),
        "today_tokens": today_tokens,
        "today_input_tokens": today_input,
        "today_output_tokens": today_output,
        "lifetime_usd": float(summary.get("estimated_api_usd_equiv") or 0.0),
        "today_usd": today_usd,
        "lifetime_credits": float(summary.get("estimated_codex_credits") or 0.0),
        "threads": int(summary.get("thread_count") or 0),
        "requests": int(summary.get("request_count") or 0),
        "events": int(summary.get("event_count") or 0),
        "active_seconds": int(summary.get("active_seconds") or 0),
        "active_minutes": minutes(int(summary.get("active_seconds") or 0)),
        "token_basis": "mixed",
    }


def build_gui_view_model(
    threads: list[dict[str, Any]],
    summary: dict[str, Any],
    report_tz: Any = None,
    previous_total_tokens: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    total_tokens = usage_total(summary["usage"])
    token_delta = None if previous_total_tokens is None else total_tokens - previous_total_tokens
    latest_activity = latest_thread_activity(threads)
    is_active = bool(latest_activity and now - latest_activity <= timedelta(minutes=15))
    top_project = summary["projects"][0]["project"] if summary["projects"] else "(none)"
    top_app = summary["sources"][0]["app"] if summary.get("sources") else "(none)"
    top_model = summary["models"][0]["model"] if summary["models"] else "(none)"
    alerts = summary.get("alerts") or build_usage_alerts(summary)
    billing_connectors = summary.get("billing_connectors") or build_billing_connectors(fetch=False)
    provider_summaries = build_provider_summaries(threads, summary, report_tz=report_tz)
    combined_totals = build_combined_totals(summary, provider_summaries)
    provider_today_tokens = {row["app_key"]: row["today_tokens"] for row in provider_summaries}

    daily_rows = [
        (
            row["date"],
            number(usage_total(row["usage"])),
            number(row["usage"]["input_tokens"]),
            number(row["usage"]["cached_input_tokens"]),
            number(row["usage"]["output_tokens"]),
            percent(cache_hit_rate(row["usage"])),
            number(row["estimated_codex_credits"]),
            f"${number(row['estimated_api_usd_equiv'])}",
            minutes(row["active_seconds"]),
        )
        for row in sorted(summary["daily"], key=lambda item: item["date"], reverse=True)
    ]

    project_rows = [
        (
            row["app"],
            row["project"],
            row["cwd"],
            number(row["thread_count"]),
            number(row.get("request_count", 0)),
            number(row.get("event_count", 0)),
            number(usage_total(row["usage"])),
            percent(cache_hit_rate(row["usage"])),
            number(row["estimated_codex_credits"]),
            f"${number(row['estimated_api_usd_equiv'])}",
            minutes(row["active_seconds"]),
        )
        for row in summary["projects"]
    ]

    model_rows = [
        (
            row["app"],
            row["model"],
            number(row["thread_count"]),
            number(row.get("request_count", 0)),
            number(usage_total(row["usage"])),
            number(row["usage"]["cached_input_tokens"]),
            percent(output_ratio(row["usage"])),
            number(row["estimated_codex_credits"]),
            f"${number(row['estimated_api_usd_equiv'])}",
        )
        for row in summary["models"]
    ]

    thread_rows = [
        (
            app_label(thread_app(thread)),
            thread.get("title") or "(untitled)",
            thread.get("project") or "",
            thread.get("model") or "",
            fmt_dt(thread.get("ended_at"), report_tz),
            number(thread.get("request_count", 0)),
            number(thread.get("event_count", thread.get("line_count", 0))),
            number(usage_total(thread["usage"])),
            percent(cache_hit_rate(thread["usage"])),
            number(thread.get("estimated_codex_credits") or 0.0),
            f"${number(thread.get('estimated_api_usd_equiv') or 0.0)}",
            minutes(thread.get("active_seconds") or 0),
        )
        for thread in sorted(
            threads,
            key=lambda item: item.get("ended_at") or item.get("started_at") or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
    ]

    source_rows = [
        (
            row["app"],
            number(row["thread_count"]),
            number(row.get("request_count", 0)),
            number(row.get("event_count", 0)),
            number(usage_total(row["usage"])),
            number(row["usage"]["input_tokens"]),
            number(row["usage"]["cached_input_tokens"]),
            number(row["usage"]["output_tokens"]),
            number(row["estimated_codex_credits"]),
            f"${number(row['estimated_api_usd_equiv'])}",
            minutes(row["active_seconds"]),
        )
        for row in summary.get("sources", [])
    ]

    provider_rows = [
        (
            row["app"],
            number(row["lifetime_tokens"]),
            number(row["lifetime_input_tokens"]),
            number(row["lifetime_cached_tokens"]),
            number(row["lifetime_output_tokens"]),
            number(row["today_tokens"]),
            f"${number(row['lifetime_usd'])}",
            f"${number(row['today_usd'])}",
            number(row["lifetime_credits"]),
            number(row["threads"]),
            number(row["requests"]),
            number(row["events"]),
            row["active_minutes"],
            row["token_basis"],
        )
        for row in provider_summaries
    ]
    lifetime_rows = provider_rows + [
        (
            combined_totals["app"],
            number(combined_totals["lifetime_tokens"]),
            number(combined_totals["lifetime_input_tokens"]),
            number(combined_totals["lifetime_cached_tokens"]),
            number(combined_totals["lifetime_output_tokens"]),
            number(combined_totals["today_tokens"]),
            f"${number(combined_totals['lifetime_usd'])}",
            f"${number(combined_totals['today_usd'])}",
            number(combined_totals["lifetime_credits"]),
            number(combined_totals["threads"]),
            number(combined_totals["requests"]),
            number(combined_totals["events"]),
            combined_totals["active_minutes"],
            combined_totals["token_basis"],
        ),
    ]

    truncated_tables: dict[str, tuple[int, int]] = {}

    def limit_gui_rows(table_key: str, rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
        limit = GUI_TABLE_ROW_LIMITS.get(table_key)
        if limit is not None and len(rows) > limit:
            truncated_tables[table_key] = (limit, len(rows))
            return rows[:limit]
        return rows

    daily_rows = limit_gui_rows("daily", daily_rows)
    project_rows = limit_gui_rows("projects", project_rows)
    thread_rows = limit_gui_rows("threads", thread_rows)

    return {
        "generated_at": fmt_dt(summary["generated_at"], report_tz),
        "latest_activity": fmt_dt(latest_activity, report_tz) if latest_activity else "(none)",
        "activity_status": "Active" if is_active else "Inactive",
        "token_delta": "" if token_delta is None else f"{token_delta:+,}",
        "total_tokens": total_tokens,
        "pricing": summary.get("pricing") or pricing_metadata(),
        "alerts": alerts,
        "billing_connectors": billing_connectors,
        "metrics": [
            ("All apps lifetime tokens", number(combined_totals["lifetime_tokens"])),
            ("Codex lifetime tokens", number(next((row["lifetime_tokens"] for row in provider_summaries if row["app_key"] == "codex"), 0))),
            ("Claude lifetime tokens", number(next((row["lifetime_tokens"] for row in provider_summaries if row["app_key"] == "claude"), 0))),
            ("Cursor lifetime tokens", number(next((row["lifetime_tokens"] for row in provider_summaries if row["app_key"] == "cursor"), 0))),
            ("All apps today tokens", number(combined_totals["today_tokens"])),
            ("Threads", number(summary["thread_count"])),
            ("Estimated USD (lifetime)", f"${number(combined_totals['lifetime_usd'])}"),
            ("Estimated USD (today)", f"${number(combined_totals['today_usd'])}"),
            ("Estimated Codex credits", number(combined_totals["lifetime_credits"])),
            ("Estimated active time", f"{combined_totals['active_minutes']} min"),
            ("Events", number(summary.get("event_count", 0))),
            ("Requests", number(summary.get("request_count", 0))),
            ("Top app", top_app),
            ("Top project", top_project),
            ("Top model", top_model),
        ],
        "provider_summaries": provider_summaries,
        "combined_totals": combined_totals,
        "truncated_tables": truncated_tables,
        "charts": {
            "sources": [
                {
                    "label": row["app"],
                    "value": usage_total(row["usage"]),
                    "detail": (
                        f"{number(provider_today_tokens.get(app_label_to_key(row['app']), 0))} today"
                        if provider_today_tokens.get(app_label_to_key(row["app"]), 0)
                        else f"{row['thread_count']} threads"
                    ),
                }
                for row in summary.get("sources", [])[:6]
            ],
            "daily": [
                {"label": row["date"], "value": usage_total(row["usage"]), "detail": f"{minutes(row['active_seconds'])} min"}
                for row in summary["daily"][-6:]
            ],
            "projects": [
                {"label": row["project"], "value": usage_total(row["usage"]), "detail": f"{row['thread_count']} threads"}
                for row in summary["projects"][:6]
            ],
            "models": [
                {"label": row["model"], "value": usage_total(row["usage"]), "detail": f"{row['thread_count']} threads"}
                for row in summary["models"][:6]
            ],
        },
        "tables": {
            "providers": {
                "columns": (
                    ("app", "App"),
                    ("lifetime_tokens", "Lifetime tokens"),
                    ("lifetime_input", "Lifetime input"),
                    ("lifetime_cached", "Lifetime cached"),
                    ("lifetime_output", "Lifetime output"),
                    ("today_tokens", "Today tokens"),
                    ("lifetime_usd", "Lifetime USD"),
                    ("today_usd", "Today USD"),
                    ("credits", "Codex credits"),
                    ("threads", "Threads"),
                    ("requests", "Requests"),
                    ("events", "Events"),
                    ("active", "Active min"),
                    ("basis", "Token basis"),
                ),
                "rows": lifetime_rows,
            },
            "sources": {
                "columns": (
                    ("app", "App"),
                    ("threads", "Threads"),
                    ("requests", "Requests"),
                    ("events", "Events"),
                    ("tokens", "Tokens"),
                    ("input", "Input"),
                    ("cached", "Cached input"),
                    ("output", "Output"),
                    ("credits", "Credits"),
                    ("api_usd", "USD"),
                    ("active", "Active min"),
                ),
                "rows": source_rows,
            },
            "daily": {
                "columns": (
                    ("date", "Date"),
                    ("total", "Total"),
                    ("input", "Input"),
                    ("cached", "Cached input"),
                    ("output", "Output"),
                    ("cache_hit", "Cache hit"),
                    ("credits", "Credits"),
                    ("api_usd", "USD"),
                    ("active", "Active min"),
                ),
                "rows": daily_rows,
            },
            "projects": {
                "columns": (
                    ("app", "App"),
                    ("project", "Project"),
                    ("folder", "Folder"),
                    ("threads", "Threads"),
                    ("requests", "Requests"),
                    ("events", "Events"),
                    ("tokens", "Tokens"),
                    ("cache_hit", "Cache hit"),
                    ("credits", "Credits"),
                    ("api_usd", "USD"),
                    ("active", "Active min"),
                ),
                "rows": project_rows,
            },
            "models": {
                "columns": (
                    ("app", "App"),
                    ("model", "Model"),
                    ("threads", "Threads"),
                    ("requests", "Requests"),
                    ("tokens", "Tokens"),
                    ("cached", "Cached input"),
                    ("output_share", "Output share"),
                    ("credits", "Credits"),
                    ("api_usd", "USD"),
                ),
                "rows": model_rows,
            },
            "threads": {
                "columns": (
                    ("app", "App"),
                    ("title", "Thread"),
                    ("project", "Project"),
                    ("model", "Model"),
                    ("last_activity", "Last activity"),
                    ("requests", "Requests"),
                    ("events", "Events"),
                    ("tokens", "Tokens"),
                    ("cache_hit", "Cache hit"),
                    ("credits", "Credits"),
                    ("api_usd", "USD"),
                    ("active", "Active min"),
                ),
                "rows": thread_rows,
            },
            "alerts": {
                "columns": (
                    ("severity", "Severity"),
                    ("title", "Signal"),
                    ("detail", "Detail"),
                ),
                "rows": tuple((alert.get("severity", ""), alert.get("title", ""), alert.get("detail", "")) for alert in alerts),
            },
            "billing": {
                "columns": (
                    ("provider", "Provider"),
                    ("status", "Status"),
                    ("period", "Period"),
                    ("official_usd", "Official USD"),
                    ("env_var", "Env var"),
                    ("blocked", "Blocked"),
                ),
                "rows": tuple(
                    (
                        row.get("provider", ""),
                        row.get("status", ""),
                        row.get("period", ""),
                        str(row.get("official_usd", "")),
                        row.get("env_var", ""),
                        row.get("blocked", ""),
                    )
                    for row in billing_connectors
                ),
            },
        },
    }


def emit_rows(rows: list[dict[str, Any]], columns: list[tuple[str, str]], args: argparse.Namespace) -> int:
    output_format = getattr(args, "format", "table")
    limit = getattr(args, "limit", None)
    if isinstance(limit, int) and limit > 0:
        rows = rows[:limit]

    if output_format == "json":
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0

    fieldnames = [key for key, _ in columns]
    if output_format == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})
        return 0

    if not rows:
        print("(no rows)")
        return 0

    compact = bool(getattr(args, "compact", False))
    max_width = 18 if compact else 34

    def cell(value: Any) -> str:
        text = str(value)
        if len(text) > max_width:
            return text[: max_width - 3] + "..."
        return text

    widths: dict[str, int] = {}
    for key, label in columns:
        widths[key] = len(label)
        for row in rows:
            widths[key] = max(widths[key], len(cell(row.get(key, ""))))

    header = "  ".join(label.ljust(widths[key]) for key, label in columns)
    rule = "  ".join("-" * widths[key] for key, _ in columns)
    print(header)
    print(rule)
    for row in rows:
        print("  ".join(cell(row.get(key, "")).ljust(widths[key]) for key, _ in columns))
    return 0


def render_dashboard(output_path: Path, threads: list[dict[str, Any]], summary: dict[str, Any], report_tz: Any = None) -> None:
    recent_threads = sorted(threads, key=lambda item: usage_total(item["usage"]), reverse=True)[:25]
    daily_rows = summary["daily"][-45:]
    source_rows = summary.get("sources", [])
    project_rows = summary["projects"][:20]
    model_rows = summary["models"]

    max_daily_tokens = max([usage_total(row["usage"]) for row in daily_rows] or [1])

    def tr(cells: list[str]) -> str:
        return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"

    def empty_row(colspan: int) -> str:
        return f"<tr><td colspan=\"{colspan}\" class=\"empty\">No data in this range.</td></tr>"

    daily_html = "\n".join(
        tr([
            html.escape(row["date"]),
            f"<div class=\"bar\"><span style=\"width:{max(2, usage_total(row['usage']) / max_daily_tokens * 100):.1f}%\"></span></div>",
            number(usage_total(row["usage"])),
            number(row["usage"]["input_tokens"]),
            number(row["usage"]["cached_input_tokens"]),
            number(row["usage"]["output_tokens"]),
            percent(cache_hit_rate(row["usage"])),
            number(row["estimated_codex_credits"]),
            f"${number(row['estimated_api_usd_equiv'])}",
            minutes(row["active_seconds"]),
        ])
        for row in reversed(daily_rows)
    ) or empty_row(11)

    source_html = "\n".join(
        tr([
            html.escape(row["app"]),
            number(row["thread_count"]),
            number(row.get("request_count", 0)),
            number(row.get("event_count", 0)),
            number(usage_total(row["usage"])),
            number(row["usage"]["input_tokens"]),
            number(row["usage"]["cached_input_tokens"]),
            number(row["usage"]["output_tokens"]),
            number(row["estimated_codex_credits"]),
            f"${number(row['estimated_api_usd_equiv'])}",
            minutes(row["active_seconds"]),
        ])
        for row in source_rows
    ) or empty_row(12)

    project_html = "\n".join(
        tr([
            html.escape(row["app"]),
            html.escape(row["project"]),
            html.escape(row["cwd"]),
            number(row["thread_count"]),
            number(row.get("request_count", 0)),
            number(row.get("event_count", 0)),
            number(usage_total(row["usage"])),
            percent(cache_hit_rate(row["usage"])),
            number(row["estimated_codex_credits"]),
            f"${number(row['estimated_api_usd_equiv'])}",
            minutes(row["active_seconds"]),
        ])
        for row in project_rows
    ) or empty_row(10)

    model_html = "\n".join(
        tr([
            html.escape(row["app"]),
            html.escape(row["model"]),
            number(row["thread_count"]),
            number(row.get("request_count", 0)),
            number(usage_total(row["usage"])),
            number(row["usage"]["cached_input_tokens"]),
            number(row["usage"]["output_tokens"]),
            percent(output_ratio(row["usage"])),
            number(row["estimated_codex_credits"]),
            f"${number(row['estimated_api_usd_equiv'])}",
        ])
        for row in model_rows
    ) or empty_row(10)

    trend_rows = daily_rows[-14:]
    max_trend_tokens = max([usage_total(row["usage"]) for row in trend_rows] or [1])
    trend_html = "\n".join(
        (
            '<div class="trend-row">'
            f'<span>{html.escape(row["date"])}</span>'
            '<div class="trend-track">'
            f'<i style="width:{max(4, usage_total(row["usage"]) / max_trend_tokens * 100):.1f}%"></i>'
            '</div>'
            f'<strong>{number(usage_total(row["usage"]))}</strong>'
            '</div>'
        )
        for row in trend_rows
    ) or '<p class="empty-copy">No trend data in this range.</p>'

    alerts = summary.get("alerts") or build_usage_alerts(summary)
    alert_html = "\n".join(
        (
            f'<div class="signal signal-{html.escape(alert.get("severity", "info"))}">'
            f'<strong>{html.escape(alert.get("title", ""))}</strong>'
            f'<span>{html.escape(alert.get("detail", ""))}</span>'
            '</div>'
        )
        for alert in alerts
    )

    def provider_slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "provider"

    provider_buttons = "\n".join(
        f'<button class="tab-button{" is-active" if index == 0 else ""}" type="button" data-provider-tab="{provider_slug(row["app"])}">{html.escape(row["app"])}</button>'
        for index, row in enumerate(source_rows)
    )
    provider_cards = "\n".join(
        (
            f'<article class="provider-card{" is-active" if index == 0 else ""}" data-provider-card="{provider_slug(row["app"])}">'
            f'<span>{html.escape(row["app"])}</span>'
            f'<strong>{number(usage_total(row["usage"]))} tokens</strong>'
            f'<p>{number(row["thread_count"])} threads, {number(row.get("request_count", 0))} requests, {minutes(row["active_seconds"])} active minutes.</p>'
            f'<p>Estimated USD: ${number(row["estimated_api_usd_equiv"])}. Estimated Codex credits: {number(row["estimated_codex_credits"])}.</p>'
            '</article>'
        )
        for index, row in enumerate(source_rows)
    ) or '<p class="empty-copy">No provider data in this range.</p>'

    connector_rows = summary.get("billing_connectors") or build_billing_connectors(fetch=False)
    connector_html = "\n".join(
        (
            '<div class="connector-row">'
            f'<span>{html.escape(row.get("provider", ""))}</span>'
            f'<strong>{html.escape(row.get("status", ""))}</strong>'
            f'<em>{html.escape(row.get("env_var", ""))}</em>'
            '</div>'
        )
        for row in connector_rows
    )

    thread_html = "\n".join(
        tr([
            html.escape(app_label(thread_app(thread))),
            html.escape(thread.get("title") or "(untitled)"),
            html.escape(thread.get("project") or ""),
            html.escape(thread.get("model") or ""),
            html.escape(fmt_dt(thread.get("ended_at"), report_tz)),
            number(thread.get("request_count", 0)),
            number(thread.get("event_count", thread.get("line_count", 0))),
            number(usage_total(thread["usage"])),
            percent(cache_hit_rate(thread["usage"])),
            number(thread.get("estimated_codex_credits") or 0.0),
            f"${number(thread.get('estimated_api_usd_equiv') or 0.0)}",
            minutes(thread.get("active_seconds") or 0),
        ])
        for thread in recent_threads
    ) or empty_row(11)

    generated_at = fmt_dt(summary["generated_at"], report_tz)
    total_tokens = usage_total(summary["usage"])
    top_project = summary["projects"][0]["project"] if summary["projects"] else "(none)"
    top_app = source_rows[0]["app"] if source_rows else "(none)"
    top_model = summary["models"][0]["model"] if summary["models"] else "(none)"
    pricing = summary.get("pricing") or pricing_metadata()
    repo_url = "https://github.com/SuvenSeo/ai-coding-usage-tracker"
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Coding Usage Dashboard</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #101114;
      --panel: #181b20;
      --ink: #f5f7fa;
      --muted: #a8b0bd;
      --subtle: #252b33;
      --border: #333a45;
      --blue: #58a6ff;
      --green: #36c58c;
      --amber: #f1b84b;
      --rose: #ff7b72;
      --code: #d7e2ff;
      --warning-bg: #302614;
      --warning-ink: #f4d28f;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      font-family: Segoe UI, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      min-height: 100vh;
      background:
        linear-gradient(180deg, #08090d 0%, var(--bg) 42%, #0d1117 100%);
      color: var(--ink);
      line-height: 1.45;
      overflow-x: hidden;
    }}
    header, main, footer {{ width: min(100%, 1280px); margin: 0 auto; padding: 24px; }}
    header {{ padding-top: 34px; }}
    .eyebrow {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 14px;
    }}
    .pill {{
      border: 1px solid var(--border);
      background: color-mix(in srgb, var(--panel) 78%, transparent);
      border-radius: 999px;
      color: var(--muted);
      font-size: 12px;
      padding: 5px 10px;
      transition: border-color 180ms ease, color 180ms ease, transform 180ms ease;
      backdrop-filter: blur(16px);
    }}
    .pill:hover {{
      border-color: var(--blue);
      color: var(--ink);
      transform: translateY(-1px);
    }}
    h1 {{ margin: 0; font-size: 32px; letter-spacing: 0; }}
    h2 {{ margin: 28px 0 12px; font-size: 18px; letter-spacing: 0; }}
    p {{ color: var(--muted); margin: 6px 0 0; max-width: 760px; overflow-wrap: anywhere; }}
    section, .metrics, .insights, .toolbar, .panel {{ min-width: 0; }}
    .metrics, .insights {{
      display: grid;
      gap: 12px;
      margin-top: 20px;
    }}
    .metrics {{ grid-template-columns: repeat(5, minmax(0, 1fr)); }}
    .insights {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
    .metric, .insight {{
      position: relative;
      overflow: hidden;
      background:
        linear-gradient(145deg, rgba(255,255,255,0.055), rgba(255,255,255,0.014)),
        var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
      min-height: 92px;
      box-shadow: 0 18px 44px rgba(0,0,0,0.24);
      animation: card-rise 420ms cubic-bezier(.2,.8,.2,1) both;
      transition: border-color 180ms ease, box-shadow 180ms ease, transform 180ms ease;
    }}
    .metric::before, .insight::before {{
      content: "";
      position: absolute;
      inset: 0 0 auto 0;
      height: 1px;
      background: linear-gradient(90deg, transparent, rgba(255,255,255,0.34), transparent);
      opacity: .72;
    }}
    .metric:hover, .insight:hover {{
      border-color: color-mix(in srgb, var(--blue) 45%, var(--border));
      box-shadow: 0 24px 58px rgba(0,0,0,0.34);
      transform: translateY(-2px);
    }}
    .metric:nth-child(2), .insight:nth-child(2) {{ animation-delay: 35ms; }}
    .metric:nth-child(3), .insight:nth-child(3) {{ animation-delay: 70ms; }}
    .metric:nth-child(4), .insight:nth-child(4) {{ animation-delay: 105ms; }}
    .metric:nth-child(5), .insight:nth-child(5) {{ animation-delay: 140ms; }}
    .metric:nth-child(6) {{ animation-delay: 175ms; }}
    .metric:nth-child(7) {{ animation-delay: 210ms; }}
    .metric:nth-child(8) {{ animation-delay: 245ms; }}
    .metric span, .insight span {{ display: block; color: var(--muted); font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 8px; font-size: 22px; }}
    .insight strong {{ display: block; margin-top: 8px; font-size: 17px; overflow-wrap: anywhere; }}
    .metric:nth-child(2) strong {{ color: var(--blue); }}
    .metric:nth-child(3) strong {{ color: var(--green); }}
    .metric:nth-child(4) strong {{ color: var(--amber); }}
    .metric:nth-child(5) strong {{ color: var(--rose); }}
    .feature-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1.35fr) minmax(280px, .65fr);
      gap: 14px;
      margin: 26px 0 18px;
    }}
    .feature-panel {{
      background: linear-gradient(145deg, rgba(255,255,255,0.05), rgba(255,255,255,0.012)), var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
      box-shadow: 0 18px 44px rgba(0,0,0,0.22);
    }}
    .feature-panel h2 {{ margin-top: 0; }}
    .trend-row {{
      display: grid;
      grid-template-columns: 96px minmax(0, 1fr) 96px;
      align-items: center;
      gap: 10px;
      min-height: 28px;
      color: var(--muted);
      font-size: 12px;
    }}
    .trend-row strong {{ color: var(--ink); text-align: right; }}
    .trend-track {{ height: 8px; border-radius: 999px; background: var(--subtle); overflow: hidden; }}
    .trend-track i {{
      display: block;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--blue), var(--green));
      animation: bar-fill 720ms cubic-bezier(.2,.8,.2,1) both;
      transform-origin: left center;
    }}
    .signal {{
      display: grid;
      gap: 4px;
      padding: 9px 0;
      border-bottom: 1px solid var(--border);
    }}
    .signal:last-child {{ border-bottom: 0; }}
    .signal strong {{ font-size: 13px; }}
    .signal span {{ color: var(--muted); font-size: 12px; }}
    .signal-risk strong {{ color: var(--rose); }}
    .signal-warn strong {{ color: var(--amber); }}
    .signal-ok strong {{ color: var(--green); }}
    .signal-info strong {{ color: var(--blue); }}
    .provider-tabs {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 4px 0 12px;
    }}
    .tab-button {{
      border: 1px solid var(--border);
      border-radius: 999px;
      background: transparent;
      color: var(--muted);
      cursor: pointer;
      font: inherit;
      font-size: 12px;
      padding: 7px 11px;
      transition: background 160ms ease, border-color 160ms ease, color 160ms ease, transform 160ms ease;
    }}
    .tab-button:hover, .tab-button.is-active {{
      background: var(--panel_alt);
      border-color: var(--blue);
      color: var(--ink);
      transform: translateY(-1px);
    }}
    .provider-card {{ display: none; }}
    .provider-card.is-active {{ display: block; }}
    .provider-card span, .provider-card p {{ color: var(--muted); }}
    .provider-card strong {{ display: block; margin: 4px 0 6px; font-size: 22px; }}
    .connector-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }}
    .connector-row {{
      display: grid;
      gap: 4px;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 11px;
      background: rgba(255,255,255,0.018);
    }}
    .connector-row span {{ color: var(--muted); font-size: 12px; }}
    .connector-row strong {{ font-size: 15px; }}
    .connector-row em {{ color: var(--code); font-style: normal; font-size: 12px; overflow-wrap: anywhere; }}
    .empty-copy {{ color: var(--muted); }}
    .toolbar {{
      position: sticky;
      top: 0;
      z-index: 10;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin: 24px 0 8px;
      padding: 12px 0;
      background: rgba(16,17,20,0.82);
      backdrop-filter: blur(22px);
    }}
    .toolbar h2 {{ margin: 0; }}
    .search-wrap {{
      display: grid;
      grid-template-columns: minmax(220px, 420px) auto;
      gap: 8px;
      align-items: center;
      width: min(520px, 100%);
    }}
    .search {{
      border: 1px solid var(--border);
      border-radius: 7px;
      background: var(--panel);
      color: var(--ink);
      font: inherit;
      padding: 10px 12px;
      transition: border-color 160ms ease, box-shadow 160ms ease, background 160ms ease;
    }}
    .search:focus {{
      outline: 0;
      border-color: var(--blue);
      background: var(--field);
      box-shadow: 0 0 0 4px rgba(88,166,255,0.16);
    }}
    .clear-search {{
      width: 38px;
      height: 38px;
      border: 1px solid var(--border);
      border-radius: 7px;
      background: var(--panel);
      color: var(--muted);
      cursor: pointer;
      font: inherit;
      transition: border-color 160ms ease, color 160ms ease, transform 160ms ease;
    }}
    .clear-search:hover {{
      border-color: var(--rose);
      color: var(--ink);
      transform: translateY(-1px);
    }}
    .search-status {{
      grid-column: 1 / -1;
      color: var(--muted);
      font-size: 12px;
      min-height: 18px;
    }}
    .table-nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 0 0 18px;
    }}
    .table-nav a {{
      border: 1px solid var(--border);
      border-radius: 999px;
      color: var(--code);
      font-size: 12px;
      font-weight: 650;
      padding: 7px 11px;
      text-decoration: none;
      transition: background 160ms ease, border-color 160ms ease, transform 160ms ease;
    }}
    .table-nav a:hover {{
      background: var(--panel_alt);
      border-color: var(--blue);
      transform: translateY(-1px);
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: auto;
      max-width: 100%;
      box-shadow: 0 18px 44px rgba(0,0,0,0.2);
      transition: border-color 180ms ease, box-shadow 180ms ease;
    }}
    .panel:hover {{ border-color: #465160; box-shadow: 0 22px 52px rgba(0,0,0,0.28); }}
    table {{ border-collapse: collapse; width: 100%; min-width: 900px; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; font-size: 13px; }}
    th {{ background: var(--subtle); color: var(--ink); font-weight: 650; position: sticky; top: 0; }}
    tr {{ transition: background 140ms ease; }}
    tbody tr:hover td {{ background: rgba(88,166,255,0.07); }}
    tr:last-child td {{ border-bottom: 0; }}
    .bar {{ width: 140px; height: 8px; background: var(--subtle); border-radius: 999px; overflow: hidden; margin-top: 5px; }}
    .bar span {{
      display: block;
      height: 100%;
      background: linear-gradient(90deg, var(--blue), var(--green));
      animation: bar-fill 720ms cubic-bezier(.2,.8,.2,1) both;
      transform-origin: left center;
    }}
    .empty {{ color: var(--muted); text-align: center; padding: 18px; }}
    .note {{
      margin-top: 24px;
      padding: 14px 16px;
      border-left: 4px solid var(--amber);
      background: var(--warning-bg);
      color: var(--warning-ink);
      border-radius: 6px;
      font-size: 13px;
    }}
    footer {{
      color: var(--muted);
      font-size: 13px;
      padding-bottom: 36px;
    }}
    footer a {{ color: var(--code); font-weight: 650; text-decoration: none; }}
    @keyframes card-rise {{
      from {{ opacity: .82; transform: translateY(8px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    @keyframes bar-fill {{
      from {{ transform: scaleX(.12); opacity: .55; }}
      to {{ transform: scaleX(1); opacity: 1; }}
    }}
    @media (max-width: 980px) {{
      header, main, footer {{ padding: 16px; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .insights {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .feature-grid {{ grid-template-columns: 1fr; }}
      .connector-grid {{ grid-template-columns: 1fr; }}
      .toolbar {{ align-items: flex-start; flex-direction: column; }}
      .search-wrap {{ width: 100%; }}
    }}
    @media (max-width: 560px) {{
      header, main, footer {{ width: 100vw; max-width: 100vw; }}
      .metrics, .insights {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 26px; }}
      .search-wrap {{ grid-template-columns: 1fr auto; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      html {{ scroll-behavior: auto; }}
      *, *::before, *::after {{
        animation-duration: 1ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 1ms !important;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="eyebrow">
      <span class="pill">Local-first</span>
      <span class="pill">Private reports</span>
      <span class="pill">Codex app logs</span>
      <span class="pill">Claude Code logs</span>
      <span class="pill">Cursor tokens</span>
    </div>
    <h1>AI Coding Usage Dashboard</h1>
    <p>Generated {html.escape(generated_at)} from selected local AI coding data. Estimates help with project-level usage, not official billing.</p>
    <section class="metrics" aria-label="Summary metrics">
      <div class="metric"><span>Apps</span><strong>{number(len(source_rows))}</strong></div>
      <div class="metric"><span>Threads</span><strong>{number(summary["thread_count"])}</strong></div>
      <div class="metric"><span>Total tokens</span><strong>{number(total_tokens)}</strong></div>
      <div class="metric"><span>Estimated Codex credits</span><strong>{number(summary["estimated_codex_credits"])}</strong></div>
      <div class="metric"><span>Estimated USD</span><strong>${number(summary["estimated_api_usd_equiv"])}</strong></div>
      <div class="metric"><span>Estimated active time</span><strong>{minutes(summary["active_seconds"])} min</strong></div>
      <div class="metric"><span>Events</span><strong>{number(summary.get("event_count", 0))}</strong></div>
      <div class="metric"><span>Requests</span><strong>{number(summary.get("request_count", 0))}</strong></div>
    </section>
    <section class="insights" aria-label="Usage insights">
      <div class="insight"><span>Top app</span><strong>{html.escape(top_app)}</strong></div>
      <div class="insight"><span>Top project</span><strong>{html.escape(top_project)}</strong></div>
      <div class="insight"><span>Top model</span><strong>{html.escape(top_model)}</strong></div>
      <div class="insight"><span>Cache hit rate</span><strong>{percent(cache_hit_rate(summary["usage"]))}</strong></div>
      <div class="insight"><span>Output share</span><strong>{percent(output_ratio(summary["usage"]))}</strong></div>
    </section>
  </header>
  <main>
    <section class="feature-grid" aria-label="Usage insights and budget signals">
      <div class="feature-panel">
        <h2>Recent Usage Trend</h2>
        <div>{trend_html}</div>
      </div>
      <div class="feature-panel">
        <h2>Budget & Signals</h2>
        <div>{alert_html}</div>
      </div>
    </section>
    <section class="feature-panel" id="providers">
      <h2>Provider Comparison</h2>
      <div class="provider-tabs" role="tablist" aria-label="Provider comparison">
        {provider_buttons}
      </div>
      {provider_cards}
    </section>
    <section class="feature-panel" id="billing-connectors">
      <h2>Official Billing Connectors</h2>
      <div class="connector-grid">{connector_html}</div>
    </section>
    <div class="toolbar">
      <h2>Usage Tables</h2>
      <div class="search-wrap">
        <input class="search" id="table-search" type="search" placeholder="Filter tables by project, model, title, or date">
        <button class="clear-search" id="clear-search" type="button" aria-label="Clear search" hidden>&times;</button>
        <span class="search-status" id="search-status"></span>
      </div>
    </div>
    <nav class="table-nav" aria-label="Usage table sections">
      <a href="#apps">Apps</a>
      <a href="#daily">Daily</a>
      <a href="#projects">Projects</a>
      <a href="#models">Models</a>
      <a href="#threads">Threads</a>
      <a href="#providers">Providers</a>
    </nav>
    <section id="apps">
      <h2>Apps</h2>
      <div class="panel">
        <table data-filterable>
          <thead><tr><th>App</th><th>Threads</th><th>Requests</th><th>Events</th><th>Tokens</th><th>Input</th><th>Cached input</th><th>Output</th><th>Credits</th><th>USD</th><th>Active min</th></tr></thead>
          <tbody>{source_html}</tbody>
        </table>
      </div>
    </section>
    <section id="daily">
      <h2>Daily Usage</h2>
      <div class="panel">
        <table data-filterable>
          <thead><tr><th>Date</th><th>Token volume</th><th>Total</th><th>Input</th><th>Cached input</th><th>Output</th><th>Cache hit</th><th>Credits</th><th>USD</th><th>Active min</th></tr></thead>
          <tbody>{daily_html}</tbody>
        </table>
      </div>
    </section>
    <section id="projects">
      <h2>Projects</h2>
      <div class="panel">
        <table data-filterable>
          <thead><tr><th>App</th><th>Project</th><th>Folder</th><th>Threads</th><th>Requests</th><th>Events</th><th>Tokens</th><th>Cache hit</th><th>Credits</th><th>USD</th><th>Active min</th></tr></thead>
          <tbody>{project_html}</tbody>
        </table>
      </div>
    </section>
    <section id="models">
      <h2>Models</h2>
      <div class="panel">
        <table data-filterable>
          <thead><tr><th>App</th><th>Model</th><th>Threads</th><th>Requests</th><th>Tokens</th><th>Cached input</th><th>Output</th><th>Output share</th><th>Credits</th><th>USD</th></tr></thead>
          <tbody>{model_html}</tbody>
        </table>
      </div>
    </section>
    <section id="threads">
      <h2>Most Expensive Threads</h2>
      <div class="panel">
        <table data-filterable>
          <thead><tr><th>App</th><th>Thread</th><th>Project</th><th>Model</th><th>Last activity</th><th>Requests</th><th>Events</th><th>Tokens</th><th>Cache hit</th><th>Credits</th><th>USD</th><th>Active min</th></tr></thead>
          <tbody>{thread_html}</tbody>
        </table>
      </div>
    </section>
    <div class="note">
      Credit estimates use OpenAI's Codex token-based rate card for Codex records only, Claude USD uses Anthropic token pricing where known local model rates exist, and Cursor USD uses local Agent transcript/bubble token estimates with Cursor Auto pricing when exact token counts are missing.
    </div>
  </main>
  <footer>
    Built by <a href="{repo_url}">SuvenSeo</a> for developers who want local visibility into AI coding usage.
  </footer>
  <script>
    const search = document.getElementById("table-search");
    const clearSearch = document.getElementById("clear-search");
    const searchStatus = document.getElementById("search-status");
    const rows = Array.from(document.querySelectorAll("table[data-filterable] tbody tr"));
    const dataRows = rows.filter((row) => !row.querySelector(".empty"));
    function applyTableFilter() {{
      const query = search.value.trim().toLowerCase();
      let shown = 0;
      rows.forEach((row) => {{
        const visible = !query || row.textContent.toLowerCase().includes(query);
        row.hidden = !visible;
        if (visible && !row.querySelector(".empty")) {{
          shown += 1;
        }}
      }});
      clearSearch.hidden = !query;
      searchStatus.textContent = query
        ? "Showing " + shown.toLocaleString() + " matching rows"
        : "Showing all " + dataRows.length.toLocaleString() + " rows";
    }}
    search.addEventListener("input", applyTableFilter);
    clearSearch.addEventListener("click", () => {{
      search.value = "";
      search.focus();
      applyTableFilter();
    }});
    const providerTabs = Array.from(document.querySelectorAll("[data-provider-tab]"));
    const providerCards = Array.from(document.querySelectorAll("[data-provider-card]"));
    providerTabs.forEach((tab) => {{
      tab.addEventListener("click", () => {{
        const provider = tab.dataset.providerTab;
        providerTabs.forEach((item) => item.classList.toggle("is-active", item === tab));
        providerCards.forEach((card) => {{
          card.classList.toggle("is-active", card.dataset.providerCard === provider);
        }});
      }});
    }});
    applyTableFilter();
  </script>
</body>
</html>
"""
    output_path.write_text(html_doc, encoding="utf-8")


def write_reports(
    threads: list[dict[str, Any]],
    summary: dict[str, Any],
    output_dir: Path,
    report_tz: Any = None,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    serial_threads = [serializable_thread(thread) for thread in threads]
    serial_summary = serializable_summary(summary)

    paths = {
        "summary_json": output_dir / "codex_usage_summary.json",
        "threads_csv": output_dir / "threads.csv",
        "daily_csv": output_dir / "daily.csv",
        "sources_csv": output_dir / "sources.csv",
        "projects_csv": output_dir / "projects.csv",
        "models_csv": output_dir / "models.csv",
        "dashboard_html": output_dir / "dashboard.html",
    }

    write_json(paths["summary_json"], {"summary": serial_summary, "threads": serial_threads})

    thread_rows = [flatten_thread_for_csv(thread, report_tz=report_tz) for thread in threads]
    write_csv(paths["threads_csv"], thread_rows, [
        "thread_id", "app", "source", "title", "project", "cwd", "model", "reasoning_effort",
        "started_at", "ended_at", "active_minutes_est", "input_tokens",
        "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
        "total_tokens", "estimated_codex_credits", "estimated_api_usd_equiv",
        "event_count", "request_count", "path",
    ])

    daily_rows = [
        flatten_group_for_csv(row, {"date": row["date"]})
        for row in summary["daily"]
    ]
    write_csv(paths["daily_csv"], daily_rows, [
        "date", "thread_count", "active_minutes_est", "input_tokens",
        "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
        "total_tokens", "estimated_codex_credits", "estimated_api_usd_equiv",
    ])

    source_rows = [
        flatten_group_for_csv(row, {"app": row["app"]})
        for row in summary.get("sources", [])
    ]
    write_csv(paths["sources_csv"], source_rows, [
        "app", "thread_count", "event_count", "request_count", "active_minutes_est",
        "input_tokens", "cached_input_tokens", "output_tokens",
        "reasoning_output_tokens", "total_tokens", "estimated_codex_credits",
        "estimated_api_usd_equiv",
    ])

    project_rows = [
        flatten_group_for_csv(row, {"app": row["app"], "project": row["project"], "cwd": row["cwd"]})
        for row in summary["projects"]
    ]
    write_csv(paths["projects_csv"], project_rows, [
        "app", "project", "cwd", "thread_count", "event_count", "request_count",
        "active_minutes_est", "input_tokens",
        "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
        "total_tokens", "estimated_codex_credits", "estimated_api_usd_equiv",
    ])

    model_rows = [
        flatten_group_for_csv(row, {"app": row["app"], "model": row["model"]})
        for row in summary["models"]
    ]
    write_csv(paths["models_csv"], model_rows, [
        "app", "model", "thread_count", "event_count", "request_count",
        "active_minutes_est", "input_tokens",
        "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
        "total_tokens", "estimated_codex_credits", "estimated_api_usd_equiv",
    ])

    render_dashboard(paths["dashboard_html"], threads, summary, report_tz=report_tz)
    return paths


def load_tracker_state(state_file: Path) -> dict[str, Any]:
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return {"wakatime_sent": {}}


def save_tracker_state(state: dict[str, Any], state_file: Path) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")


def has_wakatime_key() -> bool:
    cfg = Path.home() / ".wakatime.cfg"
    if not cfg.exists():
        return False
    try:
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            if re.match(r"^\s*api_key\s*=\s*\S+", line):
                return True
    except Exception:
        return False
    return bool(os.environ.get("WAKATIME_API_KEY"))


def find_wakatime_cli() -> str | None:
    candidates = [
        Path.home() / ".wakatime" / "wakatime-cli-windows-amd64.exe",
        Path.home() / ".wakatime" / "wakatime-cli.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which("wakatime-cli")


def env_present(*names: str) -> bool:
    return any(bool(os.environ.get(name)) for name in names)


def parse_positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("budget must be a number") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("budget must be zero or greater")
    return parsed


def budget_options(args: argparse.Namespace | None) -> dict[str, float]:
    if args is None:
        return {}
    return {
        "daily_tokens": float(getattr(args, "daily_token_budget", 0.0) or 0.0),
        "daily_usd": float(getattr(args, "daily_usd_budget", 0.0) or 0.0),
        "monthly_tokens": float(getattr(args, "monthly_token_budget", 0.0) or 0.0),
        "monthly_usd": float(getattr(args, "monthly_usd_budget", 0.0) or 0.0),
    }


def build_usage_alerts(summary: dict[str, Any], budgets: dict[str, float] | None = None) -> list[dict[str, str]]:
    budgets = budgets or {}
    alerts: list[dict[str, str]] = []
    daily = list(summary.get("daily") or [])
    latest_daily = daily[-1] if daily else None
    latest_tokens = usage_total(latest_daily["usage"]) if latest_daily else 0
    latest_usd = float(latest_daily.get("estimated_api_usd_equiv") or 0.0) if latest_daily else 0.0
    latest_date = latest_daily.get("date", "latest day") if latest_daily else "latest day"

    month_prefix = str(latest_date)[:7] if latest_daily else ""
    month_rows = [row for row in daily if str(row.get("date", "")).startswith(month_prefix)] if month_prefix else daily
    month_tokens = sum(usage_total(row["usage"]) for row in month_rows)
    month_usd = sum(float(row.get("estimated_api_usd_equiv") or 0.0) for row in month_rows)

    def add_budget_alert(label: str, actual: float, budget: float, unit: str) -> None:
        if budget <= 0:
            return
        ratio = actual / budget if budget else 0.0
        severity = "risk" if ratio >= 1.0 else "warn" if ratio >= 0.8 else "ok"
        if unit == "tokens":
            actual_text = number(int(actual))
            budget_text = number(int(budget))
        else:
            actual_text = "$" + number(actual)
            budget_text = "$" + number(budget)
        alerts.append({
            "severity": severity,
            "title": label,
            "detail": f"{actual_text} of {budget_text} ({percent(ratio * 100.0)})",
        })

    add_budget_alert(f"{latest_date} token budget", float(latest_tokens), budgets.get("daily_tokens", 0.0), "tokens")
    add_budget_alert(f"{latest_date} USD budget", latest_usd, budgets.get("daily_usd", 0.0), "usd")
    add_budget_alert(f"{month_prefix or 'Selected range'} token budget", float(month_tokens), budgets.get("monthly_tokens", 0.0), "tokens")
    add_budget_alert(f"{month_prefix or 'Selected range'} USD budget", month_usd, budgets.get("monthly_usd", 0.0), "usd")

    total_tokens = usage_total(summary.get("usage") or {})
    if total_tokens > 0 and cache_hit_rate(summary["usage"]) < 20.0:
        alerts.append({
            "severity": "warn",
            "title": "Low cache reuse",
            "detail": "Cache hit rate is below 20%; repeated large-context work may be costing more than needed.",
        })
    if total_tokens > 0 and output_ratio(summary["usage"]) > 35.0:
        alerts.append({
            "severity": "warn",
            "title": "High output share",
            "detail": "Output tokens are a large share of usage; long generated artifacts may be driving cost.",
        })
    if not env_present("OPENAI_ADMIN_KEY", "ANTHROPIC_ADMIN_KEY", "CURSOR_ADMIN_API_KEY"):
        alerts.append({
            "severity": "info",
            "title": "Official billing not connected",
            "detail": "Local estimates are shown; add provider admin keys only if you want optional official billing checks.",
        })
    if not alerts:
        alerts.append({
            "severity": "ok",
            "title": "No active budget alerts",
            "detail": "Configured budget thresholds are below their warning levels for the selected data.",
        })
    return alerts


def billing_window(days: int) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(1, int(days)))
    return start, end


def fetch_json(
    url: str,
    headers: dict[str, str],
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers = dict(headers)
        headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(2_000_000)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from provider API") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"connection failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("request timed out") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("provider returned non-JSON response") from exc


def numeric_value(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def sum_keys_deep(data: Any, names: set[str]) -> float:
    total = 0.0
    if isinstance(data, dict):
        for key, value in data.items():
            if key.lower() in names:
                total += numeric_value(value)
            total += sum_keys_deep(value, names)
    elif isinstance(data, list):
        for item in data:
            total += sum_keys_deep(item, names)
    return total


def count_collection_rows(data: Any) -> int:
    if isinstance(data, dict):
        for key in ("data", "results", "items", "teamMemberSpend", "members"):
            value = data.get(key)
            if isinstance(value, list):
                return len(value)
    return 0


def parse_openai_cost_usd(data: dict[str, Any]) -> float:
    total = 0.0
    for bucket in data.get("data") or []:
        if not isinstance(bucket, dict):
            continue
        for result in bucket.get("results") or []:
            if not isinstance(result, dict):
                continue
            amount = result.get("amount")
            if isinstance(amount, dict):
                total += numeric_value(amount.get("value"))
            else:
                total += numeric_value(amount)
    return total


def build_billing_connectors(fetch: bool = False, days: int = 30, timeout: int = 20) -> list[dict[str, Any]]:
    start, end = billing_window(days)
    rows: list[dict[str, Any]] = []

    def add(row: dict[str, Any]) -> None:
        row.setdefault("status", "not configured")
        row.setdefault("period", f"{start.date()} to {end.date()}")
        row.setdefault("records", "not queried")
        row.setdefault("official_usd", "")
        row.setdefault("blocked", "")
        rows.append(row)

    openai_key = os.environ.get("OPENAI_ADMIN_KEY")
    openai = {
        "provider": "OpenAI",
        "env_var": "OPENAI_ADMIN_KEY",
        "docs_url": OPENAI_COSTS_API_URL,
        "exact": "organization costs from OpenAI Costs API",
        "status": "configured" if openai_key else "not configured",
        "blocked": "" if openai_key else "set OPENAI_ADMIN_KEY to fetch organization costs",
    }
    if fetch and openai_key:
        params = urllib.parse.urlencode({
            "start_time": int(start.timestamp()),
            "end_time": int(end.timestamp()),
            "limit": 180,
        })
        try:
            data = fetch_json(
                f"https://api.openai.com/v1/organization/costs?{params}",
                {"Authorization": f"Bearer {openai_key}", "User-Agent": f"ai-coding-usage-tracker/{VERSION}"},
                timeout=timeout,
            )
            total = parse_openai_cost_usd(data)
            openai.update({"status": "fetched", "records": str(count_collection_rows(data)), "official_usd": round(total, 4)})
        except Exception as exc:
            openai.update({"status": "fetch failed", "blocked": str(exc)})
    add(openai)

    anthropic_key = os.environ.get("ANTHROPIC_ADMIN_KEY")
    anthropic = {
        "provider": "Anthropic",
        "env_var": "ANTHROPIC_ADMIN_KEY",
        "docs_url": ANTHROPIC_USAGE_COST_API_URL,
        "exact": "organization costs from Anthropic Usage and Cost APIs",
        "status": "configured" if anthropic_key else "not configured",
        "blocked": "" if anthropic_key else "set ANTHROPIC_ADMIN_KEY to fetch organization cost data",
    }
    if fetch and anthropic_key:
        params = urllib.parse.urlencode({
            "starting_at": start.date().isoformat(),
            "ending_at": end.date().isoformat(),
        })
        try:
            data = fetch_json(
                f"https://api.anthropic.com/v1/organizations/cost_report?{params}",
                {
                    "x-api-key": anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "User-Agent": f"ai-coding-usage-tracker/{VERSION}",
                },
                timeout=timeout,
            )
            total = sum_keys_deep(data, {"amount_usd", "cost_usd", "total_usd", "total_cost_usd"})
            anthropic.update({"status": "fetched", "records": str(count_collection_rows(data)), "official_usd": round(total, 4) if total else ""})
        except Exception as exc:
            anthropic.update({"status": "fetch failed", "blocked": str(exc)})
    add(anthropic)

    cursor_key = os.environ.get("CURSOR_ADMIN_API_KEY")
    cursor = {
        "provider": "Cursor",
        "env_var": "CURSOR_ADMIN_API_KEY",
        "docs_url": CURSOR_ADMIN_API_URL,
        "exact": "team spend from Cursor Admin API",
        "status": "configured" if cursor_key else "not configured",
        "blocked": "" if cursor_key else "set CURSOR_ADMIN_API_KEY to fetch team spending",
    }
    if fetch and cursor_key:
        token = base64.b64encode(f"{cursor_key}:".encode("utf-8")).decode("ascii")
        try:
            data = fetch_json(
                "https://api.cursor.com/teams/spend",
                {"Authorization": f"Basic {token}", "User-Agent": f"ai-coding-usage-tracker/{VERSION}"},
                method="POST",
                payload={"page": 1, "pageSize": 100},
                timeout=timeout,
            )
            cents = sum_keys_deep(data, {"spendcents", "totalspendcents"})
            cursor.update({"status": "fetched", "records": str(count_collection_rows(data)), "official_usd": round(cents / 100.0, 4) if cents else ""})
        except Exception as exc:
            cursor.update({"status": "fetch failed", "blocked": str(exc)})
    add(cursor)

    return rows


def thread_range_text(threads: list[dict[str, Any]], report_tz: Any = None) -> str:
    starts = [thread.get("started_at") for thread in threads if thread.get("started_at")]
    ends = [thread.get("ended_at") for thread in threads if thread.get("ended_at")]
    if not starts and not ends:
        return ""
    start = min(starts or ends)
    end = max(ends or starts)
    return f"{fmt_dt(start, report_tz)} to {fmt_dt(end, report_tz)}"


def audit_totals_from_threads(threads: list[dict[str, Any]]) -> dict[str, Any]:
    summary = aggregate_threads(threads)
    return {
        "threads": summary["thread_count"],
        "events": summary["event_count"],
        "requests": summary["request_count"],
        "tokens": usage_total(summary["usage"]),
        "credits": round(summary["estimated_codex_credits"], 4),
        "usd": round(summary["estimated_api_usd_equiv"], 4),
        "active_minutes": round(summary["active_seconds"] / 60.0, 1),
    }


def source_path(path: Path, redact: bool) -> str:
    return "(redacted)" if redact else str(path)


def build_source_audit(args: argparse.Namespace, report_tz: Any = None) -> dict[str, Any]:
    redact = bool(getattr(args, "redact", False))
    codex_home = Path(args.codex_home).expanduser()
    claude_home = Path(args.claude_home).expanduser()
    cursor_db = Path(args.cursor_db).expanduser()
    cursor_state_db = Path(args.cursor_state_db).expanduser()
    appdata = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    claude_desktop_dir = appdata / "Claude"
    rows: list[dict[str, Any]] = []

    def add(row: dict[str, Any]) -> None:
        row.setdefault("tokens", 0)
        row.setdefault("credits", 0.0)
        row.setdefault("usd", 0.0)
        row.setdefault("active_minutes", 0.0)
        row.setdefault("local_path", "")
        row.setdefault("docs_url", "")
        rows.append(row)

    codex_files = iter_rollout_files(codex_home) if codex_home.exists() else []
    codex_threads = load_threads(codex_home, report_tz=report_tz) if codex_files else []
    codex_totals = audit_totals_from_threads(codex_threads)
    add({
        "source": "Codex local logs",
        "status": "available" if codex_threads else "missing local logs",
        "records": f"{len(codex_files)} rollout files / {codex_totals['threads']} parsed threads",
        "date_range": thread_range_text(codex_threads, report_tz),
        "exact": "tokens, requests, events from local rollout logs",
        "estimated": "Codex credits and USD from verified rate cards",
        "blocked": "exact invoice, payment status, and remaining account credits are not stored in local logs",
        "local_path": source_path(codex_home, redact),
        "docs_url": CODEX_RATE_CARD_URL,
        **codex_totals,
    })

    claude_files = iter_claude_files(claude_home) if claude_home.exists() else []
    claude_threads = load_claude_threads(claude_home, report_tz=report_tz) if claude_files else []
    claude_totals = audit_totals_from_threads(claude_threads)
    add({
        "source": "Claude Code local JSONL",
        "status": "available" if claude_threads else "missing local logs",
        "records": f"{len(claude_files)} JSONL files / {claude_totals['threads']} parsed sessions",
        "date_range": thread_range_text(claude_threads, report_tz),
        "exact": "tokens and cache token fields from Claude Code transcripts",
        "estimated": "USD from Anthropic model prices",
        "blocked": "Claude web/desktop chats, subscription quota, and invoice totals are not in these JSONL files",
        "local_path": source_path(claude_home, redact),
        "docs_url": ANTHROPIC_PRICING_URL,
        **claude_totals,
    })

    claude_desktop_files = list(claude_desktop_dir.glob("*")) if claude_desktop_dir.exists() else []
    add({
        "source": "Claude Desktop/Web local app",
        "status": "no local usage ledger",
        "records": f"{len(claude_desktop_files)} local app files",
        "date_range": "",
        "exact": "configuration only",
        "estimated": "none",
        "blocked": "chat/token/billing history requires vendor export or admin APIs; local app folder did not expose usage logs",
        "local_path": source_path(claude_desktop_dir, redact),
    })

    cursor_threads = load_cursor_threads(
        cursor_db,
        cursor_state_db=cursor_state_db,
        cursor_projects_home=Path(getattr(args, "cursor_projects_home", DEFAULT_CURSOR_PROJECTS_HOME)).expanduser(),
        report_tz=report_tz,
    ) if cursor_db.exists() or Path(getattr(args, "cursor_projects_home", DEFAULT_CURSOR_PROJECTS_HOME)).expanduser().exists() else []
    cursor_totals = audit_totals_from_threads(cursor_threads)
    cursor_token_threads = sum(1 for thread in cursor_threads if usage_total(thread.get("usage") or {}) > 0)
    add({
        "source": "Cursor local usage",
        "status": "available" if cursor_threads else "missing local logs",
        "records": f"{cursor_totals['threads']} conversations / {cursor_token_threads} with token estimates",
        "date_range": thread_range_text(cursor_threads, report_tz),
        "exact": "bubble tokenCount when Cursor stores it",
        "estimated": "Agent transcript and bubble char/4 token estimates plus Cursor Auto USD pricing",
        "blocked": "official invoice, subscription quota, and remaining fast requests require Cursor account or admin APIs",
        "local_path": source_path(cursor_state_db, redact),
        "docs_url": CURSOR_PRICING_URL,
        **cursor_totals,
    })

    add({
        "source": "Cursor AI tracking DB",
        "status": "available" if cursor_db.exists() else "missing local DB",
        "records": "supplemental AI edit activity merged into Cursor conversations",
        "date_range": thread_range_text(cursor_threads, report_tz),
        "exact": "AI edit activity rows, requests, models, and active-time estimate",
        "estimated": "merged into Cursor token threads when conversation IDs match",
        "blocked": "does not include full chat transcripts on its own",
        "local_path": source_path(cursor_db, redact),
        "docs_url": CURSOR_PRICING_URL,
        "tokens": 0,
        "credits": 0.0,
        "usd": 0.0,
        "active_minutes": 0.0,
        "threads": 0,
    })

    cursor_daily_stats = read_cursor_daily_stats(cursor_state_db)
    cursor_daily_totals = {
        "tab_suggested_lines": sum(row["tab_suggested_lines"] for row in cursor_daily_stats),
        "tab_accepted_lines": sum(row["tab_accepted_lines"] for row in cursor_daily_stats),
        "composer_suggested_lines": sum(row["composer_suggested_lines"] for row in cursor_daily_stats),
        "composer_accepted_lines": sum(row["composer_accepted_lines"] for row in cursor_daily_stats),
    }
    date_range = ""
    if cursor_daily_stats:
        date_range = f"{cursor_daily_stats[0]['date']} to {cursor_daily_stats[-1]['date']}"
    add({
        "source": "Cursor legacy daily stats",
        "status": "available" if cursor_daily_stats else "missing daily stats",
        "records": f"{len(cursor_daily_stats)} daily rows",
        "date_range": date_range,
        "exact": "suggested and accepted line counters",
        "estimated": "none",
        "blocked": "tokens, credits, spend, model costs, and conversations are not in these daily counters",
        "local_path": source_path(cursor_state_db, redact),
        "docs_url": CURSOR_PRICING_URL,
        "tokens": 0,
        "credits": 0.0,
        "usd": 0.0,
        "active_minutes": 0.0,
        "extra": cursor_daily_totals,
    })

    add({
        "source": "OpenAI Admin Usage/Costs API",
        "status": "configured" if env_present("OPENAI_ADMIN_KEY") else "not configured",
        "records": "not queried",
        "date_range": "",
        "exact": "OpenAI API organization usage and costs when an admin key is provided",
        "estimated": "none",
        "blocked": "OPENAI_ADMIN_KEY is not configured on this machine",
        "docs_url": OPENAI_COSTS_API_URL,
    })
    add({
        "source": "Anthropic Usage/Cost APIs",
        "status": "configured" if env_present("ANTHROPIC_ADMIN_KEY") else "not configured",
        "records": "not queried",
        "date_range": "",
        "exact": "Anthropic organization costs and Claude Code analytics when admin access is provided",
        "estimated": "none",
        "blocked": "ANTHROPIC_ADMIN_KEY is not configured on this machine",
        "docs_url": ANTHROPIC_USAGE_COST_API_URL,
    })
    add({
        "source": "Cursor Team Admin API",
        "status": "configured" if env_present("CURSOR_ADMIN_API_KEY") else "not configured",
        "records": "not queried",
        "date_range": "",
        "exact": "team usage and spending when a Cursor admin key is provided",
        "estimated": "none",
        "blocked": "CURSOR_ADMIN_API_KEY is not configured on this machine",
        "docs_url": CURSOR_ADMIN_API_URL,
    })
    add({
        "source": "WakaTime",
        "status": "configured" if has_wakatime_key() else "not configured",
        "records": "optional sync target",
        "date_range": "",
        "exact": "coding-time heartbeats after sync",
        "estimated": "none",
        "blocked": "no AI tokens, credits, or vendor spend in WakaTime",
        "local_path": find_wakatime_cli() or "",
    })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pricing_source_date": PRICING_SOURCE_DATE,
        "scope": "local all-time inventory plus official API availability",
        "sources": rows,
    }


def markdown_escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_source_audit_markdown(audit: dict[str, Any]) -> str:
    lines = [
        "# AI Coding Usage Source Audit",
        "",
        f"Generated: {audit.get('generated_at', '')}",
        f"Scope: {audit.get('scope', '')}",
        "",
        "| Source | Status | Records | Range | Tokens | Credits | USD | Exact | Estimated | Blocked |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for row in audit.get("sources", []):
        lines.append(
            "| "
            + " | ".join(
                markdown_escape(value)
                for value in (
                    row.get("source", ""),
                    row.get("status", ""),
                    row.get("records", ""),
                    row.get("date_range", ""),
                    number(row.get("tokens", 0)),
                    number(row.get("credits", 0.0)),
                    number(row.get("usd", 0.0)),
                    row.get("exact", ""),
                    row.get("estimated", ""),
                    row.get("blocked", ""),
                )
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def write_source_audit(output_dir: Path, audit: dict[str, Any]) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "source_audit_json": output_dir / "source_audit.json",
        "source_audit_md": output_dir / "source_audit.md",
    }
    write_json(paths["source_audit_json"], audit)
    paths["source_audit_md"].write_text(render_source_audit_markdown(audit), encoding="utf-8")
    return paths


def source_audit_cli_rows(audit: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in audit.get("sources", []):
        rows.append({
            "source": row.get("source", ""),
            "status": row.get("status", ""),
            "records": row.get("records", ""),
            "range": row.get("date_range", ""),
            "tokens": row.get("tokens", 0),
            "credits": row.get("credits", 0.0),
            "usd": row.get("usd", 0.0),
            "blocked": row.get("blocked", ""),
        })
    return rows


def recent_wakatime_candidates(
    threads: list[dict[str, Any]],
    since_minutes: int,
    interval_minutes: int,
    max_heartbeats: int,
) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    buckets: dict[str, dict[str, Any]] = {}
    interval_seconds = max(60, interval_minutes * 60)

    for thread in threads:
        cwd = clean_windows_path(thread.get("cwd") or "")
        if not cwd:
            continue
        for ts in thread.get("event_timestamps") or []:
            if ts < cutoff:
                continue
            bucket = int(ts.timestamp() // interval_seconds)
            key = f"{thread['thread_id']}:{bucket}"
            if key not in buckets or ts < buckets[key]["timestamp"]:
                buckets[key] = {
                    "key": key,
                    "timestamp": ts,
                    "thread_id": thread["thread_id"],
                    "title": thread.get("title") or "",
                    "cwd": cwd,
                    "project": thread.get("project") or project_name_from_cwd(cwd),
                }

    return sorted(buckets.values(), key=lambda item: item["timestamp"])[-max_heartbeats:]


def sync_wakatime(
    threads: list[dict[str, Any]],
    since_minutes: int,
    interval_minutes: int,
    max_heartbeats: int,
    state_file: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    cli = find_wakatime_cli()
    if not cli:
        return {"ok": False, "sent": 0, "skipped": 0, "errors": ["wakatime-cli not found"]}
    if not has_wakatime_key():
        return {"ok": False, "sent": 0, "skipped": 0, "errors": ["~/.wakatime.cfg has no api_key"]}

    state = load_tracker_state(state_file)
    sent_map = state.setdefault("wakatime_sent", {})
    candidates = recent_wakatime_candidates(threads, since_minutes, interval_minutes, max_heartbeats)

    sent = 0
    skipped = 0
    errors: list[str] = []

    for item in candidates:
        if item["key"] in sent_map:
            skipped += 1
            continue

        cmd = [
            cli,
            "--entity", item["cwd"],
            "--entity-type", "app",
            "--category", "ai coding",
            "--project", item["project"],
            "--plugin", f"codex-app-tracker/{VERSION}",
            "--time", f"{item['timestamp'].timestamp():.3f}",
            "--heartbeat-rate-limit-seconds", "0",
        ]
        if dry_run:
            sent += 1
            continue

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            sent_map[item["key"]] = {
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "heartbeat_time": item["timestamp"].isoformat(),
                "thread_id": item["thread_id"],
                "project": item["project"],
            }
            sent += 1
        else:
            message = (proc.stderr or proc.stdout or "").strip()
            wakatime_key_pattern = "waka" + r"_[A-Za-z0-9-]+"
            message = re.sub(wakatime_key_pattern, "wakatime-key-redacted", message)
            errors.append(message[:400] or f"wakatime-cli exited {proc.returncode}")

    if not dry_run:
        state["last_wakatime_sync_at"] = datetime.now(timezone.utc).isoformat()
        save_tracker_state(state, state_file)

    return {"ok": not errors, "sent": sent, "skipped": skipped, "errors": errors}


def enrich_summary(summary: dict[str, Any], args: argparse.Namespace | None = None) -> dict[str, Any]:
    budgets = budget_options(args)
    summary["budgets"] = budgets
    summary["alerts"] = build_usage_alerts(summary, budgets)
    summary["billing_connectors"] = build_billing_connectors(fetch=False)
    return summary


def load_report_data(args: argparse.Namespace, private_for_sync: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any], Any]:
    report_tz = resolve_timezone(getattr(args, "timezone", None))
    threads = load_selected_threads(args, report_tz=report_tz)
    threads = filter_threads_by_date(
        threads,
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
        report_tz=report_tz,
    )
    if not private_for_sync:
        threads = apply_privacy(
            threads,
            redact=bool(getattr(args, "redact", False)),
            hash_projects=bool(getattr(args, "hash_projects", False)),
        )
    summary = aggregate_threads(threads)
    enrich_summary(summary, args)
    return threads, summary, report_tz


def demo_thread(
    thread_id: str,
    title: str,
    cwd: str,
    model: str,
    started_at: datetime,
    minutes_active: int,
    usage: dict[str, int],
    report_tz: Any = None,
    app: str = "codex",
    source: str | None = None,
    event_count: int = 24,
    request_count: int = 1,
) -> dict[str, Any]:
    timestamps = [started_at + timedelta(minutes=offset) for offset in range(0, max(minutes_active, 1), 8)]
    if not timestamps:
        timestamps = [started_at]
    active_seconds, active_daily = estimate_active_seconds(timestamps, report_tz=report_tz)
    daily_usage = {local_day(started_at, report_tz): usage}
    return {
        "thread_id": thread_id,
        "title": title,
        "cwd": cwd,
        "project": project_name_from_cwd(cwd),
        "app": app,
        "source": source or f"demo_{app}",
        "model": model,
        "reasoning_effort": "medium",
        "cli_version": VERSION,
        "path": f"demo/{thread_id}.jsonl",
        "line_count": event_count,
        "event_count": event_count,
        "request_count": request_count,
        "started_at": started_at,
        "ended_at": timestamps[-1],
        "usage": usage,
        "daily_usage": daily_usage,
        "event_timestamps": timestamps,
        "active_seconds": active_seconds,
        "active_daily": active_daily,
        "tool_counts": {"shell_command": 3, "apply_patch": 2},
        "estimated_codex_credits": estimate_amount(usage, model, "codex_credits") if app == "codex" else 0.0,
        "estimated_api_usd_equiv": (
            estimate_amount(usage, model, "api_usd_standard_short")
            if app == "codex"
            else estimate_amount(usage, model, "anthropic_usd")
            if app == "claude"
            else cursor_estimated_usd(usage, model)
            if app == "cursor"
            else 0.0
        ),
    }


def demo_threads(report_tz: Any = None) -> list[dict[str, Any]]:
    base = datetime(2026, 5, 24, 8, 30, tzinfo=timezone.utc)
    return [
        demo_thread(
            "demo-usage-dashboard",
            "Build usage dashboard and README launch copy",
            "C:\\Projects\\ai-coding-usage-tracker",
            "gpt-5.5",
            base,
            72,
            {
                "input_tokens": 220_000,
                "cached_input_tokens": 88_000,
                "output_tokens": 18_400,
                "reasoning_output_tokens": 6_200,
                "total_tokens": 238_400,
            },
            report_tz=report_tz,
        ),
        demo_thread(
            "demo-security-pass",
            "Review privacy controls and secret handling",
            "C:\\Projects\\ai-coding-usage-tracker",
            "gpt-5.4",
            base + timedelta(hours=3),
            38,
            {
                "input_tokens": 90_000,
                "cached_input_tokens": 45_000,
                "output_tokens": 7_600,
                "reasoning_output_tokens": 2_100,
                "total_tokens": 97_600,
            },
            report_tz=report_tz,
        ),
        demo_thread(
            "demo-client-app",
            "Debug frontend smoke test for a client app",
            "C:\\Work\\client-portal",
            "gpt-5.4-mini",
            base - timedelta(days=1, hours=2),
            54,
            {
                "input_tokens": 145_000,
                "cached_input_tokens": 96_000,
                "output_tokens": 11_800,
                "reasoning_output_tokens": 3_200,
                "total_tokens": 156_800,
            },
            report_tz=report_tz,
        ),
        demo_thread(
            "demo-claude-code",
            "Refactor parser fixtures with Claude Code",
            "C:\\Projects\\ai-coding-usage-tracker",
            "claude-sonnet-4-6",
            base + timedelta(days=1, hours=1),
            46,
            {
                "input_tokens": 180_000,
                "cached_input_tokens": 120_000,
                "output_tokens": 14_200,
                "reasoning_output_tokens": 0,
                "total_tokens": 194_200,
            },
            report_tz=report_tz,
            app="claude",
            source="demo_claude_code",
            event_count=96,
            request_count=24,
        ),
        demo_thread(
            "demo-cursor-ai-edits",
            "Cursor Agent session with token estimates",
            "C:\\Projects\\ai-coding-usage-tracker",
            "composer-2.5",
            base + timedelta(days=1, hours=3),
            28,
            {
                "input_tokens": 128_000,
                "cached_input_tokens": 0,
                "output_tokens": 96_400,
                "reasoning_output_tokens": 0,
                "total_tokens": 224_400,
            },
            report_tz=report_tz,
            app="cursor",
            source="demo_cursor_transcript",
            event_count=430,
            request_count=12,
        ),
    ]


def print_report_summary(summary: dict[str, Any], paths: dict[str, Path]) -> None:
    print(f"apps={len(summary.get('sources', []))}")
    print(f"threads={summary['thread_count']}")
    print(f"tokens={usage_total(summary['usage'])}")
    print(f"events={summary.get('event_count', 0)}")
    print(f"requests={summary.get('request_count', 0)}")
    print(f"estimated_codex_credits={summary['estimated_codex_credits']:.2f}")
    print(f"estimated_api_usd_equiv={summary['estimated_api_usd_equiv']:.2f}")
    print(f"active_minutes_est={summary['active_seconds'] / 60.0:.1f}")
    print(f"pricing_source_date={summary.get('pricing', {}).get('source_date', PRICING_SOURCE_DATE)}")
    for name, path in paths.items():
        print(f"{name}={path}")


def command_report(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).expanduser()
    threads, summary, report_tz = load_report_data(args)
    paths = write_reports(threads, summary, output_dir, report_tz=report_tz)
    paths.update(write_source_audit(output_dir, build_source_audit(args, report_tz=report_tz)))
    print_report_summary(summary, paths)
    return 0


def command_demo(args: argparse.Namespace) -> int:
    report_tz = resolve_timezone(getattr(args, "timezone", None))
    output_dir = Path(args.output_dir).expanduser()
    if str(output_dir) == str(DEFAULT_OUTPUT_DIR):
        output_dir = output_dir / "demo"
    threads = demo_threads(report_tz=report_tz)
    threads = apply_privacy(
        threads,
        redact=bool(getattr(args, "redact", False)),
        hash_projects=bool(getattr(args, "hash_projects", False)),
    )
    summary = aggregate_threads(threads)
    enrich_summary(summary, args)
    paths = write_reports(threads, summary, output_dir, report_tz=report_tz)
    print_report_summary(summary, paths)
    return 0


def command_period_report(args: argparse.Namespace, period: str) -> int:
    _, summary, _ = load_report_data(args)
    rows = grouped_daily_rows(summary, period)
    key = "date" if period == "daily" else "period"
    return emit_rows(rows, [
        (key, key.title()),
        ("threads", "Threads"),
        ("active_min", "Active min"),
        ("tokens", "Tokens"),
        ("input", "Input"),
        ("cached", "Cached"),
        ("output", "Output"),
        ("cache_hit", "Cache hit"),
        ("credits", "Credits"),
        ("api_usd", "USD"),
    ], args)


def command_daily(args: argparse.Namespace) -> int:
    return command_period_report(args, "daily")


def command_weekly(args: argparse.Namespace) -> int:
    return command_period_report(args, "weekly")


def command_monthly(args: argparse.Namespace) -> int:
    return command_period_report(args, "monthly")


def command_session(args: argparse.Namespace) -> int:
    threads, _, report_tz = load_report_data(args)
    rows = [flatten_thread_for_cli(thread, report_tz=report_tz) for thread in threads]
    rows.sort(key=lambda item: item["tokens"], reverse=True)
    return emit_rows(rows, [
        ("thread_id", "Thread"),
        ("app", "App"),
        ("title", "Title"),
        ("project", "Project"),
        ("model", "Model"),
        ("ended_at", "Last activity"),
        ("active_min", "Active min"),
        ("tokens", "Tokens"),
        ("cache_hit", "Cache hit"),
        ("credits", "Credits"),
        ("api_usd", "USD"),
    ], args)


def command_project(args: argparse.Namespace) -> int:
    _, summary, _ = load_report_data(args)
    rows = [
        flatten_summary_row_for_cli(row, {"app": row["app"], "project": row["project"]})
        for row in summary["projects"]
    ]
    return emit_rows(rows, [
        ("app", "App"),
        ("project", "Project"),
        ("threads", "Threads"),
        ("requests", "Requests"),
        ("events", "Events"),
        ("active_min", "Active min"),
        ("tokens", "Tokens"),
        ("input", "Input"),
        ("cached", "Cached"),
        ("output", "Output"),
        ("cache_hit", "Cache hit"),
        ("credits", "Credits"),
        ("api_usd", "USD"),
    ], args)


def command_model(args: argparse.Namespace) -> int:
    _, summary, _ = load_report_data(args)
    rows = [
        flatten_summary_row_for_cli(row, {"app": row["app"], "model": row["model"]})
        for row in summary["models"]
    ]
    return emit_rows(rows, [
        ("app", "App"),
        ("model", "Model"),
        ("threads", "Threads"),
        ("requests", "Requests"),
        ("events", "Events"),
        ("active_min", "Active min"),
        ("tokens", "Tokens"),
        ("input", "Input"),
        ("cached", "Cached"),
        ("output", "Output"),
        ("cache_hit", "Cache hit"),
        ("credits", "Credits"),
        ("api_usd", "USD"),
    ], args)


def command_source(args: argparse.Namespace) -> int:
    _, summary, _ = load_report_data(args)
    rows = [
        flatten_summary_row_for_cli(row, {"app": row["app"]})
        for row in summary.get("sources", [])
    ]
    return emit_rows(rows, [
        ("app", "App"),
        ("threads", "Threads"),
        ("requests", "Requests"),
        ("events", "Events"),
        ("active_min", "Active min"),
        ("tokens", "Tokens"),
        ("input", "Input"),
        ("cached", "Cached"),
        ("output", "Output"),
        ("cache_hit", "Cache hit"),
        ("credits", "Credits"),
        ("api_usd", "USD"),
    ], args)


def command_source_audit(args: argparse.Namespace) -> int:
    report_tz = resolve_timezone(getattr(args, "timezone", None))
    output_dir = Path(args.output_dir).expanduser()
    audit = build_source_audit(args, report_tz=report_tz)
    paths = write_source_audit(output_dir, audit)

    if args.format == "json":
        print(json.dumps(audit, indent=2, ensure_ascii=False))
    elif args.format == "markdown":
        print(render_source_audit_markdown(audit), end="")
    else:
        emit_rows(source_audit_cli_rows(audit), [
            ("source", "Source"),
            ("status", "Status"),
            ("records", "Records"),
            ("range", "Range"),
            ("tokens", "Tokens"),
            ("credits", "Credits"),
            ("usd", "USD"),
            ("blocked", "Blocked"),
        ], args)
        for name, path in paths.items():
            print(f"{name}={path}")
    return 0


def billing_cli_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "provider": row.get("provider", ""),
            "status": row.get("status", ""),
            "period": row.get("period", ""),
            "official_usd": row.get("official_usd", ""),
            "env_var": row.get("env_var", ""),
            "blocked": row.get("blocked", ""),
        }
        for row in rows
    ]


def command_billing(args: argparse.Namespace) -> int:
    rows = build_billing_connectors(fetch=bool(args.fetch), days=int(args.days), timeout=int(args.timeout_seconds))
    if args.format == "json":
        print(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "connectors": rows}, indent=2, ensure_ascii=False))
        return 0
    return emit_rows(billing_cli_rows(rows), [
        ("provider", "Provider"),
        ("status", "Status"),
        ("period", "Period"),
        ("official_usd", "Official USD"),
        ("env_var", "Env var"),
        ("blocked", "Blocked"),
    ], args)


class DashboardTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def live_dashboard_document(document: str, refresh_seconds: int, url: str) -> str:
    live_pill = '<span class="pill">Live auto-refresh</span>'
    if "Live auto-refresh" not in document:
        document = document.replace('<span class="pill">Private reports</span>', f'{live_pill}\n      <span class="pill">Private reports</span>', 1)
    live_script = f"""
  <script>
    window.setTimeout(() => window.location.reload(), {int(refresh_seconds) * 1000});
    console.info("AI Coding Usage Tracker live dashboard: {html.escape(url)}");
  </script>
"""
    return document.replace("</body>", live_script + "</body>")


def serve_file_response(handler: http.server.BaseHTTPRequestHandler, path: Path) -> None:
    try:
        data = path.read_bytes()
    except OSError:
        handler.send_error(404)
        return
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def make_dashboard_handler(args: argparse.Namespace, refresh_seconds: int, url: str) -> type[http.server.BaseHTTPRequestHandler]:
    class DashboardHandler(http.server.BaseHTTPRequestHandler):
        server_version = f"AICodingUsageTracker/{VERSION}"

        def log_message(self, format: str, *values: Any) -> None:
            sys.stderr.write(f"[serve] {self.address_string()} - {format % values}\n")

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            request_path = parsed.path or "/"
            output_dir = Path(args.output_dir).expanduser()

            try:
                if request_path in {"/", "/dashboard.html"}:
                    threads, summary, report_tz = load_report_data(args)
                    paths = write_reports(threads, summary, output_dir, report_tz=report_tz)
                    document = paths["dashboard_html"].read_text(encoding="utf-8")
                    data = live_dashboard_document(document, refresh_seconds, url).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                    return

                if request_path == "/api/summary":
                    threads, summary, report_tz = load_report_data(args)
                    model = build_gui_view_model(threads, summary, report_tz=report_tz)
                    payload = json.dumps({
                        "summary": serializable_summary(summary),
                        "view": model,
                        "alerts": summary.get("alerts", []),
                        "billing_connectors": summary.get("billing_connectors", []),
                    }, default=str, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(payload)
                    return

                relative = request_path.lstrip("/")
                if relative in {"codex_usage_summary.json", "threads.csv", "daily.csv", "sources.csv", "projects.csv", "models.csv", "source_audit.json", "source_audit.md"}:
                    candidate = (output_dir / relative).resolve()
                    root = output_dir.resolve()
                    if root == candidate or root in candidate.parents:
                        serve_file_response(self, candidate)
                        return

                self.send_error(404)
            except Exception as exc:
                data = f"Dashboard refresh failed: {html.escape(str(exc))}".encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

    return DashboardHandler


def start_dashboard_server(args: argparse.Namespace, host: str, port: int, refresh_seconds: int) -> tuple[DashboardTCPServer, str]:
    server = DashboardTCPServer((host, port), make_dashboard_handler(args, refresh_seconds, ""))
    actual_host, actual_port = server.server_address
    url = f"http://{actual_host}:{actual_port}/"
    server.RequestHandlerClass = make_dashboard_handler(args, refresh_seconds, url)
    return server, url


def command_serve(args: argparse.Namespace) -> int:
    refresh_seconds = validate_refresh_seconds(int(args.refresh_seconds))
    server, url = start_dashboard_server(args, args.host, int(args.port), refresh_seconds)
    print(f"Serving AI Coding Usage Tracker at {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.shutdown()
        server.server_close()
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    report_tz = resolve_timezone(getattr(args, "timezone", None))
    codex_home = Path(args.codex_home).expanduser()
    claude_home = Path(args.claude_home).expanduser()
    cursor_db = Path(args.cursor_db).expanduser()
    cursor_state_db = Path(args.cursor_state_db).expanduser()
    output_dir = Path(args.output_dir).expanduser()
    redact = bool(getattr(args, "redact", False))
    sources = parse_source_filter(getattr(args, "sources", None))
    failures = 0

    def line(status: str, label: str, detail: str = "") -> None:
        print(f"[{status}] {label}" + (f" - {detail}" if detail else ""))

    line("OK", "Python", sys.version.split()[0])
    line("OK", "Selected sources", ", ".join(app_label(source) for source in sorted(sources)))

    codex_threads: list[dict[str, Any]] = []
    if "codex" in sources:
        line("OK" if codex_home.exists() else "FAIL", "Codex home", "(redacted)" if redact else str(codex_home))
        if not codex_home.exists():
            failures += 1

        rollout_files = iter_rollout_files(codex_home) if codex_home.exists() else []
        if rollout_files:
            line("OK", "Codex rollout logs", f"{len(rollout_files)} files")
        else:
            line("FAIL", "Codex rollout logs", "no rollout-*.jsonl files found")
            failures += 1

        codex_threads = load_threads(codex_home, days=args.days, report_tz=report_tz) if rollout_files else []
        codex_threads = filter_threads_by_date(
            codex_threads,
            since=getattr(args, "since", None),
            until=getattr(args, "until", None),
            report_tz=report_tz,
        )
        if codex_threads:
            line("OK", "Codex parser", f"{len(codex_threads)} threads parsed")
        else:
            line("FAIL", "Codex parser", "no threads parsed in the selected range")
            failures += 1

    claude_threads: list[dict[str, Any]] = []
    if "claude" in sources:
        line("OK" if claude_home.exists() else "FAIL", "Claude home", "(redacted)" if redact else str(claude_home))
        if not claude_home.exists():
            failures += 1
        claude_files = iter_claude_files(claude_home) if claude_home.exists() else []
        if claude_files:
            line("OK", "Claude Code logs", f"{len(claude_files)} files")
        else:
            line("FAIL", "Claude Code logs", "no project JSONL files found")
            failures += 1
        claude_threads = load_claude_threads(claude_home, days=args.days, report_tz=report_tz) if claude_files else []
        claude_threads = filter_threads_by_date(
            claude_threads,
            since=getattr(args, "since", None),
            until=getattr(args, "until", None),
            report_tz=report_tz,
        )
        if claude_threads:
            line("OK", "Claude parser", f"{len(claude_threads)} sessions parsed")
        else:
            line("FAIL", "Claude parser", "no sessions parsed in the selected range")
            failures += 1

    cursor_threads: list[dict[str, Any]] = []
    if "cursor" in sources:
        cursor_projects_home = Path(args.cursor_projects_home).expanduser()
        line("OK" if cursor_db.exists() else "WARN", "Cursor AI tracking DB", "(redacted)" if redact else str(cursor_db))
        line("OK" if cursor_projects_home.exists() else "WARN", "Cursor Agent transcripts", "(redacted)" if redact else str(cursor_projects_home))
        line("OK" if cursor_state_db.exists() else "WARN", "Cursor state DB", "(redacted)" if redact else str(cursor_state_db))
        if not cursor_db.exists() and not cursor_projects_home.exists():
            failures += 1
        cursor_threads = load_cursor_threads(
            cursor_db,
            cursor_state_db=cursor_state_db,
            cursor_projects_home=cursor_projects_home,
            days=args.days,
            report_tz=report_tz,
        ) if cursor_db.exists() or cursor_projects_home.exists() else []
        cursor_threads = filter_threads_by_date(
            cursor_threads,
            since=getattr(args, "since", None),
            until=getattr(args, "until", None),
            report_tz=report_tz,
        )
        if cursor_threads:
            token_threads = [thread for thread in cursor_threads if usage_total(thread.get("usage") or {}) > 0]
            line("OK", "Cursor parser", f"{len(cursor_threads)} conversations parsed")
            if token_threads:
                total_tokens = sum(usage_total(thread.get("usage") or {}) for thread in token_threads)
                line("OK", "Cursor token totals", f"{len(token_threads)} conversations / {total_tokens:,} estimated tokens")
            else:
                line("WARN", "Cursor token totals", "no token estimates found; check ~/.cursor/projects agent transcripts")
        else:
            line("FAIL", "Cursor parser", "no conversations parsed in the selected range")
            failures += 1

        cursor_daily_stats = read_cursor_daily_stats(cursor_state_db)
        if cursor_daily_stats:
            line(
                "OK",
                "Cursor legacy daily stats",
                f"{len(cursor_daily_stats)} daily rows from {cursor_daily_stats[0]['date']} to {cursor_daily_stats[-1]['date']}",
            )
        else:
            line("WARN", "Cursor legacy daily stats", "no aiCodeTracking.dailyStats rows found")

    threads = codex_threads + claude_threads + cursor_threads

    unknown_models = sorted({
        thread.get("model")
        for thread in threads
        if thread.get("model")
        and usage_total(thread.get("usage") or {}) > 0
        and not rates_for_model(thread.get("model"))
    })
    if unknown_models:
        line("WARN", "Pricing table", "unknown priced models: " + ", ".join(unknown_models[:5]))
    else:
        line("OK", "Pricing table", f"{len(MODEL_RATES)} model families")

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        probe = output_dir / ".codex-usage-tracker-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        line("OK", "Output directory", "(redacted)" if redact else str(output_dir))
    except Exception as exc:
        line("FAIL", "Output directory", str(exc))
        failures += 1

    wakatime_cli = find_wakatime_cli()
    line("OK" if wakatime_cli else "WARN", "WakaTime CLI", wakatime_cli or "not found; sync is optional")
    line("OK" if has_wakatime_key() else "WARN", "WakaTime API key", "configured" if has_wakatime_key() else "not configured; sync is optional")

    if os.name == "nt":
        try:
            proc = subprocess.run(
                ["schtasks", "/Query", "/TN", "CodexAppUsageTracker"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            line("OK" if proc.returncode == 0 else "WARN", "Windows scheduled task", "CodexAppUsageTracker" if proc.returncode == 0 else "not installed")
        except Exception as exc:
            line("WARN", "Windows scheduled task", str(exc))

    return 1 if failures else 0


def command_sync_wakatime(args: argparse.Namespace) -> int:
    codex_home = Path(args.codex_home).expanduser()
    threads = load_threads(codex_home, days=args.days)
    threads = filter_threads_by_date(
        threads,
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
    )
    result = sync_wakatime(
        threads,
        since_minutes=args.wakatime_since_minutes,
        interval_minutes=args.wakatime_interval_minutes,
        max_heartbeats=args.wakatime_max_heartbeats,
        state_file=Path(args.state_file).expanduser(),
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


def command_run(args: argparse.Namespace) -> int:
    report_code = command_report(args)
    if args.sync_wakatime:
        sync_code = command_sync_wakatime(args)
        return report_code or sync_code
    return report_code


def apply_dark_ttk_theme(root: Any, style: Any) -> None:
    theme = DARK_THEME

    def configure(style_name: str, **options: Any) -> None:
        try:
            style.configure(style_name, **options)
        except Exception:
            pass

    def style_map(style_name: str, **options: Any) -> None:
        try:
            style.map(style_name, **options)
        except Exception:
            pass

    try:
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass

    try:
        root.configure(bg=theme["bg"])
    except Exception:
        pass

    configure(".", background=theme["bg"], foreground=theme["ink"], font=GUI_FONTS["body"])
    configure("TFrame", background=theme["bg"])
    configure("TLabel", background=theme["bg"], foreground=theme["ink"])
    configure(
        "TButton",
        background=theme["panel_alt"],
        foreground=theme["ink"],
        borderwidth=0,
        padding=(14, 7),
        font=GUI_FONTS["body"],
    )
    style_map(
        "TButton",
        background=[("pressed", theme["subtle"]), ("active", theme["card_hover"])],
        foreground=[("disabled", theme["muted"])],
    )
    configure("Primary.TButton", background=theme["selected"], foreground="#ffffff")
    style_map(
        "Primary.TButton",
        background=[("pressed", "#2563eb"), ("active", "#4f93f8")],
        foreground=[("disabled", theme["muted"])],
    )
    configure("TCheckbutton", background=theme["bg"], foreground=theme["muted"], font=GUI_FONTS["caption"])
    style_map(
        "TCheckbutton",
        background=[("active", theme["bg"])],
        foreground=[("disabled", theme["muted"]), ("selected", theme["ink"])],
    )
    configure(
        "TEntry",
        fieldbackground=theme["field"],
        foreground=theme["ink"],
        bordercolor=theme["border_soft"],
        lightcolor=theme["border_soft"],
        darkcolor=theme["border_soft"],
        padding=(8, 6),
    )
    configure("TNotebook", background=theme["bg"], borderwidth=0, tabmargins=(0, 6, 0, 0))
    configure(
        "TNotebook.Tab",
        background=theme["bg"],
        foreground=theme["muted"],
        padding=(14, 8),
        font=GUI_FONTS["body"],
        borderwidth=0,
    )
    style_map(
        "TNotebook.Tab",
        background=[("selected", theme["panel"]), ("active", theme["panel_alt"])],
        foreground=[("selected", theme["ink"]), ("active", theme["ink"])],
    )
    configure(
        "TLabelframe",
        background=theme["bg"],
        foreground=theme["muted"],
        bordercolor=theme["border_soft"],
        relief="flat",
        borderwidth=1,
    )
    configure("TLabelframe.Label", background=theme["bg"], foreground=theme["muted"], font=GUI_FONTS["caption"])
    configure(
        "Treeview",
        background=theme["panel"],
        fieldbackground=theme["panel"],
        foreground=theme["ink"],
        bordercolor=theme["border_soft"],
        rowheight=28,
        font=GUI_FONTS["body"],
    )
    configure(
        "Treeview.Heading",
        background=theme["panel_alt"],
        foreground=theme["muted"],
        bordercolor=theme["border_soft"],
        font=GUI_FONTS["caption"],
        relief="flat",
    )
    style_map("Treeview", background=[("selected", theme["selected"])], foreground=[("selected", "#ffffff")])


class CodexUsageTrackerGui:
    def __init__(self, args: argparse.Namespace, tk: Any, ttk: Any, messagebox: Any) -> None:
        self.args = args
        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.theme = DARK_THEME
        self.refresh_seconds = validate_refresh_seconds(int(args.refresh_seconds))
        self.events: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self.worker_running = False
        self.report_running = False
        self.closed = False
        self.previous_total_tokens: int | None = None
        self.table_data: dict[str, list[tuple[str, ...]]] = {}
        self.table_widgets: dict[str, Any] = {}
        self.chart_canvases: dict[str, Any] = {}
        self.metric_vars: dict[str, Any] = {}
        self.provider_card_vars: dict[str, dict[str, Any]] = {}
        self.web_server: DashboardTCPServer | None = None
        self.web_url: str | None = None
        self.header_strip: Any = None
        self.share_donut: Any = None
        self.activity_dot: Any = None

        self.root = tk.Tk()
        self.brand_icons = BrandIconManager(tk)
        self.root.title(f"AI Coding Usage Tracker v{VERSION}")
        self.root.geometry("1320x860")
        self.root.minsize(1080, 720)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.status_var = tk.StringVar(value="Loading local AI coding usage…")
        self.refresh_meta_var = tk.StringVar(value="Last refresh: pending")
        self.activity_var = tk.StringVar(value="Latest activity: pending")
        self.pricing_var = tk.StringVar(value="")
        self.search_var = tk.StringVar(value="")
        self.auto_refresh_var = tk.BooleanVar(value=True)

        self.build_ui()
        self.search_var.trace_add("write", lambda *_: self.apply_filter())
        self.root.after(100, self.poll_events)
        self.root.after(200, self.start_refresh)
        self.root.after(self.refresh_seconds * 1000, self.auto_refresh_tick)

    def _panel(
        self,
        parent: Any,
        *,
        bg: str | None = None,
        padx: int = 16,
        pady: int = 14,
        border: bool = True,
    ) -> Any:
        bg = bg or self.theme["panel"]
        shell = self.tk.Frame(parent, bg=self.theme["bg"])
        body_kwargs: dict[str, Any] = {"bg": bg}
        if border:
            body_kwargs.update(
                highlightthickness=1,
                highlightbackground=self.theme["border_soft"],
                highlightcolor=self.theme["border_soft"],
            )
        body = self.tk.Frame(shell, **body_kwargs)
        body.pack(fill="both", expand=True)
        content = self.tk.Frame(body, bg=bg, padx=padx, pady=pady)
        content.pack(fill="both", expand=True)
        shell.content = content
        return shell

    def _card(self, parent: Any, *, accent: str | None = None, padx: int = 16, pady: int = 14) -> Any:
        shell = self.tk.Frame(parent, bg=self.theme["bg"])
        body = self.tk.Frame(
            shell,
            bg=self.theme["card"],
            highlightthickness=1,
            highlightbackground=self.theme["border_soft"],
        )
        body.pack(fill="both", expand=True)
        if accent:
            stripe = self.tk.Frame(body, bg=accent, width=4)
            stripe.pack(side="left", fill="y")
        content = self.tk.Frame(body, bg=self.theme["card"], padx=padx, pady=pady)
        content.pack(side="left", fill="both", expand=True)
        shell.content = content
        return shell

    def _widget_bg(self, parent: Any, bg: str | None = None) -> str:
        if bg:
            return bg
        try:
            value = parent.cget("bg")
            if value:
                return str(value)
        except Exception:
            pass
        return self.theme["bg"]

    def _label(
        self,
        parent: Any,
        text: str = "",
        *,
        font: tuple[str, int, str] | tuple[str, int] = GUI_FONTS["body"],
        color: str | None = None,
        bg: str | None = None,
        textvariable: Any = None,
        wrap: int | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "font": font,
            "fg": color or self.theme["ink"],
            "bg": self._widget_bg(parent, bg),
        }
        if textvariable is not None:
            kwargs["textvariable"] = textvariable
        else:
            kwargs["text"] = text
        if wrap:
            kwargs["wraplength"] = wrap
            kwargs["justify"] = "left"
        return self.tk.Label(parent, **kwargs)

    def _stat_chip(self, parent: Any, title: str, textvariable: Any) -> None:
        chip = self.tk.Frame(parent, bg=self.theme["panel_alt"], padx=12, pady=8)
        chip.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._label(chip, title, font=GUI_FONTS["caption"], color=self.theme["muted"], bg=self.theme["panel_alt"]).pack(anchor="w")
        self._label(
            chip,
            font=GUI_FONTS["metric"],
            textvariable=textvariable,
            bg=self.theme["panel_alt"],
        ).pack(anchor="w", pady=(2, 0))

    def _make_app_badge(self, parent: Any, app_key: str, *, size: int = 44, bg: str | None = None) -> Any:
        bg = self._widget_bg(parent, bg)
        logo = self.brand_icons.photo(app_key, size)
        if logo is not None:
            shell = self.tk.Frame(parent, bg=bg)
            label = self.tk.Label(shell, image=logo, bg=bg, bd=0)
            label.image = logo
            label.pack()
            return shell
        canvas = self.tk.Canvas(parent, width=size, height=size, bg=bg, highlightthickness=0, bd=0)
        draw_app_badge(canvas, app_key=app_key, size=size, bg=bg, logo=None)
        return canvas

    def _redraw_header_strip(self, _event: Any = None) -> None:
        if self.header_strip is None:
            return
        width = max(int(self.header_strip.winfo_width() or 0), 1)
        draw_accent_header_strip(self.header_strip, width=width, bg=self.theme["panel"])

    def _provider_usage(self, row: dict[str, Any]) -> dict[str, int]:
        return {
            "input_tokens": int(row.get("lifetime_input_tokens") or 0),
            "cached_input_tokens": int(row.get("lifetime_cached_tokens") or 0),
            "output_tokens": int(row.get("lifetime_output_tokens") or 0),
        }

    def build_ui(self) -> None:
        ttk = self.ttk
        tk = self.tk

        try:
            style = ttk.Style()
            apply_dark_ttk_theme(self.root, style)
        except Exception:
            pass

        outer = ttk.Frame(self.root, padding=(18, 16, 18, 14))
        outer.pack(fill="both", expand=True)

        header = self._panel(outer, padx=18, pady=16)
        header.pack(fill="x", pady=(0, 14))
        header_body = header.content

        self.header_strip = tk.Canvas(header_body, height=5, bg=self.theme["panel"], highlightthickness=0, bd=0)
        self.header_strip.pack(fill="x", pady=(0, 12))
        self.header_strip.bind("<Configure>", self._redraw_header_strip)

        title_row = tk.Frame(header_body, bg=self.theme["panel"])
        title_row.pack(fill="x")
        title_left = tk.Frame(title_row, bg=self.theme["panel"])
        title_left.pack(side="left", fill="x", expand=True)
        self._label(title_left, "AI Coding Usage Tracker", font=GUI_FONTS["title"], bg=self.theme["panel"]).pack(anchor="w")
        self._label(
            title_left,
            "Local usage across Codex, Claude Code, and Cursor",
            font=GUI_FONTS["caption"],
            color=self.theme["muted"],
            bg=self.theme["panel"],
        ).pack(anchor="w", pady=(4, 0))

        legend_row = tk.Frame(title_row, bg=self.theme["panel"])
        legend_row.pack(side="right")
        for app_key in ("codex", "claude", "cursor"):
            brand = brand_for_app(app_key)
            legend_item = tk.Frame(legend_row, bg=self.theme["panel"])
            legend_item.pack(side="left", padx=(10, 0))
            self._make_app_badge(legend_item, app_key, size=30, bg=self.theme["panel"]).pack(side="left")
            self._label(
                legend_item,
                brand["label"],
                font=GUI_FONTS["caption"],
                color=brand["accent"],
                bg=self.theme["panel"],
            ).pack(side="left", padx=(6, 0))

        version_row = tk.Frame(header_body, bg=self.theme["panel"])
        version_row.pack(fill="x", pady=(10, 0))
        self._label(
            version_row,
            f"v{VERSION}",
            font=GUI_FONTS["caption"],
            color=self.theme["muted"],
            bg=self.theme["panel"],
        ).pack(side="left")

        meta_row = tk.Frame(header_body, bg=self.theme["panel"])
        meta_row.pack(fill="x", pady=(8, 0))
        self._label(meta_row, textvariable=self.refresh_meta_var, font=GUI_FONTS["caption"], color=self.theme["muted"], bg=self.theme["panel"]).pack(side="left")
        self._label(meta_row, "  ·  ", font=GUI_FONTS["caption"], color=self.theme["border"], bg=self.theme["panel"]).pack(side="left")
        self.activity_dot = tk.Canvas(meta_row, width=10, height=10, bg=self.theme["panel"], highlightthickness=0, bd=0)
        self.activity_dot.pack(side="left", padx=(0, 6))
        self._label(meta_row, textvariable=self.activity_var, font=GUI_FONTS["caption"], color=self.theme["muted"], bg=self.theme["panel"]).pack(side="left")

        self._label(
            header_body,
            textvariable=self.pricing_var,
            font=GUI_FONTS["caption"],
            color=self.theme["muted"],
            bg=self.theme["panel"],
            wrap=900,
        ).pack(anchor="w", pady=(8, 0))

        self._label(
            header_body,
            textvariable=self.status_var,
            font=GUI_FONTS["body"],
            color=self.theme["green"],
            bg=self.theme["panel"],
        ).pack(anchor="w", pady=(6, 0))

        toolbar = self._panel(outer, bg=self.theme["bg"], padx=0, pady=0, border=False)
        toolbar.pack(fill="x", pady=(0, 14))
        controls = tk.Frame(toolbar.content, bg=self.theme["bg"])
        controls.pack(fill="x")

        ttk.Button(controls, text="Refresh now", style="Primary.TButton", command=self.start_refresh).pack(side="left")
        ttk.Checkbutton(controls, text="Auto-refresh", variable=self.auto_refresh_var).pack(side="left", padx=(14, 18))
        self._label(controls, "Search tables", font=GUI_FONTS["caption"], color=self.theme["muted"], bg=self.theme["bg"]).pack(side="left")
        ttk.Entry(controls, textvariable=self.search_var, width=28).pack(side="left", padx=(6, 16))
        ttk.Button(controls, text="HTML report", command=self.start_report).pack(side="left")
        ttk.Button(controls, text="Web dashboard", command=self.open_web_dashboard).pack(side="left", padx=(8, 0))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)

        overview = ttk.Frame(notebook, padding=(4, 12, 4, 4))
        notebook.add(overview, text="Overview")

        hero = self._card(overview, accent=self.theme["selected"], padx=20, pady=18)
        hero.pack(fill="x", pady=(0, 12))
        hero_content = hero.content
        hero_top = tk.Frame(hero_content, bg=self.theme["card"])
        hero_top.pack(fill="x")
        hero_title = tk.Frame(hero_top, bg=self.theme["card"])
        hero_title.pack(side="left", fill="x", expand=True)
        self._label(hero_title, "Combined lifetime usage", font=GUI_FONTS["section"], color=self.theme["muted"], bg=self.theme["card"]).pack(anchor="w")
        self._label(
            hero_title,
            "Share of lifetime tokens across connected apps",
            font=GUI_FONTS["caption"],
            color=self.theme["muted"],
            bg=self.theme["card"],
        ).pack(anchor="w", pady=(4, 0))
        self.share_donut = tk.Canvas(hero_top, width=92, height=92, bg=self.theme["card"], highlightthickness=0, bd=0)
        self.share_donut.pack(side="right")
        self.combined_metric_vars: dict[str, Any] = {}
        hero_metrics = tk.Frame(hero_content, bg=self.theme["card"])
        hero_metrics.pack(fill="x", pady=(10, 0))
        hero_specs = [
            ("lifetime_tokens", "Total tokens", GUI_FONTS["hero"]),
            ("lifetime_usd", "Estimated USD", ("Segoe UI Semibold", 18, "bold")),
            ("today_tokens", "Today tokens", GUI_FONTS["metric"]),
            ("today_usd", "Today USD", GUI_FONTS["metric"]),
            ("active_minutes", "Active min", GUI_FONTS["metric"]),
            ("threads", "Threads", GUI_FONTS["metric"]),
        ]
        for index, (key, label, font) in enumerate(hero_specs):
            cell = tk.Frame(hero_metrics, bg=self.theme["card"])
            cell.grid(row=0, column=index, sticky="w", padx=(0, 24))
            self._label(cell, label, font=GUI_FONTS["caption"], color=self.theme["muted"], bg=self.theme["card"]).pack(anchor="w")
            value = tk.StringVar(value="—")
            self.combined_metric_vars[key] = value
            self._label(cell, textvariable=value, font=font, bg=self.theme["card"]).pack(anchor="w", pady=(4, 0))

        self.provider_cards_frame = tk.Frame(overview, bg=self.theme["bg"])
        self.provider_cards_frame.pack(fill="x", pady=(0, 12))

        glance = self._panel(overview, bg=self.theme["panel_alt"], padx=12, pady=10)
        glance.pack(fill="x", pady=(0, 12))
        glance_row = tk.Frame(glance.content, bg=self.theme["panel_alt"])
        glance_row.pack(fill="x")
        self.metric_vars = {}
        glance_specs = [
            "All apps today tokens",
            "Estimated USD (today)",
            "Events",
            "Requests",
            "Top app",
            "Top model",
        ]
        for title in glance_specs:
            var = tk.StringVar(value="—")
            self.metric_vars[title] = var
            self._stat_chip(glance_row, title, var)

        hidden_metrics = [
            "All apps lifetime tokens",
            "Codex lifetime tokens",
            "Claude lifetime tokens",
            "Cursor lifetime tokens",
            "Threads",
            "Estimated USD (lifetime)",
            "Estimated Codex credits",
            "Estimated active time",
            "Top project",
        ]
        for title in hidden_metrics:
            self.metric_vars[title] = tk.StringVar(value="—")

        charts = tk.Frame(overview, bg=self.theme["bg"])
        charts.pack(fill="both", expand=True)
        charts.columnconfigure(0, weight=1)
        charts.columnconfigure(1, weight=1)
        self.add_chart(charts, "sources", "App usage (lifetime)", GUI_CHART_COLORS["sources"], row=0, column=0)
        self.add_chart(charts, "daily", "Daily tokens", GUI_CHART_COLORS["daily"], row=0, column=1)

        providers = ttk.Frame(notebook, padding=(4, 12, 4, 4))
        notebook.add(providers, text="Lifetime Totals")
        self._label(
            providers,
            "Lifetime and today usage for Codex, Claude Code, Cursor, plus combined totals.",
            font=GUI_FONTS["caption"],
            color=self.theme["muted"],
            wrap=760,
        ).pack(anchor="w", pady=(0, 10))
        self.provider_detail_frame = tk.Frame(providers, bg=self.theme["bg"])
        self.provider_detail_frame.pack(fill="x", pady=(0, 12))
        self.table_widgets["providers"] = self.make_table(providers)

        detail_charts = tk.Frame(providers, bg=self.theme["bg"])
        detail_charts.pack(fill="both", expand=True, pady=(8, 0))
        detail_charts.columnconfigure(0, weight=1)
        detail_charts.columnconfigure(1, weight=1)
        self.add_chart(detail_charts, "projects", "Top projects", GUI_CHART_COLORS["projects"], row=0, column=0)
        self.add_chart(detail_charts, "models", "Top models", GUI_CHART_COLORS["models"], row=0, column=1)

        table_specs = {
            "sources": "Apps",
            "daily": "Daily",
            "projects": "Projects",
            "models": "Models",
            "threads": "Threads",
            "alerts": "Signals",
            "billing": "Billing",
        }
        for key, title in table_specs.items():
            frame = ttk.Frame(notebook, padding=(4, 12, 4, 4))
            notebook.add(frame, text=title)
            self.table_widgets[key] = self.make_table(frame)

    def add_chart(
        self,
        parent: Any,
        key: str,
        title: str,
        color: str,
        *,
        row: int = 0,
        column: int = 0,
    ) -> None:
        shell = self._panel(parent, padx=12, pady=12)
        shell.grid(row=row, column=column, sticky="nsew", padx=(0 if column == 0 else 6, 6 if column == 0 else 0), pady=0)
        parent.rowconfigure(row, weight=1)
        self._label(shell.content, title, font=GUI_FONTS["section"], color=self.theme["muted"], bg=self.theme["panel"]).pack(anchor="w", pady=(0, 8))
        canvas = self.tk.Canvas(
            shell.content,
            height=168,
            bg=self.theme["panel"],
            highlightthickness=0,
            bd=0,
        )
        canvas.pack(fill="both", expand=True)
        canvas.chart_color = color
        self.chart_canvases[key] = canvas

    def make_table(self, parent: Any) -> Any:
        frame = self.ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = self.ttk.Treeview(frame, show="headings", height=18)
        yscroll = self.ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        xscroll = self.ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return tree

    def configure_table(self, key: str, columns: tuple[tuple[str, str], ...]) -> None:
        tree = self.table_widgets[key]
        column_ids = [column_id for column_id, _ in columns]
        tree.configure(columns=column_ids)
        for column_id, label in columns:
            tree.heading(column_id, text=label)
            width = 300 if column_id in {"detail", "blocked"} else 230 if column_id in {"title", "folder"} else 120
            anchor = "w" if column_id in {"app", "title", "project", "folder", "model", "last_activity", "detail", "blocked", "provider", "status", "env_var", "basis"} else "e"
            tree.column(column_id, width=width, minwidth=80, anchor=anchor, stretch=True)
        tree.tag_configure("even", background=self.theme["panel"])
        tree.tag_configure("odd", background=self.theme["panel_alt"])

    def start_refresh(self) -> None:
        if self.worker_running:
            self.status_var.set("Refresh already running…")
            return
        self.worker_running = True
        sources = parse_source_filter(getattr(self.args, "sources", None))
        if "cursor" in sources:
            self.status_var.set("Scanning Cursor logs — first refresh can take up to a minute.")
        else:
            self.status_var.set("Refreshing dashboard…")
        thread = threading.Thread(target=self.refresh_worker, daemon=True)
        thread.start()

    def refresh_worker(self) -> None:
        try:
            threads, summary, report_tz = load_report_data(self.args)
            model = build_gui_view_model(
                threads,
                summary,
                report_tz=report_tz,
                previous_total_tokens=self.previous_total_tokens,
            )
            self.events.put(("refresh_ok", model))
        except Exception as exc:
            self.events.put(("refresh_error", str(exc)))

    def start_report(self) -> None:
        if self.report_running:
            self.status_var.set("HTML report generation already running...")
            return
        self.report_running = True
        self.status_var.set("Generating HTML, CSV, and JSON reports...")
        thread = threading.Thread(target=self.report_worker, daemon=True)
        thread.start()

    def report_worker(self) -> None:
        try:
            output_dir = Path(self.args.output_dir).expanduser()
            threads, summary, report_tz = load_report_data(self.args)
            paths = write_reports(threads, summary, output_dir, report_tz=report_tz)
            self.events.put(("report_ok", paths["dashboard_html"]))
        except Exception as exc:
            self.events.put(("report_error", str(exc)))

    def open_web_dashboard(self) -> None:
        try:
            if self.web_server is None:
                self.web_server, self.web_url = start_dashboard_server(
                    self.args,
                    "127.0.0.1",
                    0,
                    self.refresh_seconds,
                )
                thread = threading.Thread(target=self.web_server.serve_forever, daemon=True)
                thread.start()
            if self.web_url:
                webbrowser.open(self.web_url)
                self.status_var.set(f"Live web dashboard opened: {self.web_url}")
        except Exception as exc:
            self.status_var.set(f"Could not open live web dashboard: {exc}")

    def poll_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break

            kind = event[0]
            if kind == "refresh_ok":
                self.worker_running = False
                self.update_view(event[1])
            elif kind == "refresh_error":
                self.worker_running = False
                self.status_var.set(f"Refresh failed; keeping last data visible: {event[1]}")
            elif kind == "report_ok":
                self.report_running = False
                path = event[1]
                self.status_var.set(f"HTML report generated: {path}")
                self.messagebox.showinfo("Report generated", f"Dashboard written to:\n{path}")
            elif kind == "report_error":
                self.report_running = False
                self.status_var.set(f"Report generation failed: {event[1]}")

        if not self.closed:
            self.root.after(100, self.poll_events)

    def auto_refresh_tick(self) -> None:
        if self.auto_refresh_var.get():
            self.start_refresh()
        if not self.closed:
            self.root.after(self.refresh_seconds * 1000, self.auto_refresh_tick)

    def update_view(self, model: dict[str, Any]) -> None:
        self.previous_total_tokens = int(model["total_tokens"])
        status = model["activity_status"]
        self.refresh_meta_var.set(
            f"Last refresh {model['generated_at']}  ·  Token delta {model['token_delta'] or 'initial'}"
        )
        self.activity_var.set(f"Latest activity {model['latest_activity']}  ·  Status {status}")
        self.pricing_var.set(
            "Estimates from local logs. Cursor includes context-cache replay like Claude cache reads."
        )
        for label, value in model["metrics"]:
            if label in self.metric_vars:
                self.metric_vars[label].set(value)

        self.update_provider_cards(
            model.get("provider_summaries") or [],
            model.get("combined_totals") or {},
        )
        self._update_share_donut(model.get("provider_summaries") or [])
        self._update_activity_dot(str(model.get("activity_status") or "") == "Active")
        combined = model.get("combined_totals") or {}
        if combined and self.combined_metric_vars:
            self.combined_metric_vars["lifetime_tokens"].set(number(combined.get("lifetime_tokens", 0)))
            self.combined_metric_vars["lifetime_usd"].set(f"${number(combined.get('lifetime_usd', 0.0))}")
            self.combined_metric_vars["today_tokens"].set(number(combined.get("today_tokens", 0)))
            self.combined_metric_vars["today_usd"].set(f"${number(combined.get('today_usd', 0.0))}")
            self.combined_metric_vars["active_minutes"].set(str(combined.get("active_minutes", 0)))
            self.combined_metric_vars["threads"].set(number(combined.get("threads", 0)))

        for key, table in model["tables"].items():
            self.configure_table(key, table["columns"])
            self.table_data[key] = list(table["rows"])

        self.draw_chart(self.chart_canvases["sources"], model["charts"]["sources"], chart_key="sources")
        self.draw_chart(self.chart_canvases["daily"], model["charts"]["daily"], chart_key="daily")
        self.draw_chart(self.chart_canvases["projects"], model["charts"]["projects"], chart_key="projects")
        self.draw_chart(self.chart_canvases["models"], model["charts"]["models"], chart_key="models")
        self.apply_filter()
        status = "Dashboard updated."
        truncated = model.get("truncated_tables") or {}
        if truncated:
            parts = [
                f"{table}: showing {shown:,} of {total:,}"
                for table, (shown, total) in truncated.items()
            ]
            status = f"{status} Large tables capped for responsiveness ({'; '.join(parts)}). Use HTML report for full data."
        self.status_var.set(status)

    def _update_share_donut(self, providers: list[dict[str, Any]]) -> None:
        if self.share_donut is None:
            return
        slices = [
            (
                str(row.get("app") or ""),
                int(row.get("lifetime_tokens") or 0),
                GUI_APP_ACCENTS.get(str(row.get("app_key") or ""), self.theme["blue"]),
            )
            for row in providers
        ]
        draw_share_donut(
            self.share_donut,
            size=92,
            slices=slices,
            bg=self.theme["card"],
            muted=self.theme["muted"],
        )

    def _update_activity_dot(self, active: bool) -> None:
        if self.activity_dot is None:
            return
        self.activity_dot.delete("all")
        color = self.theme["green"] if active else self.theme["muted"]
        self.activity_dot.create_oval(1, 1, 9, 9, fill=color, outline="")

    def update_provider_cards(
        self,
        providers: list[dict[str, Any]],
        combined_totals: dict[str, Any],
    ) -> None:
        for child in self.provider_cards_frame.winfo_children():
            child.destroy()
        for child in self.provider_detail_frame.winfo_children():
            child.destroy()
        self.provider_card_vars.clear()

        if not providers:
            self._label(
                self.provider_cards_frame,
                "No provider data in this range.",
                color=self.theme["muted"],
                bg=self.theme["bg"],
            ).pack(anchor="w")
            return

        self.provider_cards_frame.columnconfigure(0, weight=1)
        self.provider_cards_frame.columnconfigure(1, weight=1)
        self.provider_cards_frame.columnconfigure(2, weight=1)

        for index, row in enumerate(providers):
            app_key = str(row.get("app_key") or "")
            accent = GUI_APP_ACCENTS.get(app_key, self.theme["blue"])
            card = self._card(self.provider_cards_frame, accent=accent, padx=16, pady=14)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 6, 6 if index == 0 else 0))
            content = card.content

            header_row = tk.Frame(content, bg=self.theme["card"])
            header_row.pack(fill="x")
            self._make_app_badge(header_row, app_key, size=48, bg=self.theme["card"]).pack(side="left")
            title_col = tk.Frame(header_row, bg=self.theme["card"])
            title_col.pack(side="left", padx=(12, 0), fill="x", expand=True)
            self._label(
                title_col,
                str(row.get("app") or "Provider"),
                font=GUI_FONTS["section"],
                color=accent,
                bg=self.theme["card"],
            ).pack(anchor="w")
            basis = str(row.get("token_basis") or "")
            basis_label = "Exact local tokens" if basis == "exact" else "Cache-estimated tokens" if "cache" in basis else "Activity metrics"
            self._label(
                title_col,
                basis_label,
                font=GUI_FONTS["caption"],
                color=self.theme["muted"],
                bg=self.theme["card"],
            ).pack(anchor="w", pady=(2, 0))

            mix_canvas = tk.Canvas(content, height=24, bg=self.theme["card"], highlightthickness=0, bd=0)
            mix_canvas.pack(fill="x", pady=(12, 2))

            def redraw_mix(
                _event: Any = None,
                *,
                canvas: Any = mix_canvas,
                provider_row: dict[str, Any] = row,
                provider_accent: str = accent,
            ) -> None:
                width = max(int(canvas.winfo_width() or 0), 120)
                draw_token_mix_bar(
                    canvas,
                    width=width,
                    usage=self._provider_usage(provider_row),
                    accent=provider_accent,
                    subtle=self.theme["subtle"],
                    muted=self.theme["muted"],
                    bg=self.theme["card"],
                )

            mix_canvas.bind("<Configure>", redraw_mix)

            lifetime_tokens = self.tk.StringVar(value=number(row.get("lifetime_tokens", 0)))
            token_breakdown = self.tk.StringVar(
                value=(
                    f"In {number(row.get('lifetime_input_tokens', 0))}  ·  "
                    f"Cached {number(row.get('lifetime_cached_tokens', 0))}  ·  "
                    f"Out {number(row.get('lifetime_output_tokens', 0))}"
                )
            )
            today_tokens = self.tk.StringVar(value=f"{number(row.get('today_tokens', 0))} today")
            lifetime_usd = self.tk.StringVar(value=f"${number(row.get('lifetime_usd', 0.0))} lifetime")
            today_usd = self.tk.StringVar(value=f"${number(row.get('today_usd', 0.0))} today")
            meta = self.tk.StringVar(
                value=(
                    f"{number(row.get('threads', 0))} threads  ·  "
                    f"{number(row.get('requests', 0))} requests  ·  "
                    f"{row.get('active_minutes', 0)} min"
                )
            )
            self.provider_card_vars[str(row.get("app") or index)] = {
                "lifetime_tokens": lifetime_tokens,
                "today_tokens": today_tokens,
            }

            self._label(content, textvariable=lifetime_tokens, font=GUI_FONTS["hero"], bg=self.theme["card"]).pack(anchor="w", pady=(8, 0))
            if app_key == "cursor":
                self._label(
                    content,
                    "includes cache estimate",
                    font=GUI_FONTS["caption"],
                    color=self.theme["muted"],
                    bg=self.theme["card"],
                ).pack(anchor="w")
            self._label(content, textvariable=token_breakdown, font=GUI_FONTS["caption"], color=self.theme["muted"], bg=self.theme["card"]).pack(anchor="w", pady=(6, 0))
            self._label(content, textvariable=today_tokens, font=GUI_FONTS["body"], color=accent, bg=self.theme["card"]).pack(anchor="w", pady=(4, 0))
            self._label(content, textvariable=lifetime_usd, font=GUI_FONTS["caption"], color=self.theme["muted"], bg=self.theme["card"]).pack(anchor="w", pady=(8, 0))
            self._label(content, textvariable=today_usd, font=GUI_FONTS["caption"], color=self.theme["muted"], bg=self.theme["card"]).pack(anchor="w")
            self._label(content, textvariable=meta, font=GUI_FONTS["caption"], color=self.theme["muted"], bg=self.theme["card"]).pack(anchor="w", pady=(8, 0))

            detail = self._card(self.provider_detail_frame, accent=accent, padx=14, pady=12)
            detail.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 6, 6 if index == 0 else 0))
            self.provider_detail_frame.columnconfigure(index, weight=1)
            detail_content = detail.content
            detail_header = tk.Frame(detail_content, bg=self.theme["card"])
            detail_header.pack(fill="x")
            self._make_app_badge(detail_header, app_key, size=34, bg=self.theme["card"]).pack(side="left")
            self._label(
                detail_header,
                f"{row.get('app')} breakdown",
                font=GUI_FONTS["section"],
                bg=self.theme["card"],
            ).pack(side="left", padx=(10, 0))
            detail_lines = [
                f"Lifetime: {number(row.get('lifetime_tokens', 0))} tokens",
                f"Input {number(row.get('lifetime_input_tokens', 0))}  ·  Cached {number(row.get('lifetime_cached_tokens', 0))}  ·  Output {number(row.get('lifetime_output_tokens', 0))}",
                f"Today: {number(row.get('today_tokens', 0))} tokens  ·  ${number(row.get('today_usd', 0.0))} USD",
                f"Lifetime USD est.: ${number(row.get('lifetime_usd', 0.0))}",
                f"{row.get('active_minutes', 0)} active min  ·  {number(row.get('threads', 0))} threads  ·  {number(row.get('requests', 0))} requests",
            ]
            for line in detail_lines:
                self._label(
                    detail_content,
                    line,
                    font=GUI_FONTS["caption"],
                    color=self.theme["muted"],
                    bg=self.theme["card"],
                    wrap=280,
                ).pack(anchor="w", pady=(4, 0))

    def _chart_bar_color(self, chart_key: str, label: str, fallback: str) -> str:
        if chart_key != "sources":
            return fallback
        normalized = label.strip().lower()
        if "codex" in normalized:
            return GUI_APP_ACCENTS["codex"]
        if "claude" in normalized:
            return GUI_APP_ACCENTS["claude"]
        if "cursor" in normalized:
            return GUI_APP_ACCENTS["cursor"]
        return fallback

    def _truncate_text(self, text: str, max_chars: int) -> str:
        text = str(text or "")
        if len(text) <= max_chars:
            return text
        return text[: max(0, max_chars - 1)] + "…"

    def draw_chart(
        self,
        canvas: Any,
        rows: list[dict[str, Any]],
        *,
        chart_key: str = "",
    ) -> None:
        canvas.delete("all")
        width = max(int(canvas.winfo_width() or 0), 360)
        height = max(int(canvas.winfo_height() or 0), 168)
        if not rows:
            canvas.create_text(
                width / 2,
                height / 2,
                text="No data in this range.",
                fill=self.theme["muted"],
                font=GUI_FONTS["body"],
            )
            return

        max_value = max([int(row["value"]) for row in rows] or [1])
        row_height = max(28, min(36, int((height - 24) / max(len(rows), 1))))
        label_width = 148 if chart_key == "sources" else 118
        value_width = min(150, max(110, width // 5))
        bar_width = max(60, width - label_width - value_width - 28)
        fallback_color = getattr(canvas, "chart_color", self.theme["blue"])

        for index, row in enumerate(rows):
            y = 14 + index * row_height
            label = self._truncate_text(str(row["label"]), 14 if chart_key == "sources" else 16)
            value = int(row["value"])
            filled = max(3, int((value / max_value) * bar_width)) if max_value else 0
            color = self._chart_bar_color(chart_key, str(row.get("label") or ""), fallback_color)
            label_x = 10
            if chart_key == "sources":
                app_key = app_key_from_label(str(row.get("label") or ""))
                icon_size = 18
                icon_y = y + row_height / 2 - icon_size / 2
                photo = self.brand_icons.photo(app_key, icon_size)
                if photo is not None:
                    canvas.create_image(label_x + icon_size / 2, y + row_height / 2, image=photo)
                    label_x += icon_size + 8
                else:
                    brand = brand_for_app(app_key)
                    canvas.create_oval(
                        label_x,
                        icon_y,
                        label_x + icon_size,
                        icon_y + icon_size,
                        fill=brand["surface"],
                        outline=brand["accent"],
                        width=1,
                    )
                    label_x += icon_size + 8
            canvas.create_text(
                label_x,
                y + row_height / 2,
                text=label,
                anchor="w",
                fill=self.theme["muted"],
                font=GUI_FONTS["caption"],
            )
            track_y = y + 8
            canvas.create_rectangle(
                label_width,
                track_y,
                label_width + bar_width,
                track_y + 12,
                fill=self.theme["subtle"],
                outline="",
            )
            if filled > 0:
                canvas.create_rectangle(
                    label_width,
                    track_y,
                    label_width + filled,
                    track_y + 12,
                    fill=color,
                    outline="",
                )
                canvas.create_rectangle(
                    label_width + filled - 4,
                    track_y,
                    label_width + filled,
                    track_y + 12,
                    fill=color,
                    outline="",
                )
            detail = self._truncate_text(str(row.get("detail") or ""), 18)
            value_text = self._truncate_text(number(value), 14)
            if detail:
                value_text = f"{value_text}  ·  {detail}"
            canvas.create_text(
                label_width + bar_width + 10,
                y + row_height / 2,
                text=value_text,
                anchor="w",
                fill=self.theme["ink"],
                font=GUI_FONTS["caption"],
            )

    def apply_filter(self) -> None:
        query = self.search_var.get().strip().lower()
        for key, tree in self.table_widgets.items():
            for item in tree.get_children():
                tree.delete(item)
            visible_index = 0
            rows = self.table_data.get(key, [])
            for row in rows:
                haystack = " ".join(str(value) for value in row).lower()
                if not query or query in haystack:
                    tag = "even" if visible_index % 2 == 0 else "odd"
                    tree.insert("", "end", values=row, tags=(tag,))
                    visible_index += 1
                    if visible_index % 100 == 0:
                        self.root.update_idletasks()

    def close(self) -> None:
        self.closed = True
        if self.web_server is not None:
            try:
                self.web_server.shutdown()
                self.web_server.server_close()
            except Exception:
                pass
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def command_gui(args: argparse.Namespace) -> int:
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except Exception as exc:
        print(f"error: Tkinter is not available: {exc}", file=sys.stderr)
        return 1

    try:
        app = CodexUsageTrackerGui(args, tk, ttk, messagebox)
    except Exception as exc:
        print(f"error: could not start GUI: {exc}", file=sys.stderr)
        return 1

    app.run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Track local AI coding usage across Codex, Claude Code, and Cursor.")
    parser.add_argument("--sources", default="codex", help="Comma-separated sources: codex, claude, cursor, or all. Default: codex")
    parser.add_argument("--codex-home", default=str(DEFAULT_CODEX_HOME), help="Codex data folder. Default: ~/.codex")
    parser.add_argument("--claude-home", default=str(DEFAULT_CLAUDE_HOME), help="Claude Code data folder. Default: ~/.claude")
    parser.add_argument("--cursor-db", default=str(DEFAULT_CURSOR_AI_DB), help="Cursor AI tracking SQLite DB. Default: ~/.cursor/ai-tracking/ai-code-tracking.db")
    parser.add_argument("--cursor-state-db", default=str(DEFAULT_CURSOR_STATE_DB), help="Cursor global state SQLite DB for conversation bubbles and legacy daily AI-code stats.")
    parser.add_argument("--cursor-projects-home", default=str(DEFAULT_CURSOR_PROJECTS_HOME), help="Cursor projects folder with Agent transcripts. Default: ~/.cursor/projects")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Report output directory.")
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE), help="State file used to dedupe WakaTime heartbeats.")
    parser.add_argument("--days", type=int, default=None, help="Only include threads active in the last N days.")
    parser.add_argument("--since", default=None, help="Only include threads active on or after YYYY-MM-DD or ISO datetime.")
    parser.add_argument("--until", default=None, help="Only include threads active on or before YYYY-MM-DD or ISO datetime.")
    parser.add_argument("--timezone", default=None, help="IANA timezone for date grouping, for example Asia/Colombo.")
    parser.add_argument("--redact", action="store_true", help="Hide thread titles, local folders, and log paths in reports.")
    parser.add_argument("--hash-projects", action="store_true", help="Replace project names with stable anonymous labels.")
    parser.add_argument("--daily-token-budget", type=parse_positive_float, default=0.0, help="Optional daily token budget warning threshold.")
    parser.add_argument("--daily-usd-budget", type=parse_positive_float, default=0.0, help="Optional daily estimated USD budget warning threshold.")
    parser.add_argument("--monthly-token-budget", type=parse_positive_float, default=0.0, help="Optional month-to-date token budget warning threshold.")
    parser.add_argument("--monthly-usd-budget", type=parse_positive_float, default=0.0, help="Optional month-to-date estimated USD budget warning threshold.")

    subparsers = parser.add_subparsers(dest="command")

    def add_table_args(subparser: argparse.ArgumentParser, default_limit: int = 20) -> None:
        subparser.add_argument("--format", choices=["table", "json", "csv"], default="table")
        subparser.add_argument("--limit", type=int, default=default_limit, help="Maximum rows to print. Use 0 for all rows.")
        subparser.add_argument("--compact", action="store_true", help="Trim wide table cells for terminal use.")

    report = subparsers.add_parser("report", help="Generate JSON, CSV, and HTML reports.")
    report.set_defaults(func=command_report)

    demo = subparsers.add_parser("demo", help="Generate reports from bundled synthetic data.")
    demo.set_defaults(func=command_demo)

    gui = subparsers.add_parser("gui", help="Open a live native desktop dashboard.")
    gui.add_argument("--refresh-seconds", type=parse_refresh_seconds, default=10, help="Auto-refresh interval. Minimum: 2 seconds.")
    gui.set_defaults(func=command_gui)

    serve = subparsers.add_parser("serve", help="Serve a live local web dashboard on 127.0.0.1.")
    serve.add_argument("--host", default="127.0.0.1", help="Bind host. Default: 127.0.0.1.")
    serve.add_argument("--port", type=int, default=8765, help="Bind port. Use 0 for a random free port.")
    serve.add_argument("--refresh-seconds", type=parse_refresh_seconds, default=10, help="Browser auto-refresh interval. Minimum: 2 seconds.")
    serve.add_argument("--no-open", action="store_true", help="Do not open the dashboard in the default browser.")
    serve.set_defaults(func=command_serve)

    doctor = subparsers.add_parser("doctor", help="Check Codex logs, parser readiness, WakaTime, and output paths.")
    doctor.set_defaults(func=command_doctor)

    daily = subparsers.add_parser("daily", help="Print daily usage totals.")
    add_table_args(daily, default_limit=31)
    daily.set_defaults(func=command_daily)

    weekly = subparsers.add_parser("weekly", help="Print weekly usage totals.")
    add_table_args(weekly, default_limit=12)
    weekly.set_defaults(func=command_weekly)

    monthly = subparsers.add_parser("monthly", help="Print monthly usage totals.")
    add_table_args(monthly, default_limit=12)
    monthly.set_defaults(func=command_monthly)

    session = subparsers.add_parser("session", help="Print most expensive sessions/threads.")
    add_table_args(session, default_limit=20)
    session.set_defaults(func=command_session)

    project = subparsers.add_parser("project", help="Print project usage totals.")
    add_table_args(project, default_limit=20)
    project.set_defaults(func=command_project)

    model = subparsers.add_parser("model", help="Print model usage totals.")
    add_table_args(model, default_limit=20)
    model.set_defaults(func=command_model)

    source = subparsers.add_parser("source", help="Print app/source usage totals.")
    add_table_args(source, default_limit=20)
    source.set_defaults(func=command_source)

    source_audit = subparsers.add_parser("source-audit", help="Audit every local and API source that can provide usage, cost, or time data.")
    source_audit.add_argument("--format", choices=["table", "json", "markdown"], default="table")
    source_audit.add_argument("--compact", action="store_true", help="Trim wide table cells for terminal use.")
    source_audit.set_defaults(func=command_source_audit)

    billing = subparsers.add_parser("billing", help="Check optional official billing connectors.")
    billing.add_argument("--format", choices=["table", "json"], default="table")
    billing.add_argument("--days", type=int, default=30, help="Billing lookback window for providers that support date ranges.")
    billing.add_argument("--timeout-seconds", type=int, default=20, help="HTTP timeout when --fetch is used.")
    billing.add_argument("--fetch", action="store_true", help="Call configured provider APIs. Never prints configured keys.")
    billing.add_argument("--limit", type=int, default=20, help=argparse.SUPPRESS)
    billing.add_argument("--compact", action="store_true", help="Trim wide table cells for terminal use.")
    billing.set_defaults(func=command_billing)

    sync = subparsers.add_parser("sync-wakatime", help="Send recent Codex app activity to WakaTime.")
    sync.add_argument("--wakatime-since-minutes", type=int, default=180)
    sync.add_argument("--wakatime-interval-minutes", type=int, default=10)
    sync.add_argument("--wakatime-max-heartbeats", type=int, default=50)
    sync.add_argument("--dry-run", action="store_true")
    sync.set_defaults(func=command_sync_wakatime)

    run = subparsers.add_parser("run", help="Generate reports and optionally sync WakaTime.")
    run.add_argument("--sync-wakatime", action="store_true")
    run.add_argument("--wakatime-since-minutes", type=int, default=180)
    run.add_argument("--wakatime-interval-minutes", type=int, default=10)
    run.add_argument("--wakatime-max-heartbeats", type=int, default=50)
    run.add_argument("--dry-run", action="store_true")
    run.set_defaults(func=command_run)

    parser.set_defaults(func=command_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ValueError as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
