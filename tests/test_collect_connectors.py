import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from skill_paths import configure_paths

configure_paths()

from collect_connectors import collect_connector_snapshots


class ConnectorCollectionTests(unittest.TestCase):
    def test_loads_approved_snapshots_and_calculates_focus_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            period = {"start": "2026-08-17", "end": "2026-08-24"}
            linear_path = root / "linear.json"
            calendar_path = root / "calendar.json"
            linear_path.write_text(
                json.dumps(
                    {
                        "source": "linear",
                        "captured_at": "2026-08-24T00:00:00Z",
                        "profile_id": "owner",
                        "period": period,
                        "issues": [{"id": "LED-1", "title": "Task"}],
                    }
                ),
                encoding="utf-8",
            )
            calendar_path.write_text(
                json.dumps(
                    {
                        "source": "google_calendar",
                        "captured_at": "2026-08-24T00:00:00Z",
                        "profile_id": "owner",
                        "period": period,
                        "events": [
                            {
                                "id": "focus-1",
                                "kind": "focus",
                                "start": "2026-08-18T09:00:00+08:00",
                                "end": "2026-08-18T11:00:00+08:00",
                            },
                            {
                                "id": "focus-1",
                                "kind": "focus",
                                "start": "2026-08-18T09:00:00+08:00",
                                "end": "2026-08-18T11:00:00+08:00",
                            },
                            {
                                "id": "focus-boundary",
                                "kind": "focus",
                                "start": "2026-08-16T23:00:00+08:00",
                                "end": "2026-08-17T01:00:00+08:00",
                            },
                            {
                                "id": "outside",
                                "kind": "focus",
                                "start": "2026-08-25T09:00:00+08:00",
                                "end": "2026-08-25T10:00:00+08:00",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "_config_root": str(root),
                "timezone": "Asia/Shanghai",
                "identity": {"person_id": "owner"},
                "integrations": {
                    "linear": {"enabled": True, "snapshot": str(linear_path)},
                    "google_calendar": {
                        "enabled": True,
                        "snapshot": str(calendar_path),
                    },
                },
            }
            result = collect_connector_snapshots(
                config, date(2026, 8, 17), date(2026, 8, 24)
            )
            self.assertEqual(result["calendar_focus_hours"], 3.0)
            self.assertEqual(
                [status["status"] for status in result["statuses"]],
                ["available", "available"],
            )

    def test_rejects_snapshot_for_another_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "linear.json"
            snapshot.write_text(
                json.dumps(
                    {
                        "source": "linear",
                        "captured_at": "2026-08-24T00:00:00Z",
                        "profile_id": "someone-else",
                        "period": {"start": "2026-08-17", "end": "2026-08-24"},
                        "issues": [],
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "_config_root": str(root),
                "timezone": "Asia/Shanghai",
                "identity": {"person_id": "owner"},
                "integrations": {
                    "linear": {"enabled": True, "snapshot": str(snapshot)}
                },
            }
            result = collect_connector_snapshots(
                config, date(2026, 8, 17), date(2026, 8, 24)
            )
            self.assertEqual(result["statuses"][0]["status"], "error")
            self.assertIn("profile_id", result["statuses"][0]["detail"])

    def test_rejects_snapshot_for_unexpected_service_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "linear.json"
            snapshot.write_text(
                json.dumps(
                    {
                        "source": "linear",
                        "captured_at": "2026-08-24T00:00:00Z",
                        "profile_id": "owner",
                        "period": {"start": "2026-08-17", "end": "2026-08-24"},
                        "account": {"email": "someone-else@example.com"},
                        "issues": [],
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "_config_root": str(root),
                "timezone": "Asia/Shanghai",
                "identity": {"person_id": "owner"},
                "integrations": {
                    "linear": {
                        "enabled": True,
                        "snapshot": str(snapshot),
                        "expected_user": "owner@example.com",
                    }
                },
            }
            result = collect_connector_snapshots(
                config, date(2026, 8, 17), date(2026, 8, 24)
            )
            self.assertEqual(result["statuses"][0]["status"], "error")
            self.assertIn("expected_user", result["statuses"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
