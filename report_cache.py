"""Disk cache for parsed threads and GUI models — fast startup and incremental refresh."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_CACHE_VERSION = 1
DEFAULT_REPORT_CACHE_DIR = Path.home() / ".codex-usage-tracker" / "report_cache"

_THREAD_DT_FIELDS = ("started_at", "ended_at")
_SUMMARY_DT_FIELDS = ("generated_at",)


def report_cache_dir(args: Any) -> Path:
    custom = getattr(args, "cache_dir", None)
    if custom:
        return Path(custom).expanduser()
    return DEFAULT_REPORT_CACHE_DIR


def report_cache_args_key(args: Any) -> str:
    parts = [
        str(getattr(args, "sources", "codex")),
        str(getattr(args, "days", "")),
        str(getattr(args, "since", "")),
        str(getattr(args, "until", "")),
        str(getattr(args, "timezone", "")),
        str(Path(getattr(args, "codex_home", "")).expanduser()),
        str(Path(getattr(args, "claude_home", "")).expanduser()),
        str(Path(getattr(args, "cursor_db", "")).expanduser()),
        str(Path(getattr(args, "cursor_state_db", "")).expanduser()),
        str(Path(getattr(args, "cursor_projects_home", "")).expanduser()),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:20]


def report_cache_path(args: Any) -> Path:
    return report_cache_dir(args) / f"{report_cache_args_key(args)}.json"


def file_fingerprint(path: Path) -> dict[str, int]:
    try:
        stat = path.stat()
        return {"mtime_ns": int(stat.st_mtime_ns), "size": int(stat.st_size)}
    except OSError:
        return {"mtime_ns": 0, "size": 0}


def db_fingerprint(path: Path) -> dict[str, int]:
    return file_fingerprint(path)


def build_scan_roots(args: Any, sources: set[str]) -> dict[str, dict[str, int]]:
    roots: dict[str, dict[str, int]] = {}
    if "codex" in sources:
        codex_home = Path(getattr(args, "codex_home", "")).expanduser()
        for label, child in (("codex_sessions", "sessions"), ("codex_archived", "archived_sessions")):
            path = codex_home / child
            if path.exists():
                roots[label] = file_fingerprint(path)
    if "claude" in sources:
        claude_home = Path(getattr(args, "claude_home", "")).expanduser()
        projects = claude_home / "projects"
        if projects.exists():
            roots["claude_projects"] = file_fingerprint(projects)
    if "cursor" in sources:
        projects_home = Path(getattr(args, "cursor_projects_home", "")).expanduser()
        if projects_home.exists():
            roots["cursor_projects"] = file_fingerprint(projects_home)
        cursor_db = Path(getattr(args, "cursor_db", "")).expanduser()
        if cursor_db.exists():
            roots["cursor_tracking_db"] = file_fingerprint(cursor_db)
        state_db = Path(getattr(args, "cursor_state_db", "")).expanduser()
        if state_db.exists():
            roots["cursor_state_db"] = file_fingerprint(state_db)
    return roots


def scan_roots_match(cached: dict[str, Any], current: dict[str, dict[str, int]]) -> bool:
    if not cached:
        return False
    for key, value in current.items():
        stored = cached.get(key)
        if not isinstance(stored, dict):
            return False
        if stored.get("mtime_ns") != value.get("mtime_ns") or stored.get("size") != value.get("size"):
            return False
    return True


def cache_fingerprints_valid(file_index: dict[str, Any], db_index: dict[str, Any], db_paths: dict[str, Path]) -> bool:
    for path_str in file_index:
        try:
            if not fingerprint_matches(file_index, Path(path_str)):
                return False
        except OSError:
            return False
    for key, path in db_paths.items():
        if path.exists() and not db_fingerprint_matches(db_index, key, path):
            return False
    return True


def thread_to_json(thread: dict[str, Any]) -> dict[str, Any]:
    payload = dict(thread)
    for field in _THREAD_DT_FIELDS:
        value = payload.get(field)
        if isinstance(value, datetime):
            payload[field] = value.isoformat()
    timestamps = payload.get("event_timestamps")
    if isinstance(timestamps, list):
        payload["event_timestamps"] = [
            item.isoformat() if isinstance(item, datetime) else item
            for item in timestamps
        ]
    return payload


def thread_from_json(data: dict[str, Any]) -> dict[str, Any]:
    thread = dict(data)
    for field in _THREAD_DT_FIELDS:
        value = thread.get(field)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            thread[field] = parsed.astimezone(timezone.utc)
    timestamps = thread.get("event_timestamps")
    if isinstance(timestamps, list):
        restored: list[datetime] = []
        for item in timestamps:
            if isinstance(item, str):
                parsed = datetime.fromisoformat(item.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                restored.append(parsed.astimezone(timezone.utc))
            elif isinstance(item, datetime):
                restored.append(item.astimezone(timezone.utc))
        thread["event_timestamps"] = restored
    return thread


def load_report_cache(args: Any) -> dict[str, Any] | None:
    path = report_cache_path(args)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("version") != REPORT_CACHE_VERSION:
        return None
    if payload.get("args_key") != report_cache_args_key(args):
        return None
    return payload


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def save_report_cache(
    args: Any,
    *,
    threads: list[dict[str, Any]],
    summary: dict[str, Any],
    file_index: dict[str, Any],
    db_index: dict[str, Any],
    gui_model: dict[str, Any] | None = None,
    scan_roots: dict[str, dict[str, int]] | None = None,
) -> None:
    path = report_cache_path(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    threads_by_id = {str(thread.get("thread_id") or ""): thread_to_json(thread) for thread in threads}
    payload: dict[str, Any] = {
        "version": REPORT_CACHE_VERSION,
        "args_key": report_cache_args_key(args),
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "file_index": file_index,
        "db_index": db_index,
        "threads_by_id": threads_by_id,
        "summary": summary,
    }
    if scan_roots is not None:
        payload["scan_roots"] = scan_roots
    if gui_model is not None:
        payload["gui_model"] = gui_model
    path.write_text(json.dumps(payload, ensure_ascii=False, default=_json_default), encoding="utf-8")


def cached_threads_map(cache: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = cache.get("threads_by_id") or {}
    if not isinstance(raw, dict):
        return {}
    return {
        thread_id: thread_from_json(data)
        for thread_id, data in raw.items()
        if thread_id and isinstance(data, dict)
    }


def load_cached_gui_model(args: Any) -> tuple[dict[str, Any], str] | None:
    cache = load_report_cache(args)
    if not cache:
        return None
    model = cache.get("gui_model")
    if not isinstance(model, dict):
        return None
    saved_at = str(cache.get("saved_at") or "")
    return model, saved_at


def load_cached_snapshot(args: Any) -> dict[str, Any] | None:
    """Return cached threads, summary, and optional gui_model without touching source logs."""
    return load_report_cache(args)


def fingerprint_matches(index: dict[str, Any], path: Path) -> bool:
    current = file_fingerprint(path)
    stored = index.get(str(path))
    if not isinstance(stored, dict):
        return False
    return stored.get("mtime_ns") == current["mtime_ns"] and stored.get("size") == current["size"]


def db_fingerprint_matches(index: dict[str, Any], key: str, path: Path) -> bool:
    current = db_fingerprint(path)
    stored = index.get(key)
    if not isinstance(stored, dict):
        return False
    return stored.get("mtime_ns") == current["mtime_ns"] and stored.get("size") == current["size"]


def store_file_index(index: dict[str, Any], path: Path, thread_id: str) -> None:
    fp = file_fingerprint(path)
    index[str(path)] = {**fp, "thread_id": thread_id}


def store_db_index(index: dict[str, Any], key: str, path: Path) -> None:
    index[key] = db_fingerprint(path)


def prune_file_index(index: dict[str, Any], valid_paths: set[str]) -> None:
    stale = [key for key in index if key not in valid_paths]
    for key in stale:
        index.pop(key, None)


def prune_threads_by_file_index(
    threads_by_id: dict[str, dict[str, Any]],
    file_index: dict[str, Any],
) -> None:
    live_ids = {
        str(entry.get("thread_id") or "")
        for entry in file_index.values()
        if isinstance(entry, dict) and entry.get("thread_id")
    }
    stale = [thread_id for thread_id in threads_by_id if thread_id not in live_ids]
    for thread_id in stale:
        source = str((threads_by_id.get(thread_id) or {}).get("source") or "")
        if source.startswith("cursor_") or source in {"codex_rollout", "claude_jsonl"}:
            threads_by_id.pop(thread_id, None)
