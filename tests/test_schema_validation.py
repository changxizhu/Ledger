import json
import unittest
from pathlib import Path

from scripts.schema_validation import SCHEMA_DIRECTORY, _validator, contract_errors


ROOT = Path(__file__).resolve().parents[1]


class SchemaValidationTests(unittest.TestCase):
    def test_committed_schemas_are_valid(self) -> None:
        self.assertEqual(
            sorted(path.name for path in SCHEMA_DIRECTORY.glob("*.schema.json")),
            [
                "evidence-bundle.schema.json",
                "interaction-event.schema.json",
                "weekly-input.schema.json",
            ],
        )
        _validator("evidence-bundle")
        _validator("interaction-event")
        _validator("weekly-input")

    def test_weekly_example_matches_contract(self) -> None:
        data = json.loads((ROOT / "examples/weekly-input.json").read_text())
        self.assertEqual(contract_errors("weekly-input", data), [])

    def test_weekly_contract_rejects_incomplete_records(self) -> None:
        data = json.loads((ROOT / "examples/weekly-input.json").read_text())
        del data["tasks"][0]["completed"]
        data["themes"][0]["progress"] = 1.5
        errors = contract_errors("weekly-input", data)
        self.assertTrue(any("completed" in error for error in errors))
        self.assertTrue(any("progress" in error for error in errors))

    def test_weekly_contract_requires_theme_evidence_references(self) -> None:
        data = json.loads((ROOT / "examples/weekly-input.json").read_text())
        del data["themes"][0]["evidence_refs"]
        errors = contract_errors("weekly-input", data)
        self.assertTrue(any("evidence_refs" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
