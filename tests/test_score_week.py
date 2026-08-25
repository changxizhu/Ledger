import json
import unittest
from pathlib import Path

from skill_paths import configure_paths

configure_paths()

import score_week

ROOT = Path(__file__).resolve().parents[1]


class WeeklyIndexTests(unittest.TestCase):
    def test_example_score(self) -> None:
        data = json.loads((ROOT / "examples/weekly-input.json").read_text())
        result = score_week.calculate_weekly_index(data)
        self.assertEqual(result["weekly_index"]["score"], 77.5)
        self.assertEqual(result["weekly_index"]["components"]["evidence"], 100.0)

    def test_focus_time_is_capped(self) -> None:
        data = {
            "target_focus_hours": 10,
            "actual_focus_hours": 20,
            "tasks": [{"completed": True}],
            "themes": [{"progress": 1, "evidence_count": 1}],
        }
        result = score_week.calculate_weekly_index(data)
        self.assertEqual(result["weekly_index"]["score"], 100.0)

    def test_invalid_weights_fail(self) -> None:
        with self.assertRaises(ValueError):
            score_week.calculate_weekly_index({}, {"plan": 0.5})

    def test_cancelled_tasks_do_not_lower_plan_score(self) -> None:
        data = {
            "target_focus_hours": 1,
            "actual_focus_hours": 1,
            "tasks": [
                {"completed": True},
                {"completed": False, "cancelled": True},
            ],
            "themes": [{"progress": 1, "evidence_count": 1}],
        }
        result = score_week.calculate_weekly_index(data)
        self.assertEqual(result["weekly_index"]["components"]["plan"], 100.0)

    def test_missing_plan_and_focus_are_excluded_not_scored_as_zero(self) -> None:
        data = {
            "target_focus_hours": None,
            "actual_focus_hours": 0,
            "plan_status": {"status": "unavailable", "reason": "No weekly plan."},
            "tasks": [],
            "themes": [{"progress": 0.75, "evidence_count": 1}],
        }
        result = score_week.calculate_weekly_index(data)["weekly_index"]
        self.assertIsNone(result["components"]["plan"])
        self.assertIsNone(result["components"]["focus"])
        self.assertEqual(result["score_coverage"], 55.0)
        self.assertEqual(result["score"], 79.5)


if __name__ == "__main__":
    unittest.main()
