import unittest

from skill_paths import configure_paths

configure_paths()

from aggregate_month import aggregate
from build_proposals import build_proposals
from build_html_report import render_report
from sanitize_export import sanitize_week


class OutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.record = {
            "week": "2026-W34",
            "period": {"start": "2026-08-17", "end": "2026-08-24"},
            "tasks": [
                {"title": "Private completed task", "completed": True},
                {"title": "Private open task", "completed": False},
            ],
            "themes": [
                {"name": "Private theme", "public_name": "Research", "progress": 0.75}
            ],
            "weekly_index": {
                "score": 72.0,
                "components": {"plan": 50.0, "progress": 75.0},
            },
        }

    def test_public_export_removes_private_titles(self) -> None:
        result = sanitize_week(self.record)
        rendered = str(result)
        self.assertNotIn("Private open task", rendered)
        self.assertNotIn("Private theme", rendered)
        self.assertEqual(result["themes"][0]["name"], "Theme 1")

        named_result = sanitize_week(self.record, include_public_names=True)
        self.assertEqual(named_result["themes"][0]["name"], "Research")

    def test_proposals_contain_one_calendar_reminder(self) -> None:
        self.record["next_plan"] = {
            "tasks": [{"title": "Private open task", "weight": 1}]
        }
        result = build_proposals(self.record, "Asia/Shanghai")
        self.assertEqual(result["google_calendar"]["title"], "Review weekly study plan")
        self.assertEqual(len(result["linear"]["tasks"]), 1)
        self.assertTrue(result["linear"]["tasks"][0]["ledger_key"].startswith("weekly:"))
        self.assertTrue(
            result["google_calendar"]["ledger_key"].startswith("weekly-reminder:")
        )
        self.assertTrue(result["approval_required"])

    def test_proposals_exclude_cancelled_tasks(self) -> None:
        self.record["tasks"].append(
            {"title": "Cancelled task", "completed": False, "cancelled": True}
        )
        result = build_proposals(self.record, "Asia/Shanghai")
        titles = [task["title"] for task in result["linear"]["tasks"]]
        self.assertNotIn("Cancelled task", titles)

    def test_private_report_shows_theme_evidence_references(self) -> None:
        self.record["weekly_index"]["components"].update(
            {"focus": 80.0, "evidence": 100.0}
        )
        self.record["themes"][0]["evidence_refs"] = [
            {
                "event_type": "git_commit",
                "source": "research-code",
                "commit": "0123456789abcdef",
                "path": "result.md",
                "reason": "Records the reviewed result.",
            }
        ]
        report = render_report(self.record)
        self.assertIn("<!doctype html>", report)
        self.assertIn('class="score-gauge"', report)
        self.assertIn('class="theme-list"', report)
        self.assertIn('<details class="evidence-details">', report)
        self.assertIn("17-23 Aug 2026", report)
        self.assertIn("0123456789abcdef", report)
        self.assertIn("result.md", report)
        self.assertIn("Records the reviewed result.", report)

    def test_private_report_escapes_private_text(self) -> None:
        self.record["weekly_index"]["components"].update(
            {"focus": 80.0, "evidence": 100.0}
        )
        self.record["themes"][0]["summary"] = "<script>alert('x')</script>"
        report = render_report(self.record)
        self.assertNotIn("<script>alert", report)
        self.assertIn("&lt;script&gt;alert", report)

    def test_private_report_and_public_export_mark_missing_components(self) -> None:
        self.record["weekly_index"].update(
            {
                "score": 79.5,
                "components": {
                    "plan": None,
                    "progress": 75.0,
                    "focus": None,
                    "evidence": 100.0,
                },
                "component_status": {
                    "plan": "unavailable",
                    "progress": "available",
                    "focus": "unavailable",
                    "evidence": "available",
                },
                "score_coverage": 55.0,
                "weights": {
                    "plan": 0.3,
                    "progress": 0.45,
                    "focus": 0.15,
                    "evidence": 0.1,
                },
                "effective_weights": {
                    "plan": 0.0,
                    "progress": 0.818182,
                    "focus": 0.0,
                    "evidence": 0.181818,
                },
            }
        )
        report = render_report(self.record)
        public = sanitize_week(self.record)
        self.assertIn("Partial score", report)
        self.assertIn("N/A", report)
        self.assertEqual(public["components"]["plan"], None)
        self.assertEqual(public["score_coverage"], 55.0)

        self.record["plan_status"] = {
            "status": "unavailable",
            "reason": "No weekly plan.",
        }
        report = render_report(self.record)
        public = sanitize_week(self.record)
        self.assertIn("Weekly plan unavailable.", report)
        self.assertIsNone(public["completed_task_count"])
        self.assertIsNone(public["planned_task_count"])

    def test_monthly_aggregation_finds_recurring_carry_forward(self) -> None:
        second_week = {
            **self.record,
            "week": "2026-W35",
            "period": {"start": "2026-08-24", "end": "2026-08-31"},
        }
        result = aggregate([self.record, second_week], "2026-08")
        self.assertEqual(result["week_count"], 2)
        self.assertEqual(result["average_score"], 72.0)
        self.assertEqual(result["recurring_carry_forward"][0]["weeks"], 2)

    def test_monthly_aggregation_uses_latest_rerun(self) -> None:
        replacement = {
            **self.record,
            "weekly_index": {
                "score": 80.0,
                "components": {"plan": 100.0, "progress": 75.0},
            },
        }
        result = aggregate([self.record, replacement], "2026-08")
        self.assertEqual(result["week_count"], 1)
        self.assertEqual(result["average_score"], 80.0)

    def test_monthly_aggregation_treats_period_end_as_exclusive(self) -> None:
        august_only = {
            **self.record,
            "week": "2026-W36",
            "period": {"start": "2026-08-25", "end": "2026-09-01"},
        }
        result = aggregate([august_only], "2026-09")
        self.assertEqual(result["week_count"], 0)

    def test_monthly_aggregation_includes_a_week_that_crosses_month_start(self) -> None:
        crossing = {
            **self.record,
            "week": "2026-W35",
            "period": {"start": "2026-08-31", "end": "2026-09-07"},
        }
        result = aggregate([crossing], "2026-09")
        self.assertEqual(result["week_count"], 1)

    def test_monthly_aggregation_excludes_cancelled_carry_forward(self) -> None:
        self.record["tasks"].append(
            {"title": "Cancelled task", "completed": False, "cancelled": True}
        )
        second_week = {
            **self.record,
            "week": "2026-W35",
            "period": {"start": "2026-08-24", "end": "2026-08-31"},
        }
        result = aggregate([self.record, second_week], "2026-08")
        titles = [item["title"] for item in result["recurring_carry_forward"]]
        self.assertNotIn("Cancelled task", titles)


if __name__ == "__main__":
    unittest.main()
