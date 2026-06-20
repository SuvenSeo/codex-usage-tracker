import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import codex_app_tracker as tracker
import codex_usage_tracker_gui as gui_launcher


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

    def test_serve_subcommand_accepts_local_server_options(self):
        parser = tracker.build_parser()
        args = parser.parse_args(["--sources", "all", "serve", "--port", "0", "--refresh-seconds", "5", "--no-open"])

        self.assertEqual(args.command, "serve")
        self.assertEqual(args.port, 0)
        self.assertEqual(args.refresh_seconds, 5)
        self.assertTrue(args.no_open)
        self.assertIs(args.func, tracker.command_serve)

    def test_billing_subcommand_status_does_not_fetch_by_default(self):
        parser = tracker.build_parser()
        args = parser.parse_args(["billing", "--format", "json"])

        self.assertEqual(args.command, "billing")
        self.assertFalse(args.fetch)
        self.assertIs(args.func, tracker.command_billing)

    def test_packaged_gui_launcher_defaults_to_all_sources(self):
        args = gui_launcher.launcher_args([])

        self.assertEqual(args, ["--sources", "all", "gui"])

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
        self.assertIn("All apps lifetime tokens", metrics)
        self.assertEqual(metrics["Threads"], "5")
        self.assertTrue(model["tables"]["sources"]["rows"])
        self.assertTrue(model["tables"]["daily"]["rows"])
        self.assertTrue(model["tables"]["projects"]["rows"])
        self.assertTrue(model["tables"]["models"]["rows"])
        self.assertTrue(model["tables"]["threads"]["rows"])
        self.assertTrue(model["tables"]["alerts"]["rows"])
        self.assertTrue(model["tables"]["billing"]["rows"])
        self.assertTrue(model["tables"]["providers"]["rows"])
        self.assertTrue(model["provider_summaries"])
        cursor_row = next(row for row in model["provider_summaries"] if row["app_key"] == "cursor")
        self.assertGreater(cursor_row["lifetime_tokens"], 0)
        self.assertEqual(model["pricing"]["source_date"], tracker.PRICING_SOURCE_DATE)

    def test_gui_view_model_caps_large_tables(self):
        threads = [
            {
                "thread_id": f"thread-{index}",
                "title": f"Thread {index}",
                "cwd": f"/tmp/project-{index % 3}",
                "project": f"project-{index % 3}",
                "app": "codex",
                "source": "codex",
                "model": "gpt-5.4",
                "reasoning_effort": "",
                "cli_version": "",
                "path": f"/tmp/thread-{index}.jsonl",
                "line_count": 1,
                "event_count": 1,
                "request_count": 1,
                "started_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
                "ended_at": datetime(2026, 6, 20, tzinfo=timezone.utc),
                "usage": {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 10},
                "daily_usage": {"2026-06-20": {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 10}},
                "event_timestamps": [datetime(2026, 6, 20, tzinfo=timezone.utc)],
                "active_seconds": 60,
                "active_daily": {"2026-06-20": 60},
                "tool_counts": {},
                "estimated_codex_credits": 1.0,
                "estimated_api_usd_equiv": 0.1,
            }
            for index in range(500)
        ]
        summary = tracker.aggregate_threads(threads)
        model = tracker.build_gui_view_model(threads, summary)

        self.assertEqual(len(model["tables"]["threads"]["rows"]), tracker.GUI_TABLE_ROW_LIMITS["threads"])
        self.assertEqual(model["truncated_tables"]["threads"], (tracker.GUI_TABLE_ROW_LIMITS["threads"], 500))

    def test_gui_brand_helpers_resolve_app_keys(self):
        import gui_visuals

        self.assertEqual(gui_visuals.app_key_from_label("Codex"), "codex")
        self.assertEqual(gui_visuals.app_key_from_label("Claude Code"), "claude")
        self.assertEqual(gui_visuals.app_key_from_label("Cursor"), "cursor")
        self.assertIn("accent", gui_visuals.brand_for_app("codex"))
        assets_dir = gui_visuals.brand_assets_dir()
        for app_key in ("codex", "claude", "cursor"):
            path = gui_visuals.brand_asset_path(app_key)
            self.assertIsNotNone(path)
            assert path is not None
            self.assertTrue(path.exists(), f"missing bundled brand asset: {path}")
            self.assertTrue(str(path).startswith(str(assets_dir)))

    def test_brand_icon_manager_loads_png(self):
        import tkinter as tk

        import gui_visuals

        root = tk.Tk()
        root.withdraw()
        try:
            manager = gui_visuals.BrandIconManager(tk)
            photo = manager.photo("cursor", 32)
            self.assertIsNotNone(photo)
            assert photo is not None
            self.assertGreater(int(photo.width()), 0)
        finally:
            root.destroy()

    def test_dashboard_html_uses_dark_theme_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            threads = tracker.demo_threads()
            summary = tracker.aggregate_threads(threads)
            paths = tracker.write_reports(threads, summary, Path(tmp))
            dashboard = paths["dashboard_html"].read_text(encoding="utf-8")

        self.assertIn("color-scheme: dark", dashboard)
        self.assertIn("--bg: #101114", dashboard)
        self.assertIn("--panel: #181b20", dashboard)
        self.assertIn("class=\"table-nav\"", dashboard)
        self.assertIn("id=\"search-status\"", dashboard)
        self.assertIn("@keyframes card-rise", dashboard)
        self.assertIn("@media (prefers-reduced-motion: reduce)", dashboard)
        self.assertIn("Provider Comparison", dashboard)
        self.assertIn("Official Billing Connectors", dashboard)
        self.assertIn("Budget & Signals", dashboard)

    def test_budget_alerts_warn_when_threshold_is_exceeded(self):
        threads = tracker.demo_threads()
        summary = tracker.aggregate_threads(threads)

        alerts = tracker.build_usage_alerts(summary, {"daily_tokens": 1.0, "monthly_usd": 0.01})

        self.assertTrue(any(alert["severity"] == "risk" for alert in alerts))

    def test_billing_connectors_are_status_only_without_fetch(self):
        rows = tracker.build_billing_connectors(fetch=False)

        self.assertEqual({row["provider"] for row in rows}, {"OpenAI", "Anthropic", "Cursor"})
        self.assertTrue(all("env_var" in row for row in rows))

    def test_gui_theme_is_dark_default(self):
        self.assertEqual(tracker.DARK_THEME["mode"], "dark")
        self.assertEqual(tracker.DARK_THEME["bg"], "#0b0c0f")
        self.assertIn("codex", tracker.GUI_APP_ACCENTS)
        self.assertEqual(tracker.GUI_CHART_COLORS["daily"], tracker.DARK_THEME["blue"])

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

        self.assertEqual(summary["pricing"]["source_date"], "2026-05-31")
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
                        "id": "msg_1",
                        "role": "assistant",
                        "model": "claude-sonnet-4-6",
                        "usage": {
                            "input_tokens": 10,
                            "cache_creation_input_tokens": 5,
                            "cache_creation": {"ephemeral_1h_input_tokens": 5, "ephemeral_5m_input_tokens": 0},
                            "cache_read_input_tokens": 3,
                            "output_tokens": 2,
                        },
                    },
                },
                {
                    "timestamp": "2026-05-02T09:00:11Z",
                    "type": "assistant",
                    "sessionId": "claude-session-1",
                    "cwd": "C:\\Projects\\demo",
                    "message": {
                        "id": "msg_1",
                        "role": "assistant",
                        "model": "claude-sonnet-4-6",
                        "usage": {
                            "input_tokens": 10,
                            "cache_creation_input_tokens": 5,
                            "cache_creation": {"ephemeral_1h_input_tokens": 5, "ephemeral_5m_input_tokens": 0},
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
            self.assertEqual(threads[0]["usage"]["cache_creation_1h_input_tokens"], 5)
            self.assertEqual(threads[0]["usage"]["output_tokens"], 2)
            self.assertEqual(threads[0]["usage"]["total_tokens"], 20)
            self.assertGreater(threads[0]["estimated_api_usd_equiv"], 0)

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

            threads = tracker.load_cursor_threads(
                db,
                cursor_state_db=Path(tmp) / "missing-state.vscdb",
                cursor_projects_home=Path(tmp) / "missing-projects",
            )

            self.assertEqual(len(threads), 1)
            self.assertEqual(threads[0]["app"], "cursor")
            self.assertEqual(threads[0]["title"], "Cursor demo")
            self.assertEqual(threads[0]["event_count"], 2)
            self.assertEqual(threads[0]["request_count"], 2)
            self.assertEqual(threads[0]["usage"]["total_tokens"], 0)

    def test_cursor_transcript_parser_estimates_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects_home = Path(tmp) / "projects" / "c-demo-project" / "agent-transcripts" / "conv-123"
            projects_home.mkdir(parents=True)
            transcript = projects_home / "conv-123.jsonl"
            rows = [
                {
                    "role": "user",
                    "message": {
                        "content": [
                            {"type": "text", "text": "<user_query>Fix the parser</user_query>"},
                        ],
                    },
                },
                {
                    "role": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "I'll update the parser now."},
                            {"type": "tool_use", "name": "Read", "input": {"path": "codex_app_tracker.py"}},
                        ],
                    },
                    "model": "claude-sonnet-4-6",
                },
            ]
            transcript.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            threads = tracker.load_cursor_threads(
                Path(tmp) / "missing-tracking.db",
                cursor_state_db=Path(tmp) / "missing-state.vscdb",
                cursor_projects_home=Path(tmp) / "projects",
            )

            self.assertEqual(len(threads), 1)
            self.assertEqual(threads[0]["app"], "cursor")
            self.assertEqual(threads[0]["source"], "cursor_transcript")
            self.assertGreater(threads[0]["usage"]["input_tokens"], 0)
            self.assertGreater(threads[0]["usage"]["output_tokens"], 0)
            self.assertGreaterEqual(threads[0]["usage"]["cached_input_tokens"], 0)
            self.assertGreater(threads[0]["estimated_api_usd_equiv"], 0)
            self.assertEqual(threads[0]["tool_counts"].get("Read"), 1)

    def test_cursor_daily_stats_reads_state_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.vscdb"
            con = sqlite3.connect(db)
            con.execute("create table ItemTable (key text, value text)")
            con.execute(
                "insert into ItemTable values (?, ?)",
                (
                    "aiCodeTracking.dailyStats.v1.5.2026-02-19",
                    json.dumps({
                        "date": "2026-02-19",
                        "tabSuggestedLines": 4,
                        "tabAcceptedLines": 2,
                        "composerSuggestedLines": 100,
                        "composerAcceptedLines": 80,
                    }),
                ),
            )
            con.commit()
            con.close()

            rows = tracker.read_cursor_daily_stats(db)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["date"], "2026-02-19")
            self.assertEqual(rows[0]["composer_suggested_lines"], 100)
            self.assertEqual(rows[0]["composer_accepted_lines"], 80)

    def test_source_filter_accepts_all(self):
        self.assertEqual(tracker.parse_source_filter("all"), {"codex", "claude", "cursor"})

    def test_source_audit_command_is_registered(self):
        parser = tracker.build_parser()
        args = parser.parse_args(["source-audit", "--format", "markdown"])

        self.assertEqual(args.command, "source-audit")
        self.assertEqual(args.format, "markdown")
        self.assertIs(args.func, tracker.command_source_audit)

    def test_rollout_missing_optional_fields_degrades_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / ".codex"
            rollout_dir = codex_home / "sessions" / "2026" / "05" / "15"
            rollout_dir.mkdir(parents=True)
            rollout = rollout_dir / "rollout-2026-05-15T08-00-00-019minimal-0000-0000-0000-000000000001.jsonl"
            rows = [
                {
                    "timestamp": "2026-05-15T08:00:00Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "019minimal-0000-0000-0000-000000000001",
                        "source": "codex_desktop",
                    },
                },
                {
                    "timestamp": "2026-05-15T08:00:10Z",
                    "type": "event_msg",
                    "payload": {
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 200,
                                "output_tokens": 30,
                                "total_tokens": 230,
                            }
                        }
                    },
                },
            ]
            rollout.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            threads = tracker.load_threads(codex_home)
            summary = tracker.aggregate_threads(threads)

            self.assertEqual(len(threads), 1)
            self.assertEqual(threads[0]["project"], "(unknown)")
            self.assertEqual(threads[0]["usage"]["cached_input_tokens"], 0)
            self.assertEqual(threads[0]["usage"]["reasoning_output_tokens"], 0)
            self.assertEqual(summary["usage"]["total_tokens"], 230)

    def test_rollout_no_turn_context_row_degrades_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / ".codex"
            rollout_dir = codex_home / "sessions" / "2026" / "05" / "20"
            rollout_dir.mkdir(parents=True)
            rollout = rollout_dir / "rollout-2026-05-20T09-00-00-019noturn-0000-0000-0000-000000000002.jsonl"
            rows = [
                {
                    "timestamp": "2026-05-20T09:00:00Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "019noturn-0000-0000-0000-000000000002",
                        "cwd": "C:\\Projects\\no-turn-app",
                        "source": "codex_desktop",
                    },
                },
                {
                    "timestamp": "2026-05-20T09:00:10Z",
                    "type": "event_msg",
                    "payload": {
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 500,
                                "cached_input_tokens": 100,
                                "output_tokens": 80,
                                "reasoning_output_tokens": 0,
                                "total_tokens": 580,
                            }
                        }
                    },
                },
            ]
            rollout.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            threads = tracker.load_threads(codex_home)
            summary = tracker.aggregate_threads(threads)

            self.assertEqual(len(threads), 1)
            self.assertEqual(threads[0]["project"], "no-turn-app")
            self.assertEqual(threads[0]["model"], "")
            self.assertEqual(summary["usage"]["total_tokens"], 580)


if __name__ == "__main__":
    unittest.main()
