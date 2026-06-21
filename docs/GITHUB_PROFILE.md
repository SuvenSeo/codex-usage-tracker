# GitHub Profile Copy

Use this when editing the repository on GitHub:
**Settings → General** (name, description, topics) and the README as the landing page.

Live repo: https://github.com/SuvenSeo/ai-coding-usage-tracker

## Display Name

Keep the product name as **AI Coding Usage Tracker** in README and releases.
The GitHub repository slug is `ai-coding-usage-tracker`.

## Description (160 chars max)

```text
Local-first AI coding usage dashboard for Codex, Claude Code, and Cursor — tokens, credits, USD estimates, native GUI, and WakaTime sync.
```

Shorter alternative:

```text
Track Codex, Claude Code, and Cursor usage locally — tokens, costs, dashboards, and optional WakaTime sync.
```

## Website / Homepage

Point to the repo or latest release:

```text
https://github.com/SuvenSeo/ai-coding-usage-tracker
```

Or:

```text
https://github.com/SuvenSeo/ai-coding-usage-tracker/releases/latest
```

## Topics

Recommended topic set (paste into GitHub **Topics** field):

```text
ai-coding
claude-code
codex
cursor
developer-tools
local-first
openai
productivity
python
token-usage
usage-dashboard
wakatime
```

Optional extras if you have room:

```text
anthropic
tkinter
privacy
```

## About Blurb (for social / pinned repo)

```text
AI Coding Usage Tracker reads local Codex, Claude Code, and Cursor logs and builds private HTML, CSV, JSON, web, and desktop dashboards — no cloud upload. v0.2.6 adds a cached native GUI, multi-source token totals, and brand logos.
```

## Release Title Template

```text
v0.2.6 — Cached GUI refresh and multi-source dashboards
```

## Suggested Pinned README Lead

The README already opens with badges, a one-line pitch, and the demo SVG.
After pushing doc updates, confirm these render correctly:

- CI badge → `SuvenSeo/ai-coding-usage-tracker`
- Release badge → `SuvenSeo/ai-coding-usage-tracker`
- Demo image → `raw.githubusercontent.com/SuvenSeo/ai-coding-usage-tracker/main/docs/assets/demo-dashboard.svg`

## Apply With GitHub CLI

```powershell
gh repo edit SuvenSeo/ai-coding-usage-tracker `
  --description "Local-first AI coding usage dashboard for Codex, Claude Code, and Cursor — tokens, credits, USD estimates, native GUI, and WakaTime sync." `
  --homepage "https://github.com/SuvenSeo/ai-coding-usage-tracker/releases/latest"

gh repo edit SuvenSeo/ai-coding-usage-tracker `
  --add-topic ai-coding --add-topic claude-code --add-topic codex --add-topic cursor `
  --add-topic developer-tools --add-topic local-first --add-topic openai `
  --add-topic productivity --add-topic python --add-topic token-usage `
  --add-topic usage-dashboard --add-topic wakatime
```

## Social Card Preview

GitHub uses the repository description and the README for link previews.
Lead with **multi-source** and **local-first** — not Codex-only language.
