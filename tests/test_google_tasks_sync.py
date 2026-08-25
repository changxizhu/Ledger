import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from skill_paths import configure_paths

configure_paths()

from sync_google_tasks import GoogleTasksSyncError, sync_linear_to_google_tasks


class FakeServices:
    def __init__(self) -> None:
        self.tasklists: list[dict] = []
        self.tasks: dict[str, list[dict]] = {}
        self.list_writes = 0
        self.task_writes = 0
        self.issue = {
            "id": "linear-issue-1",
            "identifier": "LED-1",
            "title": "Finish weekly review",
            "description": None,
            "url": "https://linear.app/example/issue/LED-1",
            "dueDate": "2026-08-28",
            "priority": 2,
            "estimate": 3,
            "createdAt": "2026-08-24T01:00:00Z",
            "updatedAt": "2026-08-25T01:00:00Z",
            "completedAt": None,
            "canceledAt": None,
            "state": {"name": "Todo", "type": "unstarted"},
            "assignee": {
                "id": "linear-user",
                "name": "Owner",
                "email": "owner@example.com",
            },
            "cycle": None,
        }

    def __call__(self, method, url, headers, body):
        if url.endswith("api.linear.app/graphql"):
            return self._linear(json.loads(body))
        if url.endswith("oauth2.googleapis.com/token"):
            return {"access_token": "temporary-google-access"}
        if url.endswith("users/me/calendarList/primary"):
            return {"id": "owner@example.com", "summary": "Owner"}
        if "/users/@me/lists" in url:
            return self._tasklists(method, body)
        if "/tasks/v1/lists/" in url and "/tasks" in url:
            return self._tasks(method, url, body)
        raise AssertionError(f"Unexpected request: {method} {url}")

    def _linear(self, payload):
        if "LedgerIdentity" in payload["query"]:
            return {
                "data": {
                    "viewer": {
                        "id": "linear-user",
                        "name": "Owner",
                        "email": "owner@example.com",
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
                        "nodes": [self.issue],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        }

    def _tasklists(self, method, body):
        if method == "GET":
            return {"items": self.tasklists}
        if method == "POST":
            self.list_writes += 1
            tasklist = {"id": f"list-{self.list_writes}", **json.loads(body)}
            self.tasklists.append(tasklist)
            self.tasks[tasklist["id"]] = []
            return tasklist
        raise AssertionError(f"Unexpected task list operation: {method}")

    def _tasks(self, method, url, body):
        parts = urlsplit(url).path.split("/")
        tasklist_id = parts[parts.index("lists") + 1]
        items = self.tasks.setdefault(tasklist_id, [])
        task_index = len(parts) - 1 - parts[::-1].index("tasks")
        task_id = parts[task_index + 1] if len(parts) > task_index + 1 else None
        if method == "GET" and not task_id:
            return {"items": items}
        if method == "GET":
            return next(item for item in items if item["id"] == task_id)
        payload = json.loads(body)
        if method == "POST":
            self.task_writes += 1
            task = {"id": f"task-{self.task_writes}", **payload}
            items.append(task)
            return task
        if method == "PATCH":
            self.task_writes += 1
            task = next(item for item in items if item["id"] == task_id)
            for key, value in payload.items():
                if value is None:
                    task.pop(key, None)
                else:
                    task[key] = value
            if task.get("status") == "completed":
                task["completed"] = "2026-08-25T08:00:00.000Z"
            return task
        raise AssertionError(f"Unexpected task operation: {method}")


def make_config(root: Path) -> dict:
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    return {
        "_config_root": str(root),
        "timezone": "Asia/Shanghai",
        "identity": {"person_id": "owner", "git_names": ["Owner"]},
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
            "google_tasks": {
                "enabled": True,
                "read_backend": "api",
                "write_backend": "api",
                "allow_browser_fallback": False,
                "credentials_from": "google_calendar",
                "source": "linear",
                "tasklist": "Linear",
            },
        },
    }


def resolver(reference: str) -> str:
    return {
        "env:LINEAR_CREDENTIAL": "linear-secret",
        "env:GOOGLE_CLIENT_ID": "google-client-id",
        "env:GOOGLE_CLIENT_CREDENTIAL": "google-client-secret",
        "env:GOOGLE_REFRESH_CREDENTIAL": "google-refresh-token",
    }[reference]


class GoogleTasksSyncTests(unittest.TestCase):
    def test_preview_does_not_write_external_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            services = FakeServices()
            result = sync_linear_to_google_tasks(
                make_config(Path(directory)),
                "2026-W35",
                request_json=services,
                resolver=resolver,
            )
            self.assertEqual(result["status"], "preview")
            self.assertEqual(result["target"]["list_operation"], "create")
            self.assertEqual(result["counts"]["create"], 1)
            self.assertEqual(services.list_writes, 0)
            self.assertEqual(services.task_writes, 0)

    def test_apply_is_idempotent_and_writes_no_credentials_to_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            services = FakeServices()
            first = sync_linear_to_google_tasks(
                make_config(root),
                "2026-W35",
                apply=True,
                request_json=services,
                resolver=resolver,
            )
            second = sync_linear_to_google_tasks(
                make_config(root),
                "2026-W35",
                apply=True,
                request_json=services,
                resolver=resolver,
            )
            self.assertEqual(first["status"], "verified")
            self.assertEqual(first["counts"]["create"], 1)
            self.assertEqual(second["counts"]["noop"], 1)
            self.assertEqual(services.list_writes, 1)
            self.assertEqual(services.task_writes, 1)
            self.assertEqual(len(services.tasks["list-1"]), 1)
            audit = Path(second["audit_file"]).read_text(encoding="utf-8")
            self.assertNotIn("linear-secret", audit)
            self.assertNotIn("temporary-google-access", audit)
            preview = sync_linear_to_google_tasks(
                make_config(root),
                "2026-W35",
                request_json=services,
                resolver=resolver,
            )
            self.assertTrue(preview["audit_file"].endswith(".preview.json"))
            verified = json.loads(
                (root / ".ledger/sync/google-tasks/2026-W35.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(verified["status"], "verified")

    def test_completed_linear_issue_completes_existing_google_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            services = FakeServices()
            config = make_config(root)
            sync_linear_to_google_tasks(
                config,
                "2026-W35",
                apply=True,
                request_json=services,
                resolver=resolver,
            )
            services.issue["state"] = {"name": "Done", "type": "completed"}
            services.issue["completedAt"] = "2026-08-25T08:00:00Z"
            result = sync_linear_to_google_tasks(
                config,
                "2026-W35",
                apply=True,
                request_json=services,
                resolver=resolver,
            )
            self.assertEqual(result["counts"]["update"], 1)
            self.assertEqual(services.tasks["list-1"][0]["status"], "completed")

    def test_duplicate_markers_block_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            services = FakeServices()
            services.tasklists = [{"id": "list-1", "title": "Linear"}]
            marker = "Ledger-Linear-ID: linear-issue-1"
            services.tasks = {
                "list-1": [
                    {"id": "task-1", "title": "A", "notes": marker},
                    {"id": "task-2", "title": "B", "notes": marker},
                ]
            }
            with self.assertRaisesRegex(GoogleTasksSyncError, "Duplicate"):
                sync_linear_to_google_tasks(
                    make_config(Path(directory)),
                    "2026-W35",
                    apply=True,
                    request_json=services,
                    resolver=resolver,
                )
            self.assertEqual(services.task_writes, 0)


if __name__ == "__main__":
    unittest.main()
