import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("score_week", ROOT / "scripts/score_week.py")
assert SPEC and SPEC.loader
score_week = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(score_week)


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


if __name__ == "__main__":
    unittest.main()
