# Codex Usage Tracker

[![CI](https://github.com/SuvenSeo/codex-usage-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/SuvenSeo/codex-usage-tracker/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Local-first usage analytics for the Codex app.

Codex Usage Tracker reads your local Codex app logs and generates a private dashboard for token usage, estimated Codex credits, API-equivalent cost estimates, project breakdowns, terminal reports, and optional WakaTime `ai coding` time.

![Demo dashboard](https://raw.githubusercontent.com/SuvenSeo/codex-usage-tracker/main/docs/assets/demo-dashboard.svg)

## Why

AI coding tools can burn through tokens, but it is hard to answer basic questions:

- Which project used the most tokens?
- Which thread was the most expensive?
- How much active Codex time did I spend today?
- How much of my input was cached?
- Can my Codex app activity show up in WakaTime?

This tool gives you those answers locally, without uploading Codex transcripts to another service.

## Features

- Reads local Codex app rollout logs from `~/.codex`
- Generates `HTML`, `CSV`, and `JSON` reports
- Prints `daily`, `weekly`, `monthly`, `session`, `project`, and `model` terminal reports
- Estimates Codex credits from input, cached input, and output tokens
- Shows API-equivalent USD estimates for rough comparison
- Includes `doctor` and `demo` commands for first-run confidence
- Supports date filters with `--days`, `--since`, `--until`, and `--timezone`
- Supports share-safe output with `--redact` and `--hash-projects`
- Sends optional WakaTime `ai coding` heartbeats
- Works without an OpenAI API key

## Install

Clone the repo:

```bash
git clone https://github.com/SuvenSeo/codex-usage-tracker.git
cd codex-usage-tracker
```

Run directly with Python:

```bash
python codex_app_tracker.py report
```

Or install the CLI locally:

```bash
pip install -e .
codex-usage-tracker report
```

## Quick Start

Check your machine:

```bash
codex-usage-tracker doctor
```

Generate the full local dashboard:

```bash
codex-usage-tracker report
```

Open:

```text
out/dashboard.html
```

Try a safe public demo:

```bash
codex-usage-tracker demo
```

That writes synthetic reports to `out/demo/`.

## CLI Reports

Global scope and privacy flags go before the command:

```bash
codex-usage-tracker --days 7 daily
codex-usage-tracker --since 2026-05-01 --until 2026-05-24 weekly
codex-usage-tracker --timezone Asia/Colombo monthly
codex-usage-tracker session --limit 10 --compact
codex-usage-tracker project --format json
codex-usage-tracker model --format csv
```

Create a share-safe report:

```bash
codex-usage-tracker --redact --hash-projects report
```

## WakaTime

WakaTime sync is optional. It requires `wakatime-cli` and a normal `~/.wakatime.cfg` file.

Generate reports and sync recent Codex activity:

```bash
codex-usage-tracker run --sync-wakatime
```

The tracker sends conservative heartbeats with:

- category: `ai coding`
- entity type: `app`
- project name/folder
- timestamp

It does not send prompts, responses, transcripts, or token totals to WakaTime.

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
- `projects.csv`
- `models.csv`

Do not commit generated reports. They can include private local paths, project names, thread titles, and usage details unless you use privacy flags.

## Cost Estimates

The tracker reports exact local token counts where Codex logs provide them.

Cost estimates are not an invoice:

- `estimated_codex_credits` uses OpenAI's Codex token-based rate card.
- `estimated_api_usd_equiv` uses public OpenAI API prices as an equivalent estimate.
- Real Codex billing/credit balance should be checked in Codex Settings > Usage or OpenAI billing.

Pricing can change. Review and update `MODEL_RATES` in `codex_app_tracker.py` when OpenAI updates rates.

## Privacy

This is a local parser. It reads from `~/.codex` and writes local reports.

Use `--redact` to hide thread titles, local folders, and log paths. Use `--hash-projects` to replace project names with stable anonymous labels.

See [docs/PRIVACY.md](docs/PRIVACY.md) before sharing dashboards or CSV files.

## Comparison

| Tool | Best For | Difference |
| --- | --- | --- |
| Codex Usage Tracker | Codex app users who want local reports and WakaTime sync | Codex-specific, dependency-light, private generated dashboard |
| ccusage | Multi-agent terminal usage reports | Broader agent support and mature CLI reporting |
| agentsview | Local multi-agent session intelligence | Richer local web app and session indexing |
| codex-wakatime | Codex CLI WakaTime hook | Focused time tracking, not token/cost dashboards |

The near-term goal is to be the simplest local-first Codex usage dashboard, then expand only after the Codex workflow feels solid.

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

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md).

## Built By

Built by [SuvenSeo](https://github.com/SuvenSeo) for developers who want local visibility into Codex usage.

## Status

Early alpha. Codex local log formats may change, so parser compatibility can break. Issues and PRs are welcome.
