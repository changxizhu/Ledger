#!/usr/bin/env python3
"""Mirror assigned Linear issues into a dedicated Google Tasks list."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

try:
    from ._project import ensure_project_root
    from .apply_connectors import ConnectorApplyError, _google_access
    from .refresh_connectors import (
        ConnectorRefreshError,
        _refresh_linear,
        _request_json,
    )
except ImportError:  # Direct script execution.
    from _project import ensure_project_root
    from apply_connectors import ConnectorApplyError, _google_access
    from refresh_connectors import ConnectorRefreshError, _refresh_linear, _request_json

ensure_project_root()

from scripts.credentials import resolve_credential
from scripts.ledger_common import (
    integration_backends,
    private_data_dir,
    profile_id,
    validate_config,
    week_period,
    write_json,
)


GOOGLE_TASKS_ENDPOINT = "https://tasks.googleapis.com/tasks/v1"

JsonRequest = Callable[[str, str, dict[str, str], bytes | None], dict[str, Any]]
CredentialResolver = Callable[[str], str]


class GoogleTasksSyncError(RuntimeError):
    """Linear issues could not be safely synchronized to Google Tasks."""


def _marker(linear_id: str) -> str:
    return f"Ledger-Linear-ID: {linear_id}"


def _task_title(issue: dict[str, Any]) -> str:
    identifier = str(issue.get("identifier") or issue.get("id") or "").strip()
    title = str(issue.get("title") or "Untitled issue").strip()
    return f"[{identifier}] {title}" if identifier else title


def _task_notes(issue: dict[str, Any], week: str) -> str:
    lines = ["Synced from Linear."]
    if issue.get("url"):
        lines.append(f"Linear: {issue['url']}")
    lines.extend(
        [
            f"State: {issue.get('status') or 'Unknown'}",
            f"Week: {week}",
            f"Linear-Identifier: {issue.get('identifier') or issue.get('id')}",
            _marker(str(issue["linear_id"])),
        ]
    )
    return "\n".join(lines)


def _due_payload(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        day = datetime.fromisoformat(text[:10]).date()
    except ValueError as error:
        raise GoogleTasksSyncError(f"Linear issue has invalid due date: {text}") from error
    return f"{day.isoformat()}T00:00:00.000Z"


def _task_payload(issue: dict[str, Any], week: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": _task_title(issue),
        "notes": _task_notes(issue, week),
        "status": "completed"
        if issue.get("completed") or issue.get("cancelled")
        else "needsAction",
    }
    due = _due_payload(issue.get("due_date"))
    if due:
        payload["due"] = due
    return payload


def _list_connection(
    request_json: JsonRequest,
    url: str,
    headers: dict[str, str],
    item_key: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        target = url
        if page_token:
            separator = "&" if "?" in target else "?"
            target += separator + urlencode({"pageToken": page_token})
        response = request_json("GET", target, headers, None)
        items.extend(
            item for item in response.get(item_key, []) if isinstance(item, dict)
        )
        next_page = response.get("nextPageToken")
        if not next_page:
            return items
        if str(next_page) == page_token:
            raise GoogleTasksSyncError("Google Tasks returned an invalid page cursor")
        page_token = str(next_page)


def _configured_settings(config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    integrations = config.get("integrations", {})
    settings = integrations.get("google_tasks", {})
    if not settings.get("enabled"):
        raise GoogleTasksSyncError("google_tasks sync is disabled")
    read_backend, write_backend = integration_backends(settings)
    if (read_backend, write_backend) != ("api", "api"):
        raise GoogleTasksSyncError(
            "google_tasks sync requires read_backend=api and write_backend=api"
        )
    if settings.get("allow_browser_fallback", False):
        raise GoogleTasksSyncError("google_tasks browser fallback is forbidden")
    credential_source = str(settings.get("credentials_from", "google_calendar"))
    credential_settings = integrations.get(credential_source, {})
    if not credential_settings.get("enabled"):
        raise GoogleTasksSyncError(
            f"google_tasks credential source is disabled: {credential_source}"
        )
    return settings, credential_settings


def _task_marker(task: dict[str, Any]) -> str | None:
    for line in str(task.get("notes") or "").splitlines():
        if line.startswith("Ledger-Linear-ID: "):
            value = line.removeprefix("Ledger-Linear-ID: ").strip()
            return value or None
    return None


def _task_changes(existing: dict[str, Any], desired: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for field in ("title", "notes", "status"):
        if existing.get(field) != desired.get(field):
            changes[field] = desired[field]
    existing_due = str(existing.get("due") or "")[:10] or None
    desired_due = str(desired.get("due") or "")[:10] or None
    if existing_due != desired_due:
        changes["due"] = desired.get("due")
    if desired["status"] == "needsAction" and existing.get("completed"):
        changes["completed"] = None
    return changes


def _verify_task(
    request_json: JsonRequest,
    headers: dict[str, str],
    tasklist_id: str,
    task_id: str,
    desired: dict[str, Any],
    linear_id: str,
) -> dict[str, Any]:
    url = (
        f"{GOOGLE_TASKS_ENDPOINT}/lists/{quote(tasklist_id, safe='')}/tasks/"
        f"{quote(task_id, safe='')}"
    )
    task = request_json("GET", url, headers, None)
    if (
        task.get("title") != desired.get("title")
        or _task_marker(task) != linear_id
        or task.get("status") != desired.get("status")
        or str(task.get("due") or "")[:10]
        != str(desired.get("due") or "")[:10]
    ):
        raise GoogleTasksSyncError(f"Google Tasks verification failed for {linear_id}")
    return task


def sync_linear_to_google_tasks(
    config: dict[str, Any],
    week: str,
    *,
    apply: bool = False,
    request_json: JsonRequest = _request_json,
    resolver: CredentialResolver = resolve_credential,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Preview or apply an idempotent one-way Linear-to-Google Tasks sync."""

    config_errors = validate_config(config)
    if config_errors:
        raise GoogleTasksSyncError(
            "Invalid Ledger configuration: " + "; ".join(config_errors)
        )
    start, end = week_period(week)
    settings, credential_settings = _configured_settings(config)
    try:
        _, linear = _refresh_linear(config, start, end, request_json, resolver)
        access_token, google_account = _google_access(
            credential_settings,
            request_json,
            resolver,
            service_name="google_tasks",
        )
    except (ConnectorApplyError, ConnectorRefreshError) as error:
        raise GoogleTasksSyncError(str(error)) from error

    viewer_id = str((linear.get("account") or {}).get("id") or "")
    source_issues = [
        issue
        for issue in linear.get("issues", [])
        if isinstance(issue, dict)
        and str((issue.get("assignee") or {}).get("id") or "") == viewer_id
    ]
    source_ids = [str(issue.get("linear_id") or "") for issue in source_issues]
    if any(not value for value in source_ids) or len(source_ids) != len(set(source_ids)):
        raise GoogleTasksSyncError("Linear returned missing or duplicate stable issue IDs")

    headers = {"Authorization": f"Bearer {access_token}"}
    tasklists = _list_connection(
        request_json,
        f"{GOOGLE_TASKS_ENDPOINT}/users/@me/lists?maxResults=100",
        headers,
        "items",
    )
    list_title = str(settings.get("tasklist", "Linear")).strip()
    matching_lists = [item for item in tasklists if item.get("title") == list_title]
    if len(matching_lists) > 1:
        raise GoogleTasksSyncError(
            f"Multiple Google task lists are named {list_title!r}; sync is blocked"
        )

    tasklist = matching_lists[0] if matching_lists else None
    list_operation = "use_existing" if tasklist else "create"
    if not tasklist and apply:
        tasklist = request_json(
            "POST",
            f"{GOOGLE_TASKS_ENDPOINT}/users/@me/lists",
            {**headers, "Content-Type": "application/json"},
            json.dumps({"title": list_title}, separators=(",", ":")).encode("utf-8"),
        )
        if not tasklist.get("id") or tasklist.get("title") != list_title:
            raise GoogleTasksSyncError("Google Tasks list creation could not be verified")

    existing_tasks: list[dict[str, Any]] = []
    if tasklist:
        tasklist_id = str(tasklist["id"])
        params = urlencode(
            {
                "maxResults": 100,
                "showCompleted": "true",
                "showHidden": "true",
                "showDeleted": "false",
            }
        )
        existing_tasks = _list_connection(
            request_json,
            f"{GOOGLE_TASKS_ENDPOINT}/lists/{quote(tasklist_id, safe='')}/tasks?{params}",
            headers,
            "items",
        )

    by_marker: dict[str, list[dict[str, Any]]] = {}
    for task in existing_tasks:
        marker = _task_marker(task)
        if marker:
            by_marker.setdefault(marker, []).append(task)
    duplicates = [marker for marker, tasks in by_marker.items() if len(tasks) > 1]
    if duplicates:
        raise GoogleTasksSyncError(
            "Duplicate Google Tasks markers block sync: " + ", ".join(sorted(duplicates))
        )

    operations: list[dict[str, Any]] = []
    runtime: list[dict[str, Any]] = []
    for issue in source_issues:
        linear_id = str(issue["linear_id"])
        existing = (by_marker.get(linear_id) or [None])[0]
        desired = _task_payload(issue, week)
        if desired["status"] == "completed" and not existing:
            action = "skip"
            changes: dict[str, Any] = {}
            reason = "already completed in Linear and was never mirrored"
        elif not existing:
            action = "create"
            changes = desired
            reason = None
        else:
            changes = _task_changes(existing, desired)
            action = "update" if changes else "noop"
            reason = None
        public_operation = {
            "action": action,
            "linear_id": linear_id,
            "identifier": issue.get("identifier") or issue.get("id"),
            "title": issue.get("title"),
            "google_task_id": (existing or {}).get("id"),
        }
        if reason:
            public_operation["reason"] = reason
        operations.append(public_operation)
        runtime.append(
            {
                "action": action,
                "linear_id": linear_id,
                "desired": desired,
                "changes": changes,
                "existing": existing,
                "public": public_operation,
            }
        )

    if apply:
        if not tasklist:
            raise GoogleTasksSyncError("Google Tasks list is unavailable after creation")
        tasklist_id = str(tasklist["id"])
        base_url = (
            f"{GOOGLE_TASKS_ENDPOINT}/lists/{quote(tasklist_id, safe='')}/tasks"
        )
        write_headers = {**headers, "Content-Type": "application/json"}
        for item in runtime:
            if item["action"] in {"noop", "skip"}:
                continue
            if item["action"] == "create":
                result = request_json(
                    "POST",
                    base_url,
                    write_headers,
                    json.dumps(item["desired"], separators=(",", ":")).encode(
                        "utf-8"
                    ),
                )
            else:
                task_id = quote(str(item["existing"]["id"]), safe="")
                result = request_json(
                    "PATCH",
                    f"{base_url}/{task_id}",
                    write_headers,
                    json.dumps(item["changes"], separators=(",", ":")).encode(
                        "utf-8"
                    ),
                )
            task_id = str(result.get("id") or "")
            if not task_id:
                raise GoogleTasksSyncError(
                    f"Google Tasks {item['action']} returned no task ID"
                )
            verified = _verify_task(
                request_json,
                headers,
                tasklist_id,
                task_id,
                item["desired"],
                item["linear_id"],
            )
            item["public"]["google_task_id"] = verified.get("id")
            item["public"]["verified"] = True

    counts = {
        action: sum(1 for item in operations if item["action"] == action)
        for action in ("create", "update", "noop", "skip")
    }
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    audit = {
        "schema_version": 1,
        "profile_id": profile_id(config),
        "week": week,
        "status": "verified" if apply else "preview",
        "created_at": timestamp,
        "direction": "linear_to_google_tasks",
        "source": {
            "account": (linear.get("account") or {}).get("email"),
            "team": (linear.get("team") or {}).get("name"),
        },
        "target": {
            "account": google_account.get("id") or google_account.get("summary"),
            "tasklist": list_title,
            "tasklist_id": (tasklist or {}).get("id"),
            "list_operation": list_operation,
        },
        "counts": counts,
        "operations": operations,
    }
    audit_name = f"{week}.json" if apply else f"{week}.preview.json"
    audit_path = private_data_dir(config) / "sync" / "google-tasks" / audit_name
    write_json(audit_path, audit)
    audit["audit_file"] = str(audit_path)
    return audit
