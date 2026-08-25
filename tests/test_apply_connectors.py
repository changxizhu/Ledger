import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from skill_paths import configure_paths

configure_paths()

from apply_connectors import ConnectorApplyError, apply_proposals


class FakeServices:
    def __init__(
        self, *, duplicate_events: bool = False, existing_event: bool = True
    ) -> None:
        self.linear_issues: list[dict] = []
        self.linear_creates = 0
        self.linear_updates = 0
        self.calendar_patches = 0
        self.calendar_inserts = 0
        event = {
            "id": "weekly-1",
            "summary": "Review weekly study plan",
            "description": "Old description",
            "status": "confirmed",
            "start": {"dateTime": "2026-08-31T10:00:00+08:00"},
            "end": {"dateTime": "2026-08-31T10:15:00+08:00"},
            "reminders": {"useDefault": False, "overrides": []},
            "extendedProperties": {"private": {"existing": "kept"}},
        }
        self.calendar_events = [event] if existing_event else []
        if duplicate_events and existing_event:
            self.calendar_events.append({**event, "id": "weekly-2"})

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> dict:
        if url.endswith("api.linear.app/graphql"):
            return self._linear(json.loads((body or b"{}").decode("utf-8")))
        if url.endswith("oauth2.googleapis.com/token"):
            return {"access_token": "google-access"}
        if url.endswith("/users/me/calendarList/primary"):
            return {"id": "owner@example.com", "summary": "owner@example.com"}
        if "/calendars/primary/events" in url:
            return self._calendar(method, url, body)
        raise AssertionError(f"Unexpected request: {method} {url}")

    def _linear(self, payload: dict) -> dict:
        query = payload["query"]
        variables = payload.get("variables", {})
        if "LedgerWriteIdentity" in query:
            return {
                "data": {
                    "viewer": {
                        "id": "owner-id",
                        "name": "Owner",
                        "email": "owner@example.com",
                    },
                    "teams": {
                        "nodes": [{"id": "team-id", "name": "Ledger", "key": "LED"}]
                    },
                }
            }
        if "LedgerWritePlan" in query:
            return {
                "data": {
                    "team": {
                        "states": {
                            "nodes": [
                                {"id": "todo-id", "name": "Todo", "type": "unstarted"}
                            ]
                        },
                        "issues": {
                            "nodes": self.linear_issues,
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        },
                    }
                }
            }
        if "LedgerCreateIssue" in query:
            self.linear_creates += 1
            issue = self._issue_from_input(
                variables["input"], f"issue-{self.linear_creates}", f"LED-{self.linear_creates}"
            )
            self.linear_issues.append(issue)
            return {
                "data": {
                    "issueCreate": {
                        "success": True,
                        "issue": {"id": issue["id"], "identifier": issue["identifier"]},
                    }
                }
            }
        if "LedgerUpdateIssue" in query:
            self.linear_updates += 1
            issue = next(item for item in self.linear_issues if item["id"] == variables["id"])
            issue.update(self._issue_from_input(variables["input"], issue["id"], issue["identifier"]))
            return {
                "data": {
                    "issueUpdate": {
                        "success": True,
                        "issue": {"id": issue["id"], "identifier": issue["identifier"]},
                    }
                }
            }
        if "LedgerVerifyIssue" in query:
            issue = next(item for item in self.linear_issues if item["id"] == variables["id"])
            return {"data": {"issue": issue}}
        raise AssertionError("Unexpected Linear operation")

    @staticmethod
    def _issue_from_input(input_data: dict, issue_id: str, identifier: str) -> dict:
        return {
            "id": issue_id,
            "identifier": identifier,
            "title": input_data["title"],
            "description": input_data["description"],
            "state": {"id": input_data["stateId"], "name": "Todo", "type": "unstarted"},
            "assignee": {"id": input_data["assigneeId"]},
        }

    def _calendar(self, method: str, url: str, body: bytes | None) -> dict:
        parsed = urlparse(url)
        path = parsed.path
        if method == "GET" and path.endswith("/events"):
            return {"items": self.calendar_events}
        if method == "PATCH":
            self.calendar_patches += 1
            event_id = path.rsplit("/", 1)[-1]
            event = next(item for item in self.calendar_events if item["id"] == event_id)
            event.update(json.loads((body or b"{}").decode("utf-8")))
            return event
        if method == "POST" and path.endswith("/events"):
            self.calendar_inserts += 1
            event = {
                "id": f"created-{self.calendar_inserts}",
                "status": "confirmed",
                **json.loads((body or b"{}").decode("utf-8"),),
            }
            self.calendar_events.append(event)
            return event
        if method == "GET":
            event_id = path.rsplit("/", 1)[-1]
            return next(item for item in self.calendar_events if item["id"] == event_id)
        raise AssertionError(f"Unexpected Calendar operation: {method} {url}")


def make_config(root: Path) -> dict:
    repository = root / "repo"
    (repository / ".git").mkdir(parents=True)
    return {
        "_config_root": str(root),
        "timezone": "Asia/Shanghai",
        "private_data_dir": ".ledger",
        "identity": {"person_id": "owner", "git_names": ["Owner"]},
        "repositories": [{"name": "repo", "path": str(repository)}],
        "integrations": {
            "linear": {
                "enabled": True,
                "read_backend": "mcp",
                "write_backend": "api",
                "allow_browser_fallback": False,
                "team": "Ledger",
                "snapshot": ".ledger/inbox/linear.json",
                "expected_user": "owner@example.com",
                "credential_ref": "env:LINEAR_CREDENTIAL",
            },
            "google_calendar": {
                "enabled": True,
                "read_backend": "mcp",
                "write_backend": "api",
                "allow_browser_fallback": False,
                "calendar": "primary",
                "snapshot": ".ledger/inbox/google-calendar.json",
                "expected_user": "owner@example.com",
                "client_id_ref": "env:GOOGLE_CLIENT_ID",
                "refresh_credential_ref": "env:GOOGLE_REFRESH_CREDENTIAL",
            },
        },
    }


def make_run(root: Path, *, past: bool = False) -> Path:
    run_dir = root / ".ledger" / "runs" / "2026-W35" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"status": "finalized", "profile_id": "owner", "files": {}}),
        encoding="utf-8",
    )
    day = "2026-08-24" if past else "2026-08-31"
    week = "2026-W35" if past else "2026-W36"
    proposal = {
        "approval_required": True,
        "linear": {
            "action": "prepare_next_week",
            "week_start": day,
            "tasks": [
                {
                    "title": "Finish the weekly workflow",
                    "weight": 3,
                    "origin": "next_plan",
                    "evidence_criteria": ["Run the complete test suite."],
                    "ledger_key": f"weekly:{week}:task-1",
                }
            ],
        },
        "google_calendar": {
            "action": "upsert_single_reminder",
            "title": "Review weekly study plan",
            "start": f"{day}T09:00:00+08:00",
            "end": f"{day}T09:15:00+08:00",
            "timezone": "Asia/Shanghai",
            "popup_minutes_before": 10,
            "description": "Review the plan and Linear tasks.",
            "preserve_existing_time": True,
            "ledger_key": f"weekly-reminder:{week}",
        },
    }
    (run_dir / "proposals.json").write_text(json.dumps(proposal), encoding="utf-8")
    return run_dir


class ApplyConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.secrets = {
            "env:LINEAR_CREDENTIAL": "linear-credential",
            "env:GOOGLE_CLIENT_ID": "google-client-id",
            "env:GOOGLE_REFRESH_CREDENTIAL": "google-refresh",
        }

    def test_applies_verifies_and_retries_without_duplicate_issues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = make_config(root)
            run_dir = make_run(root)
            services = FakeServices()
            result = apply_proposals(
                config,
                run_dir,
                approved=True,
                request_json=services,
                resolver=self.secrets.__getitem__,
                now=datetime.fromisoformat("2026-08-25T12:00:00+08:00"),
            )
            self.assertEqual(result["status"], "verified")
            self.assertEqual(services.linear_creates, 1)
            self.assertEqual(services.calendar_patches, 1)
            self.assertEqual(services.calendar_inserts, 0)
            self.assertEqual(
                services.calendar_events[0]["start"]["dateTime"],
                "2026-08-31T10:00:00+08:00",
            )
            self.assertEqual(
                services.calendar_events[0]["extendedProperties"]["private"]["existing"],
                "kept",
            )

            second = apply_proposals(
                config,
                run_dir,
                approved=True,
                request_json=services,
                resolver=self.secrets.__getitem__,
                now=datetime.fromisoformat("2026-08-25T12:00:00+08:00"),
            )
            self.assertEqual(second["status"], "verified")
            self.assertEqual(services.linear_creates, 1)
            self.assertEqual(services.linear_updates, 1)
            self.assertEqual(len(services.linear_issues), 1)
            manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "applied")
            self.assertEqual(
                manifest["files"]["connector_application"],
                "connector-application.json",
            )

    def test_past_reminder_blocks_before_any_external_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = make_config(root)
            run_dir = make_run(root, past=True)
            services = FakeServices()
            with self.assertRaisesRegex(ConnectorApplyError, "time has passed"):
                apply_proposals(
                    config,
                    run_dir,
                    approved=True,
                    request_json=services,
                    resolver=self.secrets.__getitem__,
                    now=datetime.fromisoformat("2026-08-25T12:00:00+08:00"),
                )
            self.assertEqual(services.linear_creates, 0)
            self.assertEqual(services.linear_updates, 0)
            self.assertEqual(services.calendar_patches, 0)
            audit = json.loads(
                (run_dir / "connector-application.json").read_text(encoding="utf-8")
            )
            self.assertEqual(audit["status"], "blocked")

    def test_applies_with_inline_toml_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = make_config(root)
            config["security"] = {"allow_inline_credentials": True}
            linear = config["integrations"]["linear"]
            linear["api_key"] = "linear-credential"
            linear.pop("credential_ref")
            calendar = config["integrations"]["google_calendar"]
            calendar["client_id"] = "google-client-id"
            calendar["refresh_token"] = "google-refresh"
            calendar.pop("client_id_ref")
            calendar.pop("refresh_credential_ref")
            run_dir = make_run(root)
            services = FakeServices()

            def reject_reference(_: str) -> str:
                raise AssertionError("inline credentials must not use a resolver")

            result = apply_proposals(
                config,
                run_dir,
                approved=True,
                request_json=services,
                resolver=reject_reference,
                now=datetime.fromisoformat("2026-08-25T12:00:00+08:00"),
            )
            self.assertEqual(result["status"], "verified")
            self.assertEqual(services.linear_creates, 1)
            self.assertEqual(services.calendar_patches, 1)

    def test_creates_and_verifies_missing_future_reminder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = make_config(root)
            run_dir = make_run(root)
            services = FakeServices(existing_event=False)
            result = apply_proposals(
                config,
                run_dir,
                approved=True,
                request_json=services,
                resolver=self.secrets.__getitem__,
                now=datetime.fromisoformat("2026-08-25T12:00:00+08:00"),
            )
            calendar = next(
                item
                for item in result["connectors"]
                if item["name"] == "google_calendar"
            )
            self.assertEqual(calendar["action"], "create")
            self.assertEqual(services.calendar_inserts, 1)
            self.assertEqual(services.calendar_patches, 0)
            self.assertEqual(
                services.calendar_events[0]["extendedProperties"]["private"][
                    "ledgerKind"
                ],
                "weekly-review",
            )

    def test_duplicate_calendar_reminders_block_linear_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = make_config(root)
            run_dir = make_run(root)
            services = FakeServices(duplicate_events=True)
            with self.assertRaisesRegex(ConnectorApplyError, "multiple weekly reminders"):
                apply_proposals(
                    config,
                    run_dir,
                    approved=True,
                    request_json=services,
                    resolver=self.secrets.__getitem__,
                    now=datetime.fromisoformat("2026-08-25T12:00:00+08:00"),
                )
            self.assertEqual(services.linear_creates, 0)
            self.assertEqual(services.calendar_patches, 0)


if __name__ == "__main__":
    unittest.main()
