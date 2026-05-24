#!/usr/bin/env python3
"""
Local Codex app usage tracker.

Reads Codex desktop/app rollout logs from ~/.codex, estimates token cost/credits,
generates CSV/JSON/HTML reports, and can send conservative WakaTime "ai coding"
heartbeats for recent Codex app activity.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python without zoneinfo support.
    ZoneInfo = None  # type: ignore[assignment]


VERSION = "0.1.0"
TRACKER_DIR = Path(__file__).resolve().parent
DEFAULT_CODEX_HOME = Path.home() / ".codex"
DEFAULT_OUTPUT_DIR = Path.cwd() / "out"
DEFAULT_STATE_FILE = Path.home() / ".codex-usage-tracker" / "state.json"

USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)

# Rates captured from public OpenAI pages on 2026-05-23.
# Codex app usage is credit-based. API USD is only an equivalent estimate.
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


def normalize_usage(value: Any) -> dict[str, int]:
    usage = zero_usage()
    if not isinstance(value, dict):
        return usage
    for field in USAGE_FIELDS:
        raw = value.get(field, 0)
        if isinstance(raw, bool):
            raw = 0
        if isinstance(raw, (int, float)):
            usage[field] = max(0, int(raw))
    if usage["total_tokens"] == 0:
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
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
    input_total = int(usage.get("input_tokens", 0))
    uncached_input = max(0, input_total - cached)
    output = int(usage.get("output_tokens", 0))
    return (
        (uncached_input / 1_000_000.0) * rate["input"]
        + (cached / 1_000_000.0) * rate["cached_input"]
        + (output / 1_000_000.0) * rate["output"]
    )


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
        "source": source,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "cli_version": cli_version,
        "path": str(path),
        "line_count": line_count,
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
    daily: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "date": "",
        "usage": zero_usage(),
        "estimated_codex_credits": 0.0,
        "estimated_api_usd_equiv": 0.0,
        "active_seconds": 0,
        "threads": set(),
    })
    projects: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "project": "",
        "cwd": "",
        "usage": zero_usage(),
        "estimated_codex_credits": 0.0,
        "estimated_api_usd_equiv": 0.0,
        "active_seconds": 0,
        "thread_count": 0,
    })
    models: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "model": "",
        "usage": zero_usage(),
        "estimated_codex_credits": 0.0,
        "estimated_api_usd_equiv": 0.0,
        "active_seconds": 0,
        "thread_count": 0,
    })

    for thread in threads:
        usage = thread["usage"]
        model = thread.get("model") or "(unknown)"
        project = thread.get("project") or "(unknown)"
        cwd = thread.get("cwd") or ""
        credits = thread.get("estimated_codex_credits") or 0.0
        usd = thread.get("estimated_api_usd_equiv") or 0.0
        active_seconds = int(thread.get("active_seconds") or 0)

        add_usage(total_usage, usage)
        total_credits += credits
        total_usd += usd
        total_active_seconds += active_seconds

        project_row = projects[cwd or project]
        project_row["project"] = project
        project_row["cwd"] = cwd
        add_usage(project_row["usage"], usage)
        project_row["estimated_codex_credits"] += credits
        project_row["estimated_api_usd_equiv"] += usd
        project_row["active_seconds"] += active_seconds
        project_row["thread_count"] += 1

        model_row = models[model]
        model_row["model"] = model
        add_usage(model_row["usage"], usage)
        model_row["estimated_codex_credits"] += credits
        model_row["estimated_api_usd_equiv"] += usd
        model_row["active_seconds"] += active_seconds
        model_row["thread_count"] += 1

        for day, day_usage in thread.get("daily_usage", {}).items():
            day_row = daily[day]
            day_row["date"] = day
            add_usage(day_row["usage"], day_usage)
            day_row["estimated_codex_credits"] += estimate_amount(day_usage, model, "codex_credits") or 0.0
            day_row["estimated_api_usd_equiv"] += estimate_amount(day_usage, model, "api_usd_standard_short") or 0.0
            day_row["threads"].add(thread["thread_id"])

        for day, seconds in thread.get("active_daily", {}).items():
            daily[day]["date"] = day
            daily[day]["active_seconds"] += int(seconds)

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
        "daily": sorted(daily_rows, key=lambda item: item["date"]),
        "projects": sorted(projects.values(), key=lambda item: usage_total(item["usage"]), reverse=True),
        "models": sorted(models.values(), key=lambda item: usage_total(item["usage"]), reverse=True),
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
        "path": thread.get("path", ""),
    }


def flatten_thread_for_cli(thread: dict[str, Any], report_tz: Any = None) -> dict[str, Any]:
    usage = thread["usage"]
    return {
        "thread_id": str(thread.get("thread_id") or "")[:12],
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
    ) or empty_row(10)

    project_html = "\n".join(
        tr([
            html.escape(row["project"]),
            html.escape(row["cwd"]),
            number(row["thread_count"]),
            number(usage_total(row["usage"])),
            percent(cache_hit_rate(row["usage"])),
            number(row["estimated_codex_credits"]),
            f"${number(row['estimated_api_usd_equiv'])}",
            minutes(row["active_seconds"]),
        ])
        for row in project_rows
    ) or empty_row(8)

    model_html = "\n".join(
        tr([
            html.escape(row["model"]),
            number(row["thread_count"]),
            number(usage_total(row["usage"])),
            number(row["usage"]["cached_input_tokens"]),
            number(row["usage"]["output_tokens"]),
            percent(output_ratio(row["usage"])),
            number(row["estimated_codex_credits"]),
            f"${number(row['estimated_api_usd_equiv'])}",
        ])
        for row in model_rows
    ) or empty_row(8)

    thread_html = "\n".join(
        tr([
            html.escape(thread.get("title") or "(untitled)"),
            html.escape(thread.get("project") or ""),
            html.escape(thread.get("model") or ""),
            html.escape(fmt_dt(thread.get("ended_at"), report_tz)),
            number(usage_total(thread["usage"])),
            percent(cache_hit_rate(thread["usage"])),
            number(thread.get("estimated_codex_credits") or 0.0),
            f"${number(thread.get('estimated_api_usd_equiv') or 0.0)}",
            minutes(thread.get("active_seconds") or 0),
        ])
        for thread in recent_threads
    ) or empty_row(9)

    generated_at = fmt_dt(summary["generated_at"], report_tz)
    total_tokens = usage_total(summary["usage"])
    top_project = summary["projects"][0]["project"] if summary["projects"] else "(none)"
    top_model = summary["models"][0]["model"] if summary["models"] else "(none)"
    repo_url = "https://github.com/SuvenSeo/codex-usage-tracker"
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Codex App Usage Dashboard</title>
  <style>
    :root {{
      --bg: #f5f6f8;
      --panel: #ffffff;
      --ink: #151a22;
      --muted: #667085;
      --subtle: #eef1f4;
      --border: #d7dce2;
      --blue: #1f6feb;
      --green: #1a7f64;
      --amber: #b7791f;
      --rose: #b42318;
      --code: #24292f;
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
      color: #344054;
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
    th {{ background: var(--subtle); color: #344054; font-weight: 650; position: sticky; top: 0; }}
    tr:last-child td {{ border-bottom: 0; }}
    .bar {{ width: 140px; height: 8px; background: #e4e7ec; border-radius: 999px; overflow: hidden; margin-top: 5px; }}
    .bar span {{ display: block; height: 100%; background: var(--blue); }}
    .empty {{ color: var(--muted); text-align: center; padding: 18px; }}
    .note {{
      margin-top: 24px;
      padding: 14px 16px;
      border-left: 4px solid var(--amber);
      background: #fff8e6;
      color: #58430d;
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
      <span class="pill">WakaTime ready</span>
      <span class="pill">Codex app logs</span>
    </div>
    <h1>Codex App Usage Dashboard</h1>
    <p>Generated {html.escape(generated_at)} from local Codex app logs. Estimates help with project-level usage, not official billing.</p>
    <section class="metrics" aria-label="Summary metrics">
      <div class="metric"><span>Threads</span><strong>{number(summary["thread_count"])}</strong></div>
      <div class="metric"><span>Total tokens</span><strong>{number(total_tokens)}</strong></div>
      <div class="metric"><span>Estimated Codex credits</span><strong>{number(summary["estimated_codex_credits"])}</strong></div>
      <div class="metric"><span>API-equivalent USD</span><strong>${number(summary["estimated_api_usd_equiv"])}</strong></div>
      <div class="metric"><span>Estimated active time</span><strong>{minutes(summary["active_seconds"])} min</strong></div>
    </section>
    <section class="insights" aria-label="Usage insights">
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
      <h2>Daily Usage</h2>
      <div class="panel">
        <table data-filterable>
          <thead><tr><th>Date</th><th>Token volume</th><th>Total</th><th>Input</th><th>Cached input</th><th>Output</th><th>Cache hit</th><th>Credits</th><th>API USD</th><th>Active min</th></tr></thead>
          <tbody>{daily_html}</tbody>
        </table>
      </div>
    </section>
    <section>
      <h2>Projects</h2>
      <div class="panel">
        <table data-filterable>
          <thead><tr><th>Project</th><th>Folder</th><th>Threads</th><th>Tokens</th><th>Cache hit</th><th>Credits</th><th>API USD</th><th>Active min</th></tr></thead>
          <tbody>{project_html}</tbody>
        </table>
      </div>
    </section>
    <section>
      <h2>Models</h2>
      <div class="panel">
        <table data-filterable>
          <thead><tr><th>Model</th><th>Threads</th><th>Tokens</th><th>Cached input</th><th>Output</th><th>Output share</th><th>Credits</th><th>API USD</th></tr></thead>
          <tbody>{model_html}</tbody>
        </table>
      </div>
    </section>
    <section>
      <h2>Most Expensive Threads</h2>
      <div class="panel">
        <table data-filterable>
          <thead><tr><th>Thread</th><th>Project</th><th>Model</th><th>Last activity</th><th>Tokens</th><th>Cache hit</th><th>Credits</th><th>API USD</th><th>Active min</th></tr></thead>
          <tbody>{thread_html}</tbody>
        </table>
      </div>
    </section>
    <div class="note">
      Credit estimates use OpenAI's Codex token-based rate card. API USD is an API-pricing equivalent, not your authoritative Codex invoice.
      Exact billing should be checked in Codex Settings &gt; Usage or OpenAI billing.
    </div>
  </main>
  <footer>
    Built by <a href="{repo_url}">SuvenSeo</a> for developers who want local visibility into Codex usage.
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
        "projects_csv": output_dir / "projects.csv",
        "models_csv": output_dir / "models.csv",
        "dashboard_html": output_dir / "dashboard.html",
    }

    write_json(paths["summary_json"], {"summary": serial_summary, "threads": serial_threads})

    thread_rows = [flatten_thread_for_csv(thread, report_tz=report_tz) for thread in threads]
    write_csv(paths["threads_csv"], thread_rows, [
        "thread_id", "title", "project", "cwd", "model", "reasoning_effort",
        "started_at", "ended_at", "active_minutes_est", "input_tokens",
        "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
        "total_tokens", "estimated_codex_credits", "estimated_api_usd_equiv", "path",
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

    project_rows = [
        flatten_group_for_csv(row, {"project": row["project"], "cwd": row["cwd"]})
        for row in summary["projects"]
    ]
    write_csv(paths["projects_csv"], project_rows, [
        "project", "cwd", "thread_count", "active_minutes_est", "input_tokens",
        "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
        "total_tokens", "estimated_codex_credits", "estimated_api_usd_equiv",
    ])

    model_rows = [
        flatten_group_for_csv(row, {"model": row["model"]})
        for row in summary["models"]
    ]
    write_csv(paths["models_csv"], model_rows, [
        "model", "thread_count", "active_minutes_est", "input_tokens",
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
    codex_home = Path(args.codex_home).expanduser()
    threads = load_threads(codex_home, days=args.days, report_tz=report_tz)
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
        "source": "demo",
        "model": model,
        "reasoning_effort": "medium",
        "cli_version": VERSION,
        "path": f"demo/{thread_id}.jsonl",
        "line_count": 24,
        "started_at": started_at,
        "ended_at": timestamps[-1],
        "usage": usage,
        "daily_usage": daily_usage,
        "event_timestamps": timestamps,
        "active_seconds": active_seconds,
        "active_daily": active_daily,
        "tool_counts": {"shell_command": 3, "apply_patch": 2},
        "estimated_codex_credits": estimate_amount(usage, model, "codex_credits"),
        "estimated_api_usd_equiv": estimate_amount(usage, model, "api_usd_standard_short"),
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
    ]


def print_report_summary(summary: dict[str, Any], paths: dict[str, Path]) -> None:
    print(f"threads={summary['thread_count']}")
    print(f"tokens={usage_total(summary['usage'])}")
    print(f"estimated_codex_credits={summary['estimated_codex_credits']:.2f}")
    print(f"estimated_api_usd_equiv={summary['estimated_api_usd_equiv']:.2f}")
    print(f"active_minutes_est={summary['active_seconds'] / 60.0:.1f}")
    for name, path in paths.items():
        print(f"{name}={path}")


def command_report(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).expanduser()
    threads, summary, report_tz = load_report_data(args)
    paths = write_reports(threads, summary, output_dir, report_tz=report_tz)
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
        ("api_usd", "API USD"),
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
        ("title", "Title"),
        ("project", "Project"),
        ("model", "Model"),
        ("ended_at", "Last activity"),
        ("active_min", "Active min"),
        ("tokens", "Tokens"),
        ("cache_hit", "Cache hit"),
        ("credits", "Credits"),
        ("api_usd", "API USD"),
    ], args)


def command_project(args: argparse.Namespace) -> int:
    _, summary, _ = load_report_data(args)
    rows = [
        flatten_summary_row_for_cli(row, {"project": row["project"]})
        for row in summary["projects"]
    ]
    return emit_rows(rows, [
        ("project", "Project"),
        ("threads", "Threads"),
        ("active_min", "Active min"),
        ("tokens", "Tokens"),
        ("input", "Input"),
        ("cached", "Cached"),
        ("output", "Output"),
        ("cache_hit", "Cache hit"),
        ("credits", "Credits"),
        ("api_usd", "API USD"),
    ], args)


def command_model(args: argparse.Namespace) -> int:
    _, summary, _ = load_report_data(args)
    rows = [
        flatten_summary_row_for_cli(row, {"model": row["model"]})
        for row in summary["models"]
    ]
    return emit_rows(rows, [
        ("model", "Model"),
        ("threads", "Threads"),
        ("active_min", "Active min"),
        ("tokens", "Tokens"),
        ("input", "Input"),
        ("cached", "Cached"),
        ("output", "Output"),
        ("cache_hit", "Cache hit"),
        ("credits", "Credits"),
        ("api_usd", "API USD"),
    ], args)


def command_doctor(args: argparse.Namespace) -> int:
    report_tz = resolve_timezone(getattr(args, "timezone", None))
    codex_home = Path(args.codex_home).expanduser()
    output_dir = Path(args.output_dir).expanduser()
    redact = bool(getattr(args, "redact", False))
    failures = 0

    def line(status: str, label: str, detail: str = "") -> None:
        print(f"[{status}] {label}" + (f" - {detail}" if detail else ""))

    line("OK", "Python", sys.version.split()[0])
    line("OK" if codex_home.exists() else "FAIL", "Codex home", "(redacted)" if redact else str(codex_home))
    if not codex_home.exists():
        failures += 1

    rollout_files = iter_rollout_files(codex_home) if codex_home.exists() else []
    if rollout_files:
        line("OK", "Rollout logs", f"{len(rollout_files)} files")
    else:
        line("FAIL", "Rollout logs", "no rollout-*.jsonl files found")
        failures += 1

    threads = load_threads(codex_home, days=args.days, report_tz=report_tz) if rollout_files else []
    threads = filter_threads_by_date(
        threads,
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
        report_tz=report_tz,
    )
    if threads:
        line("OK", "Parser", f"{len(threads)} threads parsed")
    else:
        line("FAIL", "Parser", "no threads parsed in the selected range")
        failures += 1

    unknown_models = sorted({thread.get("model") for thread in threads if thread.get("model") and not rates_for_model(thread.get("model"))})
    if unknown_models:
        line("WARN", "Pricing table", "unknown models: " + ", ".join(unknown_models[:5]))
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Track Codex app token usage, estimated cost, and WakaTime activity.")
    parser.add_argument("--codex-home", default=str(DEFAULT_CODEX_HOME), help="Codex data folder. Default: ~/.codex")
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
