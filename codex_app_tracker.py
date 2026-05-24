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


def fmt_dt(value: datetime | None) -> str:
    if not value:
        return ""
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def local_day(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d")


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


def estimate_active_seconds(timestamps: list[datetime], max_gap_seconds: int = 15 * 60) -> tuple[int, dict[str, int]]:
    unique = sorted(set(timestamps))
    if not unique:
        return 0, {}

    total = 60
    daily: dict[str, int] = defaultdict(int)
    daily[local_day(unique[0])] += 60
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
            daily[local_day(current)] += add
        previous = current

    return total, dict(daily)


def parse_rollout(path: Path, db_meta: dict[str, dict[str, Any]]) -> dict[str, Any]:
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
            add_usage(daily_usage[local_day(ts)], delta)
        previous_usage = current_usage
        latest_usage = current_usage

    if usage_total(latest_usage) == 0 and isinstance(meta.get("tokens_used"), int):
        latest_usage["total_tokens"] = max(0, int(meta["tokens_used"]))

    active_seconds, active_daily = estimate_active_seconds(timestamps)

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


def load_threads(codex_home: Path, days: int | None = None) -> list[dict[str, Any]]:
    db_meta = read_thread_db(codex_home)
    parsed: dict[str, dict[str, Any]] = {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None

    for path in iter_rollout_files(codex_home):
        try:
            thread = parse_rollout(path, db_meta)
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


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def flatten_thread_for_csv(thread: dict[str, Any]) -> dict[str, Any]:
    usage = thread["usage"]
    return {
        "thread_id": thread["thread_id"],
        "title": thread.get("title", ""),
        "project": thread.get("project", ""),
        "cwd": thread.get("cwd", ""),
        "model": thread.get("model", ""),
        "reasoning_effort": thread.get("reasoning_effort", ""),
        "started_at": fmt_dt(thread.get("started_at")),
        "ended_at": fmt_dt(thread.get("ended_at")),
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


def render_dashboard(output_path: Path, threads: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    recent_threads = sorted(threads, key=lambda item: usage_total(item["usage"]), reverse=True)[:25]
    daily_rows = summary["daily"][-45:]
    project_rows = summary["projects"][:20]
    model_rows = summary["models"]

    max_daily_tokens = max([usage_total(row["usage"]) for row in daily_rows] or [1])

    def tr(cells: list[str]) -> str:
        return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"

    daily_html = "\n".join(
        tr([
            html.escape(row["date"]),
            f"<div class=\"bar\"><span style=\"width:{max(2, usage_total(row['usage']) / max_daily_tokens * 100):.1f}%\"></span></div>",
            number(usage_total(row["usage"])),
            number(row["usage"]["input_tokens"]),
            number(row["usage"]["cached_input_tokens"]),
            number(row["usage"]["output_tokens"]),
            number(row["estimated_codex_credits"]),
            f"${number(row['estimated_api_usd_equiv'])}",
            minutes(row["active_seconds"]),
        ])
        for row in reversed(daily_rows)
    )

    project_html = "\n".join(
        tr([
            html.escape(row["project"]),
            html.escape(row["cwd"]),
            number(row["thread_count"]),
            number(usage_total(row["usage"])),
            number(row["estimated_codex_credits"]),
            f"${number(row['estimated_api_usd_equiv'])}",
            minutes(row["active_seconds"]),
        ])
        for row in project_rows
    )

    model_html = "\n".join(
        tr([
            html.escape(row["model"]),
            number(row["thread_count"]),
            number(usage_total(row["usage"])),
            number(row["usage"]["cached_input_tokens"]),
            number(row["usage"]["output_tokens"]),
            number(row["estimated_codex_credits"]),
            f"${number(row['estimated_api_usd_equiv'])}",
        ])
        for row in model_rows
    )

    thread_html = "\n".join(
        tr([
            html.escape(thread.get("title") or "(untitled)"),
            html.escape(thread.get("project") or ""),
            html.escape(thread.get("model") or ""),
            fmt_dt(thread.get("ended_at")),
            number(usage_total(thread["usage"])),
            number(thread.get("estimated_codex_credits") or 0.0),
            f"${number(thread.get('estimated_api_usd_equiv') or 0.0)}",
            minutes(thread.get("active_seconds") or 0),
        ])
        for thread in recent_threads
    )

    generated_at = fmt_dt(summary["generated_at"])
    total_tokens = usage_total(summary["usage"])
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Codex App Usage Dashboard</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #667085;
      --border: #d7dce2;
      --blue: #1f6feb;
      --green: #1a7f64;
      --amber: #b7791f;
      --red: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Segoe UI, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.45;
    }}
    header, main {{ max-width: 1280px; margin: 0 auto; padding: 24px; }}
    header {{ padding-top: 32px; }}
    h1 {{ margin: 0 0 6px; font-size: 30px; letter-spacing: 0; }}
    h2 {{ margin: 28px 0 12px; font-size: 18px; letter-spacing: 0; }}
    p {{ color: var(--muted); margin: 0; }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 12px;
      margin-top: 20px;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
      min-height: 92px;
    }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 8px; font-size: 22px; }}
    .metric:nth-child(2) strong {{ color: var(--blue); }}
    .metric:nth-child(3) strong {{ color: var(--green); }}
    .metric:nth-child(4) strong {{ color: var(--amber); }}
    .metric:nth-child(5) strong {{ color: var(--red); }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: auto;
    }}
    table {{ border-collapse: collapse; width: 100%; min-width: 860px; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; font-size: 13px; }}
    th {{ background: #eef2f6; color: #344054; font-weight: 650; position: sticky; top: 0; }}
    tr:last-child td {{ border-bottom: 0; }}
    .bar {{ width: 140px; height: 8px; background: #e4e7ec; border-radius: 999px; overflow: hidden; margin-top: 5px; }}
    .bar span {{ display: block; height: 100%; background: var(--blue); }}
    .note {{
      margin-top: 24px;
      padding: 14px 16px;
      border-left: 4px solid var(--amber);
      background: #fff8e6;
      color: #58430d;
      border-radius: 6px;
      font-size: 13px;
    }}
    @media (max-width: 900px) {{
      header, main {{ padding: 16px; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Codex App Usage Dashboard</h1>
    <p>Generated {html.escape(generated_at)} from local Codex app logs.</p>
    <section class="metrics">
      <div class="metric"><span>Threads</span><strong>{number(summary["thread_count"])}</strong></div>
      <div class="metric"><span>Total tokens</span><strong>{number(total_tokens)}</strong></div>
      <div class="metric"><span>Estimated Codex credits</span><strong>{number(summary["estimated_codex_credits"])}</strong></div>
      <div class="metric"><span>API-equivalent USD</span><strong>${number(summary["estimated_api_usd_equiv"])}</strong></div>
      <div class="metric"><span>Estimated active time</span><strong>{minutes(summary["active_seconds"])} min</strong></div>
    </section>
  </header>
  <main>
    <section>
      <h2>Daily Usage</h2>
      <div class="panel">
        <table>
          <thead><tr><th>Date</th><th>Token volume</th><th>Total</th><th>Input</th><th>Cached input</th><th>Output</th><th>Credits</th><th>API USD</th><th>Active min</th></tr></thead>
          <tbody>{daily_html}</tbody>
        </table>
      </div>
    </section>
    <section>
      <h2>Projects</h2>
      <div class="panel">
        <table>
          <thead><tr><th>Project</th><th>Folder</th><th>Threads</th><th>Tokens</th><th>Credits</th><th>API USD</th><th>Active min</th></tr></thead>
          <tbody>{project_html}</tbody>
        </table>
      </div>
    </section>
    <section>
      <h2>Models</h2>
      <div class="panel">
        <table>
          <thead><tr><th>Model</th><th>Threads</th><th>Tokens</th><th>Cached input</th><th>Output</th><th>Credits</th><th>API USD</th></tr></thead>
          <tbody>{model_html}</tbody>
        </table>
      </div>
    </section>
    <section>
      <h2>Most Expensive Threads</h2>
      <div class="panel">
        <table>
          <thead><tr><th>Thread</th><th>Project</th><th>Model</th><th>Last activity</th><th>Tokens</th><th>Credits</th><th>API USD</th><th>Active min</th></tr></thead>
          <tbody>{thread_html}</tbody>
        </table>
      </div>
    </section>
    <div class="note">
      Credit estimates use OpenAI's Codex token-based rate card. API USD is an API-pricing equivalent, not your authoritative Codex invoice.
      Exact billing should be checked in Codex Settings &gt; Usage or OpenAI billing.
    </div>
  </main>
</body>
</html>
"""
    output_path.write_text(html_doc, encoding="utf-8")


def write_reports(threads: list[dict[str, Any]], summary: dict[str, Any], output_dir: Path) -> dict[str, Path]:
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

    thread_rows = [flatten_thread_for_csv(thread) for thread in threads]
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

    render_dashboard(paths["dashboard_html"], threads, summary)
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


def command_report(args: argparse.Namespace) -> int:
    codex_home = Path(args.codex_home).expanduser()
    output_dir = Path(args.output_dir).expanduser()
    threads = load_threads(codex_home, days=args.days)
    summary = aggregate_threads(threads)
    paths = write_reports(threads, summary, output_dir)

    print(f"threads={summary['thread_count']}")
    print(f"tokens={usage_total(summary['usage'])}")
    print(f"estimated_codex_credits={summary['estimated_codex_credits']:.2f}")
    print(f"estimated_api_usd_equiv={summary['estimated_api_usd_equiv']:.2f}")
    print(f"active_minutes_est={summary['active_seconds'] / 60.0:.1f}")
    for name, path in paths.items():
        print(f"{name}={path}")
    return 0


def command_sync_wakatime(args: argparse.Namespace) -> int:
    codex_home = Path(args.codex_home).expanduser()
    threads = load_threads(codex_home, days=args.days)
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

    subparsers = parser.add_subparsers(dest="command")

    report = subparsers.add_parser("report", help="Generate JSON, CSV, and HTML reports.")
    report.set_defaults(func=command_report)

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
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
