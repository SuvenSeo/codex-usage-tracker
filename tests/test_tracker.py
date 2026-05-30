import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
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
            self.assertEqual(threads[0]["app"], "codex")
            self.assertEqual(threads[0]["project"], "example-app")
            self.assertEqual(summary["usage"]["total_tokens"], 1050)
            self.assertEqual(summary["sources"][0]["app"], "Codex")
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

    def test_date_filter_uses_last_activity(self):
        early = {
            "thread_id": "early",
            "started_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
            "ended_at": datetime(2026, 5, 1, 1, tzinfo=timezone.utc),
        }
        late = {
            "thread_id": "late",
            "started_at": datetime(2026, 5, 3, tzinfo=timezone.utc),
            "ended_at": datetime(2026, 5, 3, 1, tzinfo=timezone.utc),
        }
        filtered = tracker.filter_threads_by_date([early, late], since="2026-05-02")
        self.assertEqual([thread["thread_id"] for thread in filtered], ["late"])

    def test_privacy_redacts_paths_and_titles(self):
        thread = {
            "thread_id": "abc",
            "title": "Private client work",
            "cwd": "C:\\Secret\\client-app",
            "project": "client-app",
            "path": "C:\\Users\\suven\\.codex\\sessions\\rollout.jsonl",
            "usage": tracker.zero_usage(),
            "daily_usage": {},
            "active_daily": {},
            "tool_counts": {},
            "event_timestamps": [],
        }
        redacted = tracker.apply_privacy([thread], redact=True, hash_projects=True)[0]
        self.assertEqual(redacted["title"], "(redacted)")
        self.assertEqual(redacted["cwd"], "")
        self.assertEqual(redacted["path"], "")
        self.assertTrue(redacted["project"].startswith("project-"))

    def test_demo_threads_are_aggregatable(self):
        threads = tracker.demo_threads()
        summary = tracker.aggregate_threads(threads)
        self.assertGreaterEqual(len(threads), 5)
        self.assertGreaterEqual(len(summary["sources"]), 3)
        self.assertGreater(summary["usage"]["total_tokens"], 0)

    def test_gui_subcommand_accepts_refresh_seconds(self):
        parser = tracker.build_parser()
        args = parser.parse_args(["--days", "7", "--timezone", "Asia/Colombo", "gui", "--refresh-seconds", "5"])

        self.assertEqual(args.command, "gui")
        self.assertEqual(args.refresh_seconds, 5)
        self.assertIs(args.func, tracker.command_gui)

    def test_gui_refresh_seconds_rejects_low_values(self):
        with self.assertRaises(ValueError):
            tracker.validate_refresh_seconds(1)

        parser = tracker.build_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["gui", "--refresh-seconds", "1"])

    def test_gui_view_model_formats_demo_summary(self):
        threads = tracker.demo_threads()
        summary = tracker.aggregate_threads(threads)
        model = tracker.build_gui_view_model(threads, summary)

        metrics = dict(model["metrics"])
        self.assertIn("Total tokens", metrics)
        self.assertEqual(metrics["Threads"], "5")
        self.assertEqual(metrics["Apps"], "3")
        self.assertTrue(model["tables"]["sources"]["rows"])
        self.assertTrue(model["tables"]["daily"]["rows"])
        self.assertTrue(model["tables"]["projects"]["rows"])
        self.assertTrue(model["tables"]["models"]["rows"])
        self.assertTrue(model["tables"]["threads"]["rows"])
        self.assertEqual(model["token_delta"], "")
        self.assertEqual(model["pricing"]["source_date"], tracker.PRICING_SOURCE_DATE)

    def test_gui_view_model_uses_shared_privacy_path(self):
        threads = tracker.apply_privacy(tracker.demo_threads(), redact=True, hash_projects=True)
        summary = tracker.aggregate_threads(threads)
        model = tracker.build_gui_view_model(threads, summary)

        thread_rows = model["tables"]["threads"]["rows"]
        project_rows = model["tables"]["projects"]["rows"]
        self.assertTrue(thread_rows)
        self.assertTrue(project_rows)
        self.assertTrue(all(row[1] == "(redacted)" for row in thread_rows))
        self.assertTrue(all(str(row[1]).startswith("project-") for row in project_rows))
        self.assertTrue(all(row[2] == "" for row in project_rows))

    def test_summary_includes_pricing_metadata(self):
        summary = tracker.aggregate_threads(tracker.demo_threads())

        self.assertEqual(summary["pricing"]["source_date"], "2026-05-29")
        self.assertIn("codex-rate-card", summary["pricing"]["codex_rate_card_url"])

    def test_claude_jsonl_parser_sums_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_home = Path(tmp) / ".claude"
            project_dir = claude_home / "projects" / "C--Projects-demo"
            project_dir.mkdir(parents=True)
            transcript = project_dir / "session.jsonl"
            rows = [
                {
                    "timestamp": "2026-05-02T09:00:00Z",
                    "type": "user",
                    "sessionId": "claude-session-1",
                    "cwd": "C:\\Projects\\demo",
                },
                {
                    "timestamp": "2026-05-02T09:00:10Z",
                    "type": "assistant",
                    "sessionId": "claude-session-1",
                    "cwd": "C:\\Projects\\demo",
                    "aiTitle": "Demo Claude work",
                    "message": {
                        "role": "assistant",
                        "model": "claude-sonnet-4-6",
                        "usage": {
                            "input_tokens": 10,
                            "cache_creation_input_tokens": 5,
                            "cache_read_input_tokens": 3,
                            "output_tokens": 2,
                        },
                    },
                },
            ]
            transcript.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            threads = tracker.load_claude_threads(claude_home)

            self.assertEqual(len(threads), 1)
            self.assertEqual(threads[0]["app"], "claude")
            self.assertEqual(threads[0]["model"], "claude-sonnet-4-6")
            self.assertEqual(threads[0]["usage"]["input_tokens"], 18)
            self.assertEqual(threads[0]["usage"]["cached_input_tokens"], 3)
            self.assertEqual(threads[0]["usage"]["output_tokens"], 2)
            self.assertEqual(threads[0]["usage"]["total_tokens"], 20)

    def test_cursor_parser_reads_ai_tracking_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ai-code-tracking.db"
            con = sqlite3.connect(db)
            con.execute(
                """
                create table ai_code_hashes (
                  hash text, source text, fileExtension text, fileName text,
                  requestId text, conversationId text, timestamp integer,
                  model text, createdAt integer
                )
                """
            )
            con.execute("create table conversation_summaries (conversationId text, title text)")
            con.execute("insert into conversation_summaries values (?, ?)", ("conv1", "Cursor demo"))
            base_ms = int(datetime(2026, 5, 2, 10, tzinfo=timezone.utc).timestamp() * 1000)
            con.executemany(
                "insert into ai_code_hashes values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("h1", "composer", "py", "a.py", "req1", "conv1", base_ms, "composer-2.5", base_ms),
                    ("h2", "composer", "py", "b.py", "req2", "conv1", base_ms + 60_000, "composer-2.5", base_ms + 60_000),
                ],
            )
            con.commit()
            con.close()

            threads = tracker.load_cursor_threads(db)

            self.assertEqual(len(threads), 1)
            self.assertEqual(threads[0]["app"], "cursor")
            self.assertEqual(threads[0]["title"], "Cursor demo")
            self.assertEqual(threads[0]["event_count"], 2)
            self.assertEqual(threads[0]["request_count"], 2)
            self.assertEqual(threads[0]["usage"]["total_tokens"], 0)

    def test_source_filter_accepts_all(self):
        self.assertEqual(tracker.parse_source_filter("all"), {"codex", "claude", "cursor"})


if __name__ == "__main__":
    unittest.main()
