# Privacy Notes

This tool is designed to be local-first.

## What It Reads

- Codex rollout JSONL files under `~/.codex/sessions`
- Codex metadata from `~/.codex/state_5.sqlite`, if available
- WakaTime config presence from `~/.wakatime.cfg`, only to verify that an API key exists

## What It Writes

- Local JSON, CSV, and HTML reports under `out/` by default
- `.tracker_state.json`, used to avoid sending duplicate WakaTime heartbeats

## What Can Be Sensitive

Generated reports can contain:

- Local filesystem paths
- Project names
- Thread titles
- Model names and token counts
- Estimated usage costs

The repository `.gitignore` excludes generated reports by default. Review reports before sharing screenshots or files publicly.

## WakaTime Sync

When `sync-wakatime` is enabled, the tool sends WakaTime heartbeats with:

- Activity category: `ai coding`
- Entity type: `app`
- Project name/folder
- Timestamp

It does not send token counts, prompts, responses, or Codex transcript content to WakaTime.

