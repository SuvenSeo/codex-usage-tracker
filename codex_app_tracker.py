#!/usr/bin/env python3
"""
Local AI coding usage tracker.

Reads Codex desktop/app rollout logs, Claude Code transcripts, and Cursor AI
tracking metadata, generates CSV/JSON/HTML reports, and can send conservative
WakaTime "ai coding" heartbeats for recent Codex app activity.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python without zoneinfo support.
    ZoneInfo = None  # type: ignore[assignment]


VERSION = "0.2.0"
TRACKER_DIR = Path(__file__).resolve().parent
DEFAULT_CODEX_HOME = Path.home() / ".codex"
DEFAULT_CLAUDE_HOME = Path.home() / ".claude"
DEFAULT_CURSOR_AI_DB = Path.home() / ".cursor" / "ai-tracking" / "ai-code-tracking.db"
DEFAULT_CURSOR_STATE_DB = (
    Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    / "Cursor" / "User" / "globalStorage" / "state.vscdb"
)
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
    "bg": "#101114",
    "panel": "#181b20",
    "panel_alt": "#20242b",
    "field": "#111418",
    "ink": "#f5f7fa",
    "muted": "#a8b0bd",
    "subtle": "#252b33",
    "border": "#333a45",
    "blue": "#58a6ff",
    "green": "#36c58c",
    "amber": "#f1b84b",
    "rose": "#ff7b72",
    "selected": "#2f81f7",
}
GUI_CHART_COLORS = {
    "sources": "#a78bfa",
    "daily": DARK_THEME["blue"],
    "projects": DARK_THEME["green"],
    "models": DARK_THEME["amber"],
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
        "caveat": "Codex credits and Claude USD are estimated from local token logs and official token rates. Cursor activity comes from local app data, but official billing, remaining credits, fast mode uplifts, taxes, and plan exceptions must be checked with the vendor.",
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


def load_cursor_threads(cursor_db: Path, days: int | None = None, report_tz: Any = None) -> list[dict[str, Any]]:
    if not cursor_db.exists():
        return []

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
            return []

        rows = con.execute(
            """
            select conversationId, requestId, timestamp, source, fileExtension, model
            from ai_code_hashes
            order by timestamp
            """
        )
        for conversation_id, request_id, timestamp, source, file_extension, model in rows:
            conversation_id = str(conversation_id or request_id or f"cursor-{timestamp}")
            group = groups.setdefault(conversation_id, {
                "thread_id": f"cursor-{conversation_id}",
                "conversation_id": conversation_id,
                "timestamps": [],
                "requests": set(),
                "model_counts": Counter(),
                "source_counts": Counter(),
                "extension_counts": Counter(),
                "line_count": 0,
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
            group["line_count"] += 1
        con.close()
    except Exception as exc:
        print(f"warning: could not read {cursor_db}: {exc}", file=sys.stderr)
        return []

    threads: list[dict[str, Any]] = []
    for group in groups.values():
        timestamps = list(group["timestamps"])
        if not timestamps:
            continue
        started_at = min(timestamps)
        ended_at = max(timestamps)
        if cutoff and ended_at < cutoff:
            continue
        model_counts: Counter[str] = group["model_counts"]
        source_counts: Counter[str] = group["source_counts"]
        active_seconds, active_daily = estimate_active_seconds(timestamps, report_tz=report_tz)
        conversation_id = group["conversation_id"]
        model = model_counts.most_common(1)[0][0] if model_counts else ""
        source = source_counts.most_common(1)[0][0] if source_counts else "cursor"
        request_count = len(group["requests"]) or group["line_count"]
        threads.append({
            "thread_id": group["thread_id"],
            "title": summaries.get(conversation_id) or f"Cursor AI edits {conversation_id[:8]}",
            "cwd": "",
            "project": "Cursor AI edits",
            "app": "cursor",
            "source": f"cursor_{source}",
            "model": model,
            "reasoning_effort": "",
            "cli_version": "",
            "path": str(cursor_db),
            "line_count": group["line_count"],
            "event_count": group["line_count"],
            "request_count": request_count,
            "started_at": started_at,
            "ended_at": ended_at,
            "usage": zero_usage(),
            "daily_usage": {},
            "event_timestamps": timestamps,
            "active_seconds": active_seconds,
            "active_daily": active_daily,
            "tool_counts": {
                "ai_code_events": group["line_count"],
                "requests": request_count,
            },
            "estimated_codex_credits": 0.0,
            "estimated_api_usd_equiv": 0.0,
        })

    return sorted(threads, key=lambda item: item.get("ended_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


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
        threads.extend(load_cursor_threads(Path(args.cursor_db).expanduser(), days=args.days, report_tz=report_tz))
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

    return {
        "generated_at": fmt_dt(summary["generated_at"], report_tz),
        "latest_activity": fmt_dt(latest_activity, report_tz) if latest_activity else "(none)",
        "activity_status": "Active" if is_active else "Inactive",
        "token_delta": "" if token_delta is None else f"{token_delta:+,}",
        "total_tokens": total_tokens,
        "pricing": summary.get("pricing") or pricing_metadata(),
        "metrics": [
            ("Threads", number(summary["thread_count"])),
            ("Apps", number(len(summary.get("sources", [])))),
            ("Total tokens", number(total_tokens)),
            ("Estimated Codex credits", number(summary["estimated_codex_credits"])),
            ("Estimated USD", f"${number(summary['estimated_api_usd_equiv'])}"),
            ("Estimated active time", f"{minutes(summary['active_seconds'])} min"),
            ("Events", number(summary.get("event_count", 0))),
            ("Requests", number(summary.get("request_count", 0))),
            ("Top app", top_app),
            ("Top project", top_project),
            ("Top model", top_model),
            ("Cache hit rate", percent(cache_hit_rate(summary["usage"]))),
            ("Output share", percent(output_ratio(summary["usage"]))),
        ],
        "charts": {
            "sources": [
                {"label": row["app"], "value": usage_total(row["usage"]) or int(row.get("event_count") or 0), "detail": f"{row['thread_count']} threads"}
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
    repo_url = "https://github.com/SuvenSeo/codex-usage-tracker"
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
    body {{
      margin: 0;
      font-family: Segoe UI, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg);
      color: var(--ink);
      line-height: 1.45;
    }}
    header, main, footer {{ max-width: 1280px; margin: 0 auto; padding: 24px; }}
    header {{ padding-top: 34px; }}
    .eyebrow {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 14px;
    }}
    .pill {{
      border: 1px solid var(--border);
      background: var(--panel);
      border-radius: 999px;
      color: var(--muted);
      font-size: 12px;
      padding: 5px 10px;
    }}
    h1 {{ margin: 0; font-size: 32px; letter-spacing: 0; }}
    h2 {{ margin: 28px 0 12px; font-size: 18px; letter-spacing: 0; }}
    p {{ color: var(--muted); margin: 6px 0 0; max-width: 760px; }}
    .metrics, .insights {{
      display: grid;
      gap: 12px;
      margin-top: 20px;
    }}
    .metrics {{ grid-template-columns: repeat(5, minmax(0, 1fr)); }}
    .insights {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
    .metric, .insight {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
      min-height: 92px;
    }}
    .metric span, .insight span {{ display: block; color: var(--muted); font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 8px; font-size: 22px; }}
    .insight strong {{ display: block; margin-top: 8px; font-size: 17px; overflow-wrap: anywhere; }}
    .metric:nth-child(2) strong {{ color: var(--blue); }}
    .metric:nth-child(3) strong {{ color: var(--green); }}
    .metric:nth-child(4) strong {{ color: var(--amber); }}
    .metric:nth-child(5) strong {{ color: var(--rose); }}
    .toolbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin: 24px 0 8px;
    }}
    .search {{
      width: min(420px, 100%);
      border: 1px solid var(--border);
      border-radius: 7px;
      background: var(--panel);
      color: var(--ink);
      font: inherit;
      padding: 10px 12px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: auto;
    }}
    table {{ border-collapse: collapse; width: 100%; min-width: 900px; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; font-size: 13px; }}
    th {{ background: var(--subtle); color: var(--ink); font-weight: 650; position: sticky; top: 0; }}
    tr:last-child td {{ border-bottom: 0; }}
    .bar {{ width: 140px; height: 8px; background: var(--subtle); border-radius: 999px; overflow: hidden; margin-top: 5px; }}
    .bar span {{ display: block; height: 100%; background: var(--blue); }}
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
    @media (max-width: 980px) {{
      header, main, footer {{ padding: 16px; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .insights {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .toolbar {{ align-items: flex-start; flex-direction: column; }}
    }}
    @media (max-width: 560px) {{
      .metrics, .insights {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 26px; }}
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
      <span class="pill">Cursor activity</span>
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
    <div class="toolbar">
      <h2>Usage Tables</h2>
      <input class="search" id="table-search" type="search" placeholder="Filter tables by project, model, title, or date">
    </div>
    <section>
      <h2>Apps</h2>
      <div class="panel">
        <table data-filterable>
          <thead><tr><th>App</th><th>Threads</th><th>Requests</th><th>Events</th><th>Tokens</th><th>Input</th><th>Cached input</th><th>Output</th><th>Credits</th><th>USD</th><th>Active min</th></tr></thead>
          <tbody>{source_html}</tbody>
        </table>
      </div>
    </section>
    <section>
      <h2>Daily Usage</h2>
      <div class="panel">
        <table data-filterable>
          <thead><tr><th>Date</th><th>Token volume</th><th>Total</th><th>Input</th><th>Cached input</th><th>Output</th><th>Cache hit</th><th>Credits</th><th>USD</th><th>Active min</th></tr></thead>
          <tbody>{daily_html}</tbody>
        </table>
      </div>
    </section>
    <section>
      <h2>Projects</h2>
      <div class="panel">
        <table data-filterable>
          <thead><tr><th>App</th><th>Project</th><th>Folder</th><th>Threads</th><th>Requests</th><th>Events</th><th>Tokens</th><th>Cache hit</th><th>Credits</th><th>USD</th><th>Active min</th></tr></thead>
          <tbody>{project_html}</tbody>
        </table>
      </div>
    </section>
    <section>
      <h2>Models</h2>
      <div class="panel">
        <table data-filterable>
          <thead><tr><th>App</th><th>Model</th><th>Threads</th><th>Requests</th><th>Tokens</th><th>Cached input</th><th>Output</th><th>Output share</th><th>Credits</th><th>USD</th></tr></thead>
          <tbody>{model_html}</tbody>
        </table>
      </div>
    </section>
    <section>
      <h2>Most Expensive Threads</h2>
      <div class="panel">
        <table data-filterable>
          <thead><tr><th>App</th><th>Thread</th><th>Project</th><th>Model</th><th>Last activity</th><th>Requests</th><th>Events</th><th>Tokens</th><th>Cache hit</th><th>Credits</th><th>USD</th><th>Active min</th></tr></thead>
          <tbody>{thread_html}</tbody>
        </table>
      </div>
    </section>
    <div class="note">
      Credit estimates use OpenAI's Codex token-based rate card for Codex records only, and Claude USD uses Anthropic token pricing where known local model rates exist. Cursor activity is local activity/time only, not authoritative token or billing data.
    </div>
  </main>
  <footer>
    Built by <a href="{repo_url}">SuvenSeo</a> for developers who want local visibility into AI coding usage.
  </footer>
  <script>
    const search = document.getElementById("table-search");
    const rows = Array.from(document.querySelectorAll("table[data-filterable] tbody tr"));
    search.addEventListener("input", () => {{
      const query = search.value.trim().toLowerCase();
      rows.forEach((row) => {{
        row.hidden = query && !row.textContent.toLowerCase().includes(query);
      }});
    }});
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

    cursor_threads = load_cursor_threads(cursor_db, report_tz=report_tz) if cursor_db.exists() else []
    cursor_totals = audit_totals_from_threads(cursor_threads)
    add({
        "source": "Cursor AI tracking DB",
        "status": "activity only" if cursor_threads else "missing local DB",
        "records": f"{cursor_totals['threads']} parsed conversations",
        "date_range": thread_range_text(cursor_threads, report_tz),
        "exact": "AI edit activity rows, requests, models, and active-time estimate",
        "estimated": "none",
        "blocked": "exact tokens, credits, and spend are not exposed in this local DB",
        "local_path": source_path(cursor_db, redact),
        "docs_url": CURSOR_PRICING_URL,
        **cursor_totals,
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
            else estimate_amount(usage, model, "anthropic_usd") if app == "claude" else 0.0
        ),
    }


def demo_threads(report_tz: Any = None) -> list[dict[str, Any]]:
    base = datetime(2026, 5, 24, 8, 30, tzinfo=timezone.utc)
    return [
        demo_thread(
            "demo-usage-dashboard",
            "Build usage dashboard and README launch copy",
            "C:\\Projects\\codex-usage-tracker",
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
            "C:\\Projects\\codex-usage-tracker",
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
            "C:\\Projects\\codex-usage-tracker",
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
            "Cursor AI edit activity",
            "Cursor AI edits",
            "composer-2.5",
            base + timedelta(days=1, hours=3),
            28,
            zero_usage(),
            report_tz=report_tz,
            app="cursor",
            source="demo_cursor_ai_tracking",
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
        line("OK" if cursor_db.exists() else "FAIL", "Cursor AI tracking DB", "(redacted)" if redact else str(cursor_db))
        if not cursor_db.exists():
            failures += 1
        cursor_threads = load_cursor_threads(cursor_db, days=args.days, report_tz=report_tz) if cursor_db.exists() else []
        cursor_threads = filter_threads_by_date(
            cursor_threads,
            since=getattr(args, "since", None),
            until=getattr(args, "until", None),
            report_tz=report_tz,
        )
        if cursor_threads:
            line("OK", "Cursor parser", f"{len(cursor_threads)} conversations parsed")
            line("WARN", "Cursor token totals", "local Cursor DB exposes AI edit activity, not exact token usage")
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

    configure(".", background=theme["bg"], foreground=theme["ink"])
    configure("TFrame", background=theme["bg"])
    configure("TLabel", background=theme["bg"], foreground=theme["ink"])
    configure("TButton", background=theme["subtle"], foreground=theme["ink"], borderwidth=1)
    style_map(
        "TButton",
        background=[("pressed", theme["border"]), ("active", theme["panel_alt"])],
        foreground=[("disabled", theme["muted"])],
    )
    configure("TCheckbutton", background=theme["bg"], foreground=theme["ink"])
    style_map(
        "TCheckbutton",
        background=[("active", theme["bg"])],
        foreground=[("disabled", theme["muted"])],
    )
    configure("TEntry", fieldbackground=theme["field"], foreground=theme["ink"])
    configure("TNotebook", background=theme["bg"], borderwidth=0)
    configure("TNotebook.Tab", background=theme["subtle"], foreground=theme["muted"], padding=(12, 6))
    style_map(
        "TNotebook.Tab",
        background=[("selected", theme["panel"]), ("active", theme["panel_alt"])],
        foreground=[("selected", theme["ink"]), ("active", theme["ink"])],
    )
    configure("TLabelframe", background=theme["bg"], foreground=theme["ink"], bordercolor=theme["border"])
    configure("TLabelframe.Label", background=theme["bg"], foreground=theme["muted"])
    configure(
        "Treeview",
        background=theme["panel"],
        fieldbackground=theme["panel"],
        foreground=theme["ink"],
        bordercolor=theme["border"],
        rowheight=24,
    )
    configure("Treeview.Heading", background=theme["subtle"], foreground=theme["ink"], bordercolor=theme["border"])
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

        self.root = tk.Tk()
        self.root.title("AI Coding Usage Tracker")
        self.root.geometry("1180x780")
        self.root.minsize(980, 640)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.status_var = tk.StringVar(value="Loading selected local AI coding usage...")
        self.header_var = tk.StringVar(value="Last refresh: pending | Latest activity: pending | Status: pending | Token delta: pending")
        self.pricing_var = tk.StringVar(value="Pricing: pending")
        self.search_var = tk.StringVar(value="")
        self.auto_refresh_var = tk.BooleanVar(value=True)

        self.build_ui()
        self.search_var.trace_add("write", lambda *_: self.apply_filter())
        self.root.after(100, self.poll_events)
        self.root.after(200, self.start_refresh)
        self.root.after(self.refresh_seconds * 1000, self.auto_refresh_tick)

    def build_ui(self) -> None:
        ttk = self.ttk
        tk = self.tk

        try:
            style = ttk.Style()
            apply_dark_ttk_theme(self.root, style)
        except Exception:
            pass

        container = ttk.Frame(self.root, padding=16)
        container.pack(fill="both", expand=True)

        header = ttk.Frame(container)
        header.pack(fill="x")
        ttk.Label(
            header,
            text="AI Coding Usage Tracker",
            font=("Segoe UI", 18, "bold"),
            foreground=self.theme["ink"],
        ).pack(anchor="w")
        ttk.Label(header, textvariable=self.header_var, foreground=self.theme["muted"]).pack(anchor="w", pady=(4, 0))
        ttk.Label(header, textvariable=self.pricing_var, foreground=self.theme["muted"]).pack(anchor="w", pady=(4, 0))
        ttk.Label(header, textvariable=self.status_var, foreground=self.theme["green"]).pack(anchor="w", pady=(4, 0))

        controls = ttk.Frame(container)
        controls.pack(fill="x", pady=(14, 10))
        ttk.Button(controls, text="Refresh now", command=self.start_refresh).pack(side="left")
        ttk.Checkbutton(controls, text="Auto-refresh", variable=self.auto_refresh_var).pack(side="left", padx=(12, 18))
        ttk.Label(controls, text="Search").pack(side="left")
        ttk.Entry(controls, textvariable=self.search_var, width=36).pack(side="left", padx=(6, 18))
        ttk.Button(controls, text="Generate HTML report", command=self.start_report).pack(side="left")

        metrics = ttk.Frame(container)
        metrics.pack(fill="x", pady=(0, 12))
        metric_names = [
            "Threads",
            "Apps",
            "Total tokens",
            "Estimated Codex credits",
            "Estimated USD",
            "Estimated active time",
            "Events",
            "Requests",
            "Top app",
            "Top project",
            "Top model",
            "Cache hit rate",
            "Output share",
        ]
        for index, name in enumerate(metric_names):
            card = ttk.LabelFrame(metrics, text=name, padding=(10, 8))
            card.grid(row=index // 3, column=index % 3, sticky="ew", padx=4, pady=4)
            metrics.columnconfigure(index % 3, weight=1)
            value = tk.StringVar(value="--")
            self.metric_vars[name] = value
            ttk.Label(card, textvariable=value, font=("Segoe UI", 12, "bold"), wraplength=320).pack(anchor="w")

        notebook = ttk.Notebook(container)
        notebook.pack(fill="both", expand=True)

        overview = ttk.Frame(notebook, padding=10)
        notebook.add(overview, text="Overview")
        self.add_chart(overview, "sources", "Apps", GUI_CHART_COLORS["sources"])
        self.add_chart(overview, "daily", "Daily Tokens", GUI_CHART_COLORS["daily"])
        self.add_chart(overview, "projects", "Top Projects", GUI_CHART_COLORS["projects"])
        self.add_chart(overview, "models", "Top Models", GUI_CHART_COLORS["models"])

        table_specs = {
            "sources": "Apps",
            "daily": "Daily",
            "projects": "Projects",
            "models": "Models",
            "threads": "Threads",
        }
        for key, title in table_specs.items():
            frame = ttk.Frame(notebook, padding=8)
            notebook.add(frame, text=title)
            self.table_widgets[key] = self.make_table(frame)

    def add_chart(self, parent: Any, key: str, title: str, color: str) -> None:
        frame = self.ttk.LabelFrame(parent, text=title, padding=8)
        frame.pack(fill="both", expand=True, pady=(0, 8))
        canvas = self.tk.Canvas(
            frame,
            height=155,
            bg=self.theme["panel"],
            highlightthickness=1,
            highlightbackground=self.theme["border"],
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
            width = 230 if column_id in {"title", "folder"} else 120
            anchor = "w" if column_id in {"app", "title", "project", "folder", "model", "last_activity"} else "e"
            tree.column(column_id, width=width, minwidth=80, anchor=anchor, stretch=True)

    def start_refresh(self) -> None:
        if self.worker_running:
            self.status_var.set("Refresh already running...")
            return
        self.worker_running = True
        self.status_var.set("Refreshing from selected local AI coding data...")
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
        self.header_var.set(
            "Last refresh: "
            + model["generated_at"]
            + " | Latest activity: "
            + model["latest_activity"]
            + " | Status: "
            + model["activity_status"]
            + " | Token delta: "
            + (model["token_delta"] or "initial")
        )
        pricing = model.get("pricing") or pricing_metadata()
        self.pricing_var.set(
            "Pricing: Codex token-rate card verified "
            + str(pricing.get("source_date") or PRICING_SOURCE_DATE)
            + "; non-Codex billing remains vendor-authoritative."
        )
        for label, value in model["metrics"]:
            if label in self.metric_vars:
                self.metric_vars[label].set(value)

        for key, table in model["tables"].items():
            self.configure_table(key, table["columns"])
            self.table_data[key] = list(table["rows"])

        self.draw_chart(self.chart_canvases["sources"], model["charts"]["sources"])
        self.draw_chart(self.chart_canvases["daily"], model["charts"]["daily"])
        self.draw_chart(self.chart_canvases["projects"], model["charts"]["projects"])
        self.draw_chart(self.chart_canvases["models"], model["charts"]["models"])
        self.apply_filter()
        self.status_var.set("Live dashboard refreshed from selected local AI coding data.")

    def draw_chart(self, canvas: Any, rows: list[dict[str, Any]]) -> None:
        canvas.delete("all")
        width = max(int(canvas.winfo_width() or 0), 520)
        height = max(int(canvas.winfo_height() or 0), 140)
        if not rows:
            canvas.create_text(width / 2, height / 2, text="No data in this range.", fill=self.theme["muted"])
            return

        max_value = max([int(row["value"]) for row in rows] or [1])
        row_height = max(22, min(34, int((height - 18) / max(len(rows), 1))))
        label_width = 155
        value_width = 110
        bar_width = max(80, width - label_width - value_width - 34)
        color = getattr(canvas, "chart_color", "#1f6feb")

        for index, row in enumerate(rows):
            y = 12 + index * row_height
            label = str(row["label"])
            if len(label) > 24:
                label = label[:21] + "..."
            value = int(row["value"])
            filled = max(2, int((value / max_value) * bar_width))
            canvas.create_text(12, y + 8, text=label, anchor="w", fill=self.theme["muted"])
            canvas.create_rectangle(
                label_width,
                y + 2,
                label_width + bar_width,
                y + 14,
                fill=self.theme["subtle"],
                outline="",
            )
            canvas.create_rectangle(label_width, y + 2, label_width + filled, y + 14, fill=color, outline="")
            detail = str(row.get("detail") or "")
            value_text = number(value) + (f" | {detail}" if detail else "")
            canvas.create_text(label_width + bar_width + 12, y + 8, text=value_text, anchor="w", fill=self.theme["ink"])

    def apply_filter(self) -> None:
        query = self.search_var.get().strip().lower()
        for key, tree in self.table_widgets.items():
            for item in tree.get_children():
                tree.delete(item)
            for row in self.table_data.get(key, []):
                haystack = " ".join(str(value) for value in row).lower()
                if not query or query in haystack:
                    tree.insert("", "end", values=row)

    def close(self) -> None:
        self.closed = True
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
    parser.add_argument("--cursor-state-db", default=str(DEFAULT_CURSOR_STATE_DB), help="Cursor global state SQLite DB used for legacy daily AI-code stats.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Report output directory.")
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE), help="State file used to dedupe WakaTime heartbeats.")
    parser.add_argument("--days", type=int, default=None, help="Only include threads active in the last N days.")
    parser.add_argument("--since", default=None, help="Only include threads active on or after YYYY-MM-DD or ISO datetime.")
    parser.add_argument("--until", default=None, help="Only include threads active on or before YYYY-MM-DD or ISO datetime.")
    parser.add_argument("--timezone", default=None, help="IANA timezone for date grouping, for example Asia/Colombo.")
    parser.add_argument("--redact", action="store_true", help="Hide thread titles, local folders, and log paths in reports.")
    parser.add_argument("--hash-projects", action="store_true", help="Replace project names with stable anonymous labels.")

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
