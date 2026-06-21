## Summary

- 

## Verification

```bash
python -m py_compile codex_app_tracker.py report_cache.py gui_visuals.py
python -m unittest discover -s tests -v
```

## Privacy

- [ ] This change does not expose prompts, responses, raw Codex logs, API keys, or private local paths.
- [ ] Any new external integration documents what data leaves the machine.

## Notes

- 
