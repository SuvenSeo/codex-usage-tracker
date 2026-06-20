# GUI brand logos

Bundled PNG logos identify the user's connected AI coding apps in the local desktop dashboard.

| File | App | Source |
| --- | --- | --- |
| `codex.png` | OpenAI Codex | [OpenAI favicon](https://openai.com/favicon.ico) (OpenAI trademark) |
| `claude.png` | Claude Code | [Claude favicon](https://claude.ai/favicon.ico) (Anthropic trademark) |
| `cursor.png` | Cursor | Cursor desktop app asset (`cursor-splash-logo-normal.png`) (Cursor trademark) |

Regenerate PNGs with:

```bash
python scripts/generate_brand_assets.py
```

The generator requires Pillow and, for Cursor, a local Cursor install on Windows. Committed PNGs are used at runtime so end users do not need to regenerate them.

These marks are used only to label the user's own local usage sources, not to imply endorsement.
