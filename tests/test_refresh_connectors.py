import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

from skill_paths import configure_paths

configure_paths()

from refresh_connectors import (
    ConnectorRefreshError,
    refresh_direct_connector_snapshots,
)


class FakeServices:
    def __init__(
        self,
        linear_email: str = "owner@example.com",
        google_email: str = "owner@example.com",
    ) -> None:
        self.linear_email = linear_email
        self.google_email = google_email
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if url.endswith("api.linear.app/graphql"):
            payload = json.loads(body)
            if "LedgerIdentity" in payload["query"]:
                return {
                    "data": {
                        "viewer": {
                            "id": "linear-user",
                            "name": "Owner",
                            "email": self.linear_email,
                        },
                        "teams": {
                            "nodes": [{"id": "team-1", "name": "Ledger", "key": "LED"}]
                        },
                    }
                }
            return {
                "data": {
                    "team": {
                        "issues": {
                            "nodes": [
                                {
                                    "id": "issue-1",
                                    "identifier": "LED-1",
                                    "title": "Finish weekly review",
                                    "priority": 2,
                                    "estimate": 3,
                                    "createdAt": "2026-08-01T00:00:00Z",
                                    "updatedAt": "2026-08-19T00:00:00Z",
                                    "completedAt": "2026-08-19T00:00:00Z",
                                    "canceledAt": None,
                                    "state": {"name": "Done", "type": "completed"},
                                    "cycle": {
                                        "id": "cycle-1",
                                        "name": "Week 34",
                                        "number": 34,
                                        "startsAt": "2026-08-17T00:00:00Z",
                                        "endsAt": "2026-08-24T00:00:00Z",
                                    },
                                }
                            ],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }
        if url.endswith("oauth2.googleapis.com/token"):
            form = parse_qs(body.decode("utf-8"))
            self.assert_form(form)
            return {"access_token": "temporary-google-access"}
        if url.endswith("users/me/calendarList/primary"):
            return {"id": self.google_email, "summary": "Owner"}
        if "/calendars/primary/events?" in url:
            return {
                "items": [
                    {
                        "id": "event-1",
                        "summary": "Research focus",
                        "eventType": "focusTime",
                        "status": "confirmed",
                        "start": {"dateTime": "2026-08-18T09:00:00+08:00"},
                        "end": {"dateTime": "2026-08-18T11:00:00+08:00"},
                    }
                ]
            }
        raise AssertionError(f"Unexpected request: {method} {url}")

    def assert_form(self, form):
        if form != {
            "client_id": ["google-client-id"],
            "client_secret": ["google-client-credential"],
            "refresh_token": ["google-refresh-credential"],
            "grant_type": ["refresh_token"],
        }:
            raise AssertionError(f"Unexpected OAuth form fields: {sorted(form)}")


def make_config(root: Path) -> dict:
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    return {
        "_config_root": str(root),
        "timezone": "Asia/Shanghai",
        "identity": {
            "person_id": "owner",
            "git_names": ["Owner"],
        },
        "repositories": [{"name": "repo", "path": str(repo)}],
        "integrations": {
            "linear": {
                "enabled": True,
                "auth_mode": "direct",
                "team": "Ledger",
                "snapshot": ".ledger/inbox/linear.json",
                "expected_user": "owner@example.com",
                "credential_ref": "env:LINEAR_CREDENTIAL",
            },
            "google_calendar": {
                "enabled": True,
                "auth_mode": "direct",
                "calendar": "primary",
                "snapshot": ".ledger/inbox/google-calendar.json",
                "expected_user": "owner@example.com",
                "client_id_ref": "env:GOOGLE_CLIENT_ID",
                "client_credential_ref": "env:GOOGLE_CLIENT_CREDENTIAL",
                "refresh_credential_ref": "env:GOOGLE_REFRESH_CREDENTIAL",
            },
        },
    }


class ConnectorRefreshTests(unittest.TestCase):
    def test_refreshes_verified_snapshots_without_persisting_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = make_config(root)
            services = FakeServices()
            secrets = {
                "env:LINEAR_CREDENTIAL": "linear-secret-value",
                "env:GOOGLE_CLIENT_ID": "google-client-id",
                "env:GOOGLE_CLIENT_CREDENTIAL": "google-client-credential",
                "env:GOOGLE_REFRESH_CREDENTIAL": "google-refresh-credential",
            }
            results = refresh_direct_connector_snapshots(
                config,
                date(2026, 8, 17),
                date(2026, 8, 24),
                request_json=services,
                resolver=secrets.__getitem__,
            )
            self.assertEqual(
                [item["name"] for item in results], ["linear", "google_calendar"]
            )
            linear = json.loads(
                (root / ".ledger/inbox/linear.json").read_text(encoding="utf-8")
            )
            calendar = json.loads(
                (root / ".ledger/inbox/google-calendar.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(linear["issues"][0]["completed"])
            self.assertEqual(calendar["events"][0]["kind"], "focus")
            persisted = json.dumps({"linear": linear, "calendar": calendar})
            for secret in secrets.values():
                self.assertNotIn(secret, persisted)
            self.assertNotIn("temporary-google-access", persisted)

    def test_rejects_unexpected_linear_account_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = make_config(root)
            config["integrations"]["google_calendar"]["enabled"] = False
            services = FakeServices(linear_email="someone-else@example.com")
            with self.assertRaisesRegex(ConnectorRefreshError, "expected_user"):
                refresh_direct_connector_snapshots(
                    config,
                    date(2026, 8, 17),
                    date(2026, 8, 24),
                    request_json=services,
                    resolver=lambda reference: "linear-secret-value",
                )
            self.assertFalse((root / ".ledger/inbox/linear.json").exists())

    def test_does_not_write_partial_batch_when_second_account_is_wrong(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = make_config(root)
            services = FakeServices(google_email="someone-else@example.com")
            secrets = {
                "env:LINEAR_CREDENTIAL": "linear-secret-value",
                "env:GOOGLE_CLIENT_ID": "google-client-id",
                "env:GOOGLE_CLIENT_CREDENTIAL": "google-client-credential",
                "env:GOOGLE_REFRESH_CREDENTIAL": "google-refresh-credential",
            }
            with self.assertRaisesRegex(ConnectorRefreshError, "expected_user"):
                refresh_direct_connector_snapshots(
                    config,
                    date(2026, 8, 17),
                    date(2026, 8, 24),
                    request_json=services,
                    resolver=secrets.__getitem__,
                )
            self.assertFalse((root / ".ledger/inbox/linear.json").exists())
            self.assertFalse((root / ".ledger/inbox/google-calendar.json").exists())


if __name__ == "__main__":
    unittest.main()
