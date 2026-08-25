#!/usr/bin/env python3
"""Apply approved weekly Linear and Calendar proposals through verified APIs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

try:
    from ._project import ensure_project_root
    from .refresh_connectors import (
        GOOGLE_CALENDAR_ENDPOINT,
        GOOGLE_TOKEN_ENDPOINT,
        _account_matches,
        _graphql,
        _request_json,
    )
except ImportError:  # Direct script execution.
    from _project import ensure_project_root
    from refresh_connectors import (
        GOOGLE_CALENDAR_ENDPOINT,
        GOOGLE_TOKEN_ENDPOINT,
        _account_matches,
        _graphql,
        _request_json,
    )

ensure_project_root()

from scripts.credentials import resolve_config_credential, resolve_credential
from scripts.ledger_common import (
    integration_backends,
    private_data_dir,
    profile_id,
    validate_config,
    write_json,
)


JsonRequest = Callable[[str, str, dict[str, str], bytes | None], dict[str, Any]]
CredentialResolver = Callable[[str], str]


class ConnectorApplyError(RuntimeError):
    """An approved connector proposal could not be safely applied."""


def _api_settings(config: dict[str, Any], name: str) -> dict[str, Any]:
    settings = config.get("integrations", {}).get(name, {})
    if not settings.get("enabled"):
        raise ConnectorApplyError(f"{name} write is blocked: integration is disabled")
    _, write_backend = integration_backends(settings)
    if write_backend != "api":
        raise ConnectorApplyError(
            f"{name} write is blocked: write_backend must be api, not {write_backend}"
        )
    if settings.get("allow_browser_fallback", False):
        raise ConnectorApplyError(
            f"{name} write is blocked: browser fallback is forbidden"
        )
    return settings


def _week_label(value: str) -> str:
    parsed = date.fromisoformat(value)
    year, week, _ = parsed.isocalendar()
    return f"{year}-W{week:02d}"


def _linear_marker(key: str) -> str:
    return f"Ledger-Key: {key}"


def _linear_description(task: dict[str, Any], week: str) -> str:
    lines = [
        f"Week: {week}",
        f"Weight: {task.get('weight', 1)}",
    ]
    origin = str(task.get("origin", "")).strip()
    if origin:
        lines.append(f"Origin: {origin}")
    criteria = [
        str(item).strip()
        for item in task.get("evidence_criteria", [])
        if str(item).strip()
    ]
    if criteria:
        lines.extend(["", "Acceptance evidence:"])
        lines.extend(f"- {item}" for item in criteria)
    lines.extend(["", _linear_marker(str(task["ledger_key"]))])
    return "\n".join(lines)


def _linear_identity(
    config: dict[str, Any],
    settings: dict[str, Any],
    request_json: JsonRequest,
    resolver: CredentialResolver,
) -> dict[str, Any]:
    credential = resolve_config_credential(
        settings, "api_key", "credential_ref", resolver=resolver
    )
    query = """
        query LedgerWriteIdentity {
          viewer { id name email }
          teams(first: 250) { nodes { id name key } }
        }
    """
    data = _graphql(request_json, credential, query)
    viewer = data.get("viewer") or {}
    expected_user = str(settings["expected_user"])
    if not _account_matches(
        expected_user, [viewer.get("id"), viewer.get("name"), viewer.get("email")]
    ):
        raise ConnectorApplyError(
            "linear write is blocked: authenticated account does not match expected_user"
        )
    target = str(settings["team"]).strip().casefold()
    team = next(
        (
            item
            for item in (data.get("teams") or {}).get("nodes", [])
            if target
            in {
                str(item.get("id", "")).casefold(),
                str(item.get("name", "")).casefold(),
                str(item.get("key", "")).casefold(),
            }
        ),
        None,
    )
    if not team:
        raise ConnectorApplyError("linear write is blocked: configured team is unavailable")
    return {"credential": credential, "viewer": viewer, "team": team}


def _linear_team_state_and_issues(
    request_json: JsonRequest, credential: str, team_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    query = """
        query LedgerWritePlan($teamId: String!, $after: String) {
          team(id: $teamId) {
            states { nodes { id name type } }
            issues(first: 250, after: $after, orderBy: updatedAt) {
              nodes {
                id identifier title description
                state { id name type }
                assignee { id }
              }
              pageInfo { hasNextPage endCursor }
            }
          }
        }
    """
    issues: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        data = _graphql(
            request_json,
            credential,
            query,
            {"teamId": team_id, "after": cursor},
        )
        team = data.get("team") or {}
        if not states:
            states = [
                item
                for item in (team.get("states") or {}).get("nodes", [])
                if isinstance(item, dict)
            ]
        connection = team.get("issues") or {}
        issues.extend(
            item for item in connection.get("nodes", []) if isinstance(item, dict)
        )
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        next_cursor = page_info.get("endCursor")
        if not next_cursor or next_cursor == cursor:
            raise ConnectorApplyError(
                "linear write is blocked: issue pagination returned an invalid cursor"
            )
        cursor = str(next_cursor)

    todo_state = next(
        (item for item in states if str(item.get("name", "")).casefold() == "todo"),
        None,
    ) or next(
        (item for item in states if str(item.get("type", "")).casefold() == "unstarted"),
        None,
    )
    if not todo_state:
        raise ConnectorApplyError(
            "linear write is blocked: team has no Todo or unstarted workflow state"
        )
    return todo_state, issues


def _preflight_linear(
    config: dict[str, Any],
    proposal: dict[str, Any],
    request_json: JsonRequest,
    resolver: CredentialResolver,
) -> tuple[dict[str, Any], dict[str, Any]]:
    settings = _api_settings(config, "linear")
    if proposal.get("action") != "prepare_next_week":
        raise ConnectorApplyError("linear write is blocked: unsupported proposal action")
    week = _week_label(str(proposal["week_start"]))
    tasks = proposal.get("tasks")
    if not isinstance(tasks, list):
        raise ConnectorApplyError("linear write is blocked: tasks must be a list")
    keys = [str(task.get("ledger_key", "")).strip() for task in tasks]
    if any(not key for key in keys) or len(keys) != len(set(keys)):
        raise ConnectorApplyError(
            "linear write is blocked: every task needs a unique ledger_key"
        )
    if any(not str(task.get("title", "")).strip() for task in tasks):
        raise ConnectorApplyError("linear write is blocked: every task needs a title")

    runtime = _linear_identity(config, settings, request_json, resolver)
    todo_state, existing = _linear_team_state_and_issues(
        request_json, runtime["credential"], str(runtime["team"]["id"])
    )
    operations = []
    for task in tasks:
        marker = _linear_marker(str(task["ledger_key"]))
        title = str(task["title"]).strip()
        matches = [
            issue for issue in existing if marker in str(issue.get("description") or "")
        ]
        if not matches:
            matches = [
                issue
                for issue in existing
                if str(issue.get("title", "")).strip() == title
                and f"Week: {week}" in str(issue.get("description") or "")
            ]
        unique_matches = {str(item.get("id")): item for item in matches}
        if len(unique_matches) > 1:
            raise ConnectorApplyError(
                f"linear write is blocked: multiple issues match {task['ledger_key']}"
            )
        existing_issue = next(iter(unique_matches.values()), None)
        operations.append(
            {
                "action": "update" if existing_issue else "create",
                "task": task,
                "existing": existing_issue,
            }
        )
    runtime.update(
        {
            "settings": settings,
            "week": week,
            "todo_state": todo_state,
            "operations": operations,
        }
    )
    public = {
        "name": "linear",
        "status": "ready",
        "account_verified": True,
        "team": runtime["team"].get("name"),
        "operations": [
            {
                "action": item["action"],
                "ledger_key": item["task"]["ledger_key"],
                "title": item["task"]["title"],
                "existing_id": (item["existing"] or {}).get("identifier"),
            }
            for item in operations
        ],
    }
    return runtime, public


def _verify_linear_issue(
    request_json: JsonRequest,
    credential: str,
    issue_id: str,
    title: str,
    marker: str,
    assignee_id: str,
    state_id: str,
) -> dict[str, Any]:
    query = """
        query LedgerVerifyIssue($id: String!) {
          issue(id: $id) {
            id identifier title description
            state { id name }
            assignee { id }
          }
        }
    """
    issue = _graphql(request_json, credential, query, {"id": issue_id}).get("issue")
    if not isinstance(issue, dict):
        raise ConnectorApplyError("linear verification failed: issue is unavailable")
    if (
        issue.get("title") != title
        or marker not in str(issue.get("description") or "")
        or str((issue.get("assignee") or {}).get("id")) != assignee_id
        or str((issue.get("state") or {}).get("id")) != state_id
    ):
        raise ConnectorApplyError(
            f"linear verification failed for {issue.get('identifier') or issue_id}"
        )
    return issue


def _apply_linear(
    runtime: dict[str, Any], request_json: JsonRequest
) -> dict[str, Any]:
    credential = str(runtime["credential"])
    viewer_id = str(runtime["viewer"]["id"])
    team_id = str(runtime["team"]["id"])
    state_id = str(runtime["todo_state"]["id"])
    results = []
    for operation in runtime["operations"]:
        task = operation["task"]
        title = str(task["title"]).strip()
        description = _linear_description(task, str(runtime["week"]))
        input_data = {
            "title": title,
            "description": description,
            "assigneeId": viewer_id,
            "stateId": state_id,
        }
        if operation["action"] == "create":
            input_data["teamId"] = team_id
            mutation = """
                mutation LedgerCreateIssue($input: IssueCreateInput!) {
                  issueCreate(input: $input) {
                    success
                    issue { id identifier }
                  }
                }
            """
            payload = _graphql(
                request_json, credential, mutation, {"input": input_data}
            ).get("issueCreate") or {}
        else:
            mutation = """
                mutation LedgerUpdateIssue($id: String!, $input: IssueUpdateInput!) {
                  issueUpdate(id: $id, input: $input) {
                    success
                    issue { id identifier }
                  }
                }
            """
            payload = _graphql(
                request_json,
                credential,
                mutation,
                {"id": operation["existing"]["id"], "input": input_data},
            ).get("issueUpdate") or {}
        issue_ref = payload.get("issue") or {}
        if not payload.get("success") or not issue_ref.get("id"):
            raise ConnectorApplyError(f"linear {operation['action']} failed for {title}")
        issue = _verify_linear_issue(
            request_json,
            credential,
            str(issue_ref["id"]),
            title,
            _linear_marker(str(task["ledger_key"])),
            viewer_id,
            state_id,
        )
        results.append(
            {
                "action": operation["action"],
                "id": issue.get("identifier") or issue.get("id"),
                "ledger_key": task["ledger_key"],
                "verified": True,
            }
        )
    return {"name": "linear", "status": "verified", "issues": results}


def _google_access(
    settings: dict[str, Any],
    request_json: JsonRequest,
    resolver: CredentialResolver,
    service_name: str = "google_calendar",
) -> tuple[str, dict[str, Any]]:
    form = {
        "client_id": resolve_config_credential(
            settings, "client_id", "client_id_ref", resolver=resolver
        ),
        "refresh_token": resolve_config_credential(
            settings,
            "refresh_token",
            "refresh_credential_ref",
            resolver=resolver,
        ),
        "grant_type": "refresh_token",
    }
    if settings.get("client_secret") or settings.get("client_credential_ref"):
        form["client_secret"] = resolve_config_credential(
            settings,
            "client_secret",
            "client_credential_ref",
            resolver=resolver,
        )
    token_response = request_json(
        "POST",
        GOOGLE_TOKEN_ENDPOINT,
        {"Content-Type": "application/x-www-form-urlencoded"},
        urlencode(form).encode("utf-8"),
    )
    access_token = token_response.get("access_token")
    if not access_token:
        raise ConnectorApplyError(
            f"{service_name} write is blocked: OAuth refresh returned no access token"
        )
    headers = {"Authorization": f"Bearer {access_token}"}
    account = request_json(
        "GET",
        f"{GOOGLE_CALENDAR_ENDPOINT}/users/me/calendarList/primary",
        headers,
        None,
    )
    if not _account_matches(
        str(settings["expected_user"]), [account.get("id"), account.get("summary")]
    ):
        raise ConnectorApplyError(
            f"{service_name} write is blocked: authenticated account does not match expected_user"
        )
    return str(access_token), account


def _parse_event_time(value: Any, timezone_name: str) -> datetime:
    if isinstance(value, dict):
        value = value.get("dateTime") or value.get("date")
    text = str(value or "")
    try:
        if "T" in text:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            parsed = datetime.combine(date.fromisoformat(text), time.min)
    except ValueError as error:
        raise ConnectorApplyError("calendar proposal contains an invalid event time") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed


def _event_time_payload(value: str, timezone_name: str) -> dict[str, str]:
    parsed = _parse_event_time(value, timezone_name)
    return {"dateTime": parsed.isoformat(), "timeZone": timezone_name}


def _preflight_calendar(
    config: dict[str, Any],
    proposal: dict[str, Any],
    request_json: JsonRequest,
    resolver: CredentialResolver,
    now: datetime,
    allow_past_reminder: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    settings = _api_settings(config, "google_calendar")
    if proposal.get("action") not in {
        "upsert_single_reminder",
        "update_existing_reminder",
    }:
        raise ConnectorApplyError(
            "google_calendar write is blocked: unsupported proposal action"
        )
    title = str(proposal.get("title", "")).strip()
    ledger_key = str(proposal.get("ledger_key", "")).strip()
    if title != "Review weekly study plan" or not ledger_key:
        raise ConnectorApplyError(
            "google_calendar write is blocked: reminder title or ledger_key is invalid"
        )
    timezone_name = str(proposal.get("timezone") or config["timezone"])
    proposed_start = _parse_event_time(proposal.get("start"), timezone_name)
    proposed_end = _parse_event_time(proposal.get("end"), timezone_name)
    if proposed_end <= proposed_start:
        raise ConnectorApplyError(
            "google_calendar write is blocked: reminder end must follow start"
        )
    comparable_now = now.astimezone(proposed_end.tzinfo)
    if proposed_end <= comparable_now and not allow_past_reminder:
        raise ConnectorApplyError(
            "google_calendar write is blocked: Monday reminder time has passed"
        )

    access_token, account = _google_access(settings, request_json, resolver)
    headers = {"Authorization": f"Bearer {access_token}"}
    week_start = proposed_start.date() - timedelta(days=proposed_start.weekday())
    zone = ZoneInfo(timezone_name)
    interval_start = datetime.combine(week_start, time.min, tzinfo=zone)
    interval_end = interval_start + timedelta(days=7)
    params = {
        "timeMin": interval_start.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "timeMax": interval_end.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "singleEvents": "true",
        "showDeleted": "false",
        "q": title,
        "maxResults": "50",
    }
    calendar_id = quote(str(settings.get("calendar", "primary")), safe="")
    response = request_json(
        "GET",
        f"{GOOGLE_CALENDAR_ENDPOINT}/calendars/{calendar_id}/events?"
        + urlencode(params),
        headers,
        None,
    )
    matches = [
        item
        for item in response.get("items", [])
        if isinstance(item, dict)
        and item.get("status") != "cancelled"
        and str(item.get("summary", "")).strip() == title
    ]
    if len(matches) > 1:
        raise ConnectorApplyError(
            "google_calendar write is blocked: multiple weekly reminders match"
        )
    event_id = str(proposal.get("event_id", "")).strip()
    if event_id:
        matches = [item for item in matches if str(item.get("id")) == event_id]
    if event_id and not matches:
        raise ConnectorApplyError(
            "google_calendar write is blocked: configured event_id is not the weekly reminder"
        )
    existing = matches[0] if matches else None
    runtime = {
        "settings": settings,
        "access_token": access_token,
        "account": account,
        "calendar_id": calendar_id,
        "proposal": proposal,
        "existing": existing,
        "timezone": timezone_name,
    }
    public = {
        "name": "google_calendar",
        "status": "ready",
        "account_verified": True,
        "operation": "update" if existing else "create",
        "event_id": (existing or {}).get("id"),
        "preserve_existing_time": bool(
            existing and proposal.get("preserve_existing_time", False)
        ),
    }
    return runtime, public


def _calendar_reminders_match(event: dict[str, Any], minutes: int) -> bool:
    reminders = event.get("reminders") or {}
    overrides = reminders.get("overrides") or []
    return reminders.get("useDefault") is False and any(
        item.get("method") == "popup" and item.get("minutes") == minutes
        for item in overrides
        if isinstance(item, dict)
    )


def _apply_calendar(
    runtime: dict[str, Any], request_json: JsonRequest
) -> dict[str, Any]:
    proposal = runtime["proposal"]
    existing = runtime["existing"]
    timezone_name = runtime["timezone"]
    headers = {
        "Authorization": f"Bearer {runtime['access_token']}",
        "Content-Type": "application/json",
    }
    private_properties = dict(
        ((existing or {}).get("extendedProperties") or {}).get("private") or {}
    )
    private_properties.update(
        {
            "ledgerKind": "weekly-review",
            "ledgerKey": str(proposal["ledger_key"]),
        }
    )
    body: dict[str, Any] = {
        "summary": proposal["title"],
        "description": proposal.get("description", ""),
        "reminders": {
            "useDefault": False,
            "overrides": [
                {
                    "method": "popup",
                    "minutes": int(proposal.get("popup_minutes_before", 10)),
                }
            ],
        },
        "extendedProperties": {"private": private_properties},
    }
    preserve_time = bool(existing and proposal.get("preserve_existing_time", False))
    if not preserve_time:
        body["start"] = _event_time_payload(str(proposal["start"]), timezone_name)
        body["end"] = _event_time_payload(str(proposal["end"]), timezone_name)
    base_url = (
        f"{GOOGLE_CALENDAR_ENDPOINT}/calendars/{runtime['calendar_id']}/events"
    )
    if existing:
        event_id = str(existing["id"])
        request_json(
            "PATCH",
            f"{base_url}/{quote(event_id, safe='')}?sendUpdates=none",
            headers,
            json.dumps(body, separators=(",", ":")).encode("utf-8"),
        )
        action = "update"
    else:
        result = request_json(
            "POST",
            f"{base_url}?sendUpdates=none",
            headers,
            json.dumps(body, separators=(",", ":")).encode("utf-8"),
        )
        event_id = str(result.get("id", ""))
        action = "create"
    if not event_id:
        raise ConnectorApplyError("google_calendar write failed: event id is missing")
    verified = request_json(
        "GET",
        f"{base_url}/{quote(event_id, safe='')}",
        {"Authorization": f"Bearer {runtime['access_token']}"},
        None,
    )
    expected_minutes = int(proposal.get("popup_minutes_before", 10))
    private = ((verified.get("extendedProperties") or {}).get("private") or {})
    if (
        verified.get("summary") != proposal["title"]
        or verified.get("description", "") != proposal.get("description", "")
        or private.get("ledgerKey") != proposal["ledger_key"]
        or not _calendar_reminders_match(verified, expected_minutes)
    ):
        raise ConnectorApplyError(
            f"google_calendar verification failed for event {event_id}"
        )
    return {
        "name": "google_calendar",
        "status": "verified",
        "action": action,
        "event_id": event_id,
        "time_preserved": preserve_time,
        "verified": True,
    }


def _proposal_hash(proposal: dict[str, Any]) -> str:
    encoded = json.dumps(
        proposal, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def apply_proposals(
    config: dict[str, Any],
    run_dir: Path,
    *,
    approved: bool,
    allow_past_reminder: bool = False,
    request_json: JsonRequest = _request_json,
    resolver: CredentialResolver = resolve_credential,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Preflight, apply, and verify one finalized run's connector proposals."""

    errors = validate_config(config)
    if errors:
        raise ValueError("Invalid Ledger configuration: " + "; ".join(errors))
    run_dir = run_dir.expanduser().resolve()
    runs_root = (private_data_dir(config) / "runs").resolve()
    if not run_dir.is_relative_to(runs_root):
        raise ValueError(
            "Run directory must be inside the configured private runs directory"
        )
    manifest_path = run_dir / "run.json"
    proposal_path = run_dir / "proposals.json"
    if not manifest_path.is_file() or not proposal_path.is_file():
        raise ValueError("Run directory is missing run.json or proposals.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") not in {"finalized", "applied"}:
        raise ConnectorApplyError("Connector proposals require a finalized run")
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    if proposal.get("approval_required") and not approved:
        raise ConnectorApplyError("Connector proposals require explicit approval")

    started_at = datetime.now(timezone.utc)
    audit_path = run_dir / "connector-application.json"
    audit: dict[str, Any] = {
        "schema_version": 1,
        "profile_id": profile_id(config),
        "status": "preflight",
        "started_at": started_at.isoformat(),
        "proposal_sha256": _proposal_hash(proposal),
        "connectors": [],
    }
    current_time = now or started_at
    runtime: dict[str, dict[str, Any]] = {}
    preflight_errors = []
    preflight_steps = (
        (
            "linear",
            lambda: _preflight_linear(
                config, proposal.get("linear") or {}, request_json, resolver
            ),
        ),
        (
            "google_calendar",
            lambda: _preflight_calendar(
                config,
                proposal.get("google_calendar") or {},
                request_json,
                resolver,
                current_time,
                allow_past_reminder,
            ),
        ),
    )
    for name, preflight in preflight_steps:
        try:
            runtime[name], public = preflight()
            audit["connectors"].append(public)
        except Exception as error:
            preflight_errors.append(f"{name}: {error}")
            audit["connectors"].append(
                {"name": name, "status": "blocked", "detail": str(error)}
            )
    if preflight_errors:
        audit["status"] = "blocked"
        audit["completed_at"] = datetime.now(timezone.utc).isoformat()
        write_json(audit_path, audit)
        raise ConnectorApplyError("; ".join(preflight_errors))

    audit["status"] = "applying"
    write_json(audit_path, audit)
    applied = []
    try:
        applied.append(_apply_linear(runtime["linear"], request_json))
        applied.append(_apply_calendar(runtime["google_calendar"], request_json))
    except Exception as error:
        audit["status"] = "partial" if applied else "failed"
        audit["connectors"] = applied + [
            {"name": "application", "status": "failed", "detail": str(error)}
        ]
        audit["completed_at"] = datetime.now(timezone.utc).isoformat()
        write_json(audit_path, audit)
        raise ConnectorApplyError(str(error)) from error

    audit["status"] = "verified"
    audit["connectors"] = applied
    audit["completed_at"] = datetime.now(timezone.utc).isoformat()
    write_json(audit_path, audit)
    manifest["status"] = "applied"
    manifest["applied_at"] = audit["completed_at"]
    manifest.setdefault("files", {})["connector_application"] = audit_path.name
    write_json(manifest_path, manifest)
    return audit
