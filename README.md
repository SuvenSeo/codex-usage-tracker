# AI Coding Usage Tracker

[![CI](https://github.com/SuvenSeo/ai-coding-usage-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/SuvenSeo/ai-coding-usage-tracker/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/SuvenSeo/ai-coding-usage-tracker)](https://github.com/SuvenSeo/ai-coding-usage-tracker/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

Local-first usage analytics for Codex, Claude Code, and Cursor.

AI Coding Usage Tracker reads local AI coding data and generates a private
dashboard for token usage, app/source totals, estimated Codex credits,
estimated USD, project breakdowns, terminal reports, and optional WakaTime
`ai coding` time.

![Demo dashboard](https://raw.githubusercontent.com/SuvenSeo/ai-coding-usage-tracker/main/docs/assets/demo-dashboard.svg)

## Why

AI coding tools can burn through tokens, but it is hard to answer basic questions:

- Which project used the most tokens?
- Which thread was the most expensive?
- How much active AI coding time did I spend today?
- How much of my input was cached?
- Can my Codex app activity show up in WakaTime?

This tool gives you those answers locally, without uploading Codex transcripts to another service.

## Features

- Reads local Codex app rollout logs from `~/.codex`
- Reads Claude Code project transcripts from `~/.claude/projects`
- Reads Cursor from Agent transcripts, Composer bubbles, `agentKv` cache, and the AI tracking DB
- Estimates Cursor tokens with Claude-style **context cache replay** when exact counts are missing locally
- Generates `HTML`, `CSV`, and `JSON` reports
- Serves a live local web dashboard with auto-refresh on `127.0.0.1`
- Shows trend bars, provider comparison tabs, budget signals, and connector status
- Native GUI with combined lifetime totals, per-app cards, and a **Lifetime Totals** tab
- Prints `daily`, `weekly`, `monthly`, `session`, `project`, `model`, and `source` terminal reports
- Estimates Codex credits from input, cached input, and output tokens
- Shows estimated USD for Codex, Claude Code, and Cursor where local token logs and known rates exist
- Checks optional official billing connectors without printing configured keys
- Includes `doctor` and `demo` commands for first-run confidence
- Supports date filters with `--days`, `--since`, `--until`, and `--timezone`
- Supports share-safe output with `--redact` and `--hash-projects`
- Sends optional WakaTime `ai coding` heartbeats
- Works without an OpenAI API key

## Install

Install from PyPI with `pipx`:

```bash
pipx install codex-usage-tracker
ai-coding-usage-tracker demo
```

Or install with `pip`:

```bash
python -m pip install codex-usage-tracker
ai-coding-usage-tracker demo
```

The PyPI package is still named `codex-usage-tracker` for compatibility. New
installs expose `ai-coding-usage-tracker`; the legacy `codex-usage-tracker`
command remains available.

For local development, clone the repo:

```bash
git clone https://github.com/SuvenSeo/ai-coding-usage-tracker.git
cd ai-coding-usage-tracker
```

Run directly with Python:

```bash
python codex_app_tracker.py report
```

Or install the CLI locally:

```bash
pip install -e .
ai-coding-usage-tracker report
```

Windows users can also download `AICodingUsageTracker.exe` from the
[latest release](https://github.com/SuvenSeo/ai-coding-usage-tracker/releases/latest).
The release includes a SHA256 checksum file.

## Quick Start

Check your machine:

```bash
ai-coding-usage-tracker doctor
```

Generate the full local dashboard:

```bash
ai-coding-usage-tracker report
```

Include Codex, Claude Code, and Cursor in one report:

```bash
ai-coding-usage-tracker --sources all report
```

Open:

```text
out/dashboard.html
```

Serve a live local web dashboard:

```bash
ai-coding-usage-tracker --sources all --days 14 serve
```

By default this opens `http://127.0.0.1:8765/`, reloads the page every 10
seconds, and keeps all data local. Use `serve --port 0 --no-open` when you want
an auto-selected port without launching a browser.

Open the live native desktop dashboard:

```bash
ai-coding-usage-tracker --sources all gui
```

The GUI (v0.2.2+) uses Python's built-in Tkinter toolkit and polls selected local
sources every 10 seconds by default. It opens in dark mode, shows:

- **Overview** — combined lifetime hero, Codex / Claude / Cursor cards with cached
  token breakdown, quick stats, and app/daily charts
- **Lifetime Totals** — full provider table plus project/model charts
- **Apps, Daily, Projects, Models, Threads, Signals, Billing** — sortable tables

The packaged Windows EXE defaults to `--sources all`. The first Cursor refresh can
take up to a minute while local transcripts and cache blobs are scanned.

Buttons include **Refresh now**, **HTML report**, and **Open live web dashboard**.
Change the refresh interval with:

```bash
ai-coding-usage-tracker gui --refresh-seconds 5
```

After rebuilding the EXE, update Desktop and Start Menu shortcuts:

```powershell
.\scripts\build_windows_exe.ps1
.\scripts\install_windows_app.ps1
```

Close the running app first if the install step reports the EXE is in use.

Try a safe public demo:

```bash
ai-coding-usage-tracker demo
```

That writes synthetic reports to `out/demo/`.

Build a double-clickable Windows EXE:

```powershell
python -m pip install -e ".[build]"
.\scripts\build_windows_exe.ps1
```

The generated app is written to `dist\AICodingUsageTracker.exe`. Double-clicking it
opens the live GUI with Codex, Claude Code, and Cursor selected. It reads only
local data folders on your machine.

Install it as a normal user-level Windows app:

```powershell
.\scripts\install_windows_app.ps1
```

That copies the EXE to `%LOCALAPPDATA%\Programs\AICodingUsageTracker` and creates
Start Menu plus Desktop shortcuts. Remove it with:

```powershell
.\scripts\uninstall_windows_app.ps1
```

## CLI Reports

Global scope and privacy flags go before the command:

```bash
ai-coding-usage-tracker --days 7 daily
ai-coding-usage-tracker --since 2026-05-01 --until 2026-05-24 weekly
ai-coding-usage-tracker --timezone Asia/Colombo monthly
ai-coding-usage-tracker --sources all source
ai-coding-usage-tracker session --limit 10 --compact
ai-coding-usage-tracker project --format json
ai-coding-usage-tracker model --format csv
```

Create a share-safe report:

```bash
ai-coding-usage-tracker --sources all --redact --hash-projects report
```

Source selection is opt-in. The default is `--sources codex`; use
`--sources claude`, `--sources cursor`, or a comma-separated list such as
`--sources codex,claude`.

Audit every local and official source the tracker knows about:

```bash
ai-coding-usage-tracker --sources all source-audit
```

The audit writes `out/source_audit.json` and `out/source_audit.md`. It separates
exact local metrics from estimates and blocked sources. Cursor token totals are
estimated from local Agent transcripts, Composer bubbles, and `agentKv` blobs
with context-cache replay (similar in spirit to Claude's cache-read accounting).
Vendor admin APIs can provide deeper billing detail only when the matching admin
key is configured.

Check optional official billing connector status:

```bash
ai-coding-usage-tracker billing
```

Set provider admin keys only when you want official account-side checks, then
run an explicit fetch:

```bash
ai-coding-usage-tracker billing --fetch --format json
```

Supported environment variable names are `OPENAI_ADMIN_KEY`,
`ANTHROPIC_ADMIN_KEY`, and `CURSOR_ADMIN_API_KEY`. Keys are never printed.

Add dashboard budget signals with global flags before the command:

```bash
ai-coding-usage-tracker --sources all --daily-token-budget 250000 --daily-usd-budget 5 --monthly-usd-budget 100 report
```

## WakaTime

WakaTime sync is optional. It requires `wakatime-cli` and a normal `~/.wakatime.cfg` file.

Generate reports and sync recent Codex activity:

```bash
ai-coding-usage-tracker run --sync-wakatime
```

The tracker sends conservative heartbeats with:

- category: `ai coding`
- entity type: `app`
- project name/folder
- timestamp

It does not send prompts, responses, transcripts, or token totals to WakaTime.
WakaTime sync currently uses Codex records only; Claude Code and Cursor are
included in reports but not synced as WakaTime heartbeats.

On Windows, you can install a scheduled task:

```powershell
.\install_scheduled_task.ps1
```

Remove it:

```powershell
.\uninstall_scheduled_task.ps1
```

## Output

By default, reports are written to `out/`:

- `dashboard.html`
- `codex_usage_summary.json`
- `threads.csv`
- `daily.csv`
- `sources.csv`
- `projects.csv`
- `models.csv`

Do not commit generated reports. They can include private local paths, project names, thread titles, and usage details unless you use privacy flags.

## Cost Estimates

The tracker reports local token counts where app logs provide them.

Cost estimates are not an invoice:

- `estimated_codex_credits` uses OpenAI's current Codex token-based rate card for Codex records.
- `estimated_api_usd_equiv` uses public OpenAI API standard short-context prices for Codex and Anthropic token prices for Claude Code when known local model rates exist.
- **Cursor** totals are **estimates** from local logs: Agent transcripts under
  `~/.cursor/projects`, Composer bubbles and `agentKv` blobs in Cursor's
  `state.vscdb`, plus AI edit activity from `~/.cursor/ai-tracking`. When Cursor
  stores zero token counts, the tracker estimates from character length and
  applies context-cache replay so cached input is visible like Claude Code.
- The included rates were verified against official OpenAI and Anthropic docs on 2026-05-31.
- Real billing/credit balance, fast-mode uplifts, taxes, and any workspace exceptions should be checked with the vendor.

Pricing can change. The current source pages are OpenAI's [Codex rate card](https://help.openai.com/en/articles/20001106-codex-rate-card), OpenAI [API pricing](https://developers.openai.com/api/docs/pricing), Anthropic [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing?hsLang=en), and Cursor [pricing](https://cursor.com/en-US/pricing). Review and update `MODEL_RATES` in `codex_app_tracker.py` when vendor rates change.

## Privacy

This is a local parser. Depending on `--sources`, it reads from `~/.codex`,
`~/.claude/projects`, `~/.cursor/projects` (Agent transcripts),
`%APPDATA%\Cursor\User\globalStorage\state.vscdb` (Composer/agent cache), and/or
`~/.cursor/ai-tracking`. It writes local reports.

Use `--redact` to hide thread titles, local folders, and log paths. Use `--hash-projects` to replace project names with stable anonymous labels.

See [docs/PRIVACY.md](docs/PRIVACY.md) before sharing dashboards or CSV files.

## Comparison

| Tool | Best For | Difference |
| --- | --- | --- |
| AI Coding Usage Tracker | AI coding users who want local Codex, Claude Code, and Cursor visibility | Dependency-light, private generated dashboard with Codex token/cost depth |
| ccusage | Multi-agent terminal usage reports | Broader agent support and mature CLI reporting |
| agentsview | Local multi-agent session intelligence | Richer local web app and session indexing |
| codex-wakatime | Codex CLI WakaTime hook | Focused time tracking, not token/cost dashboards |

The near-term goal is to keep Codex as the deepest parser while adding honest local visibility for adjacent AI coding tools.

## Development

Run checks:

```bash
python -m py_compile codex_app_tracker.py
python -m unittest discover -s tests -v
```

Build package artifacts:

```bash
python -m pip install build
python -m build
```

New contributors can start with
[good first issues](https://github.com/SuvenSeo/ai-coding-usage-tracker/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
or the current [roadmap](docs/ROADMAP.md). See [CONTRIBUTING.md](CONTRIBUTING.md)
for the privacy rules and PR checklist.

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md).

## Built By

Built by [SuvenSeo](https://github.com/SuvenSeo) for developers who want local visibility into AI coding usage.

## Status

Early alpha. Codex, Claude Code, and Cursor local storage formats may change, so
parser compatibility can break. **v0.2.2** adds Cursor context-cache token
estimates and a redesigned native GUI with combined lifetime totals. The Python
package is published on
[PyPI](https://pypi.org/project/codex-usage-tracker/) and can be installed with
`pipx install codex-usage-tracker` or `python -m pip install codex-usage-tracker`.
Issues and PRs are welcome.
