# Changelog

All notable changes to this project are documented here.

## v0.2.0 - 2026-05-30

- Added opt-in multi-source reports with `--sources codex,claude,cursor` and `--sources all`.
- Added Claude Code JSONL parsing from `~/.claude/projects` with local token totals by session/model/day.
- Added Cursor AI tracking DB parsing from `~/.cursor/ai-tracking/ai-code-tracking.db` for AI edit activity, request counts, models, timestamps, and active time.
- Added app/source totals to the HTML dashboard, GUI, JSON, CSV, and new `source` terminal report.
- Kept Codex as the only source with Codex credit estimates and WakaTime sync.

## v0.1.1 - 2026-05-30

- Published the Python package to PyPI using GitHub Actions trusted publishing.
- Updated installation docs to prefer `pipx install codex-usage-tracker`.
- Documented the working trusted publisher configuration.

## v0.1.0 - 2026-05-29

First public release.

- Added local Codex usage parsing from `~/.codex`.
- Added HTML, CSV, and JSON report generation.
- Added daily, weekly, monthly, session, project, and model terminal reports.
- Added `doctor` and `demo` commands for first-run confidence.
- Added share-safe `--redact` and `--hash-projects` modes.
- Added optional WakaTime `ai coding` heartbeat sync.
- Added native Tkinter GUI and Windows executable release asset.
- Added CI, MIT license, privacy docs, publishing notes, and roadmap.
