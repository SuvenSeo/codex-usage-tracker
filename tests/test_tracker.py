import json
import tempfile
import unittest
from pathlib import Path

import codex_app_tracker as tracker


class CodexUsageTrackerTests(unittest.TestCase):
    def test_report_from_minimal_rollout(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / ".codex"
            rollout_dir = codex_home / "sessions" / "2026" / "05" / "01"
            rollout_dir.mkdir(parents=True)
            rollout = rollout_dir / "rollout-2026-05-01T10-00-00-019example-0000-7000-8000-000000000001.jsonl"
            rows = [
                {
                    "timestamp": "2026-05-01T10:00:00Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "019example-0000-7000-8000-000000000001",
                        "cwd": "C:\\Projects\\example-app",
                        "source": "codex_desktop",
                    },
                },
                {
                    "timestamp": "2026-05-01T10:00:05Z",
                    "type": "turn_context",
                    "payload": {
                        "cwd": "C:\\Projects\\example-app",
                        "model": "gpt-5.5",
                        "effort": "xhigh",
                    },
                },
                {
                    "timestamp": "2026-05-01T10:00:10Z",
                    "type": "event_msg",
                    "payload": {
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 1000,
                                "cached_input_tokens": 400,
                                "output_tokens": 50,
                                "reasoning_output_tokens": 10,
                                "total_tokens": 1050,
                            }
                        }
                    },
                },
            ]
            rollout.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            threads = tracker.load_threads(codex_home)
            summary = tracker.aggregate_threads(threads)

            self.assertEqual(len(threads), 1)
            self.assertEqual(threads[0]["project"], "example-app")
            self.assertEqual(summary["usage"]["total_tokens"], 1050)
            self.assertGreater(summary["estimated_codex_credits"], 0)

    def test_cached_input_is_charged_separately(self):
        usage = {
            "input_tokens": 1000,
            "cached_input_tokens": 600,
            "output_tokens": 100,
            "reasoning_output_tokens": 0,
            "total_tokens": 1100,
        }
        credits = tracker.estimate_amount(usage, "gpt-5.5", "codex_credits")
        expected = (400 / 1_000_000) * 125.0 + (600 / 1_000_000) * 12.5 + (100 / 1_000_000) * 750.0
        self.assertAlmostEqual(credits, expected)

    def test_windows_project_name_on_any_platform(self):
        self.assertEqual(
            tracker.project_name_from_cwd("C:\\Projects\\example-app"),
            "example-app",
        )


if __name__ == "__main__":
    unittest.main()
