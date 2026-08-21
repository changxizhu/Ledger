import unittest

from scripts import ledger_common


class LedgerCommonTests(unittest.TestCase):
    def test_week_period_is_monday_to_monday(self) -> None:
        start, end = ledger_common.week_period("2026-W34")
        self.assertEqual(start.isoformat(), "2026-08-17")
        self.assertEqual(end.isoformat(), "2026-08-24")

    def test_config_rejects_secrets(self) -> None:
        config = {
            "_config_root": "/tmp",
            "timezone": "Asia/Shanghai",
            "identity": {"person_id": "owner", "git_names": ["Owner"]},
            "repositories": [{"name": "repo", "path": "/missing"}],
            "integrations": {"linear": {"api_token": "not-allowed"}},
        }
        errors = ledger_common.validate_config(config, check_paths=False)
        self.assertTrue(any("api_token" in error for error in errors))

    def test_direct_connector_requires_references_not_raw_credentials(self) -> None:
        config = {
            "_config_root": "/tmp",
            "timezone": "Asia/Shanghai",
            "identity": {"person_id": "owner", "git_names": ["Owner"]},
            "repositories": [{"name": "repo", "path": "/missing"}],
            "integrations": {
                "linear": {
                    "enabled": True,
                    "auth_mode": "direct",
                    "team": "Ledger",
                    "snapshot": ".ledger/inbox/linear.json",
                    "expected_user": "owner@example.com",
                    "credential_ref": "raw-value",
                }
            },
        }
        errors = ledger_common.validate_config(config, check_paths=False)
        self.assertTrue(any("credential_ref" in error for error in errors))
        self.assertTrue(any("env: or keychain:" in error for error in errors))

    def test_config_accepts_only_the_shared_interaction_contract(self) -> None:
        config = {
            "_config_root": "/tmp",
            "timezone": "Asia/Shanghai",
            "identity": {"person_id": "owner", "git_names": ["Owner"]},
            "repositories": [{"name": "repo", "path": "/missing"}],
            "interaction_sources": [
                {
                    "name": "native-codex",
                    "kind": "codex_jsonl",
                    "path": "/tmp/interactions.jsonl",
                }
            ],
        }
        errors = ledger_common.validate_config(config, check_paths=False)
        self.assertIn("Unsupported interaction source kind in native-codex", errors)

    def test_config_validates_device_export_settings(self) -> None:
        config = {
            "_config_root": "/tmp",
            "timezone": "Asia/Shanghai",
            "identity": {"person_id": "owner", "git_names": ["Owner"]},
            "repositories": [{"name": "repo", "path": "/missing"}],
            "interaction_export": {
                "device_id": "macbook-a",
                "output_dir": "/tmp/interactions",
                "providers": ["codex", "unknown"],
                "lookback_days": 0,
            },
        }
        errors = ledger_common.validate_config(config, check_paths=False)
        self.assertIn(
            "interaction_export.providers contains an unsupported provider", errors
        )
        self.assertIn(
            "interaction_export.lookback_days must be an integer of at least 1", errors
        )

    def test_weekly_input_reports_unknown_required_source(self) -> None:
        data = {
            "profile_id": "owner",
            "week": "2026-W34",
            "period": {"start": "2026-08-17", "end": "2026-08-24"},
            "target_focus_hours": 10,
            "actual_focus_hours": 8,
            "tasks": [],
            "themes": [{"name": "Work", "progress": 0.5, "evidence_count": 1}],
            "source_status": [
                {"name": "linear", "required": True, "status": "unavailable"}
            ],
        }
        errors = ledger_common.validate_weekly_input(data)
        self.assertIn("Required sources unavailable: linear", errors)


if __name__ == "__main__":
    unittest.main()
