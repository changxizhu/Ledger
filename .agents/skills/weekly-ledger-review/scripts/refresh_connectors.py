#!/usr/bin/env python3
"""Refresh private Linear and Google Calendar snapshots using direct credentials."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

try:
    from ._project import ensure_project_root
except ImportError:  # Direct script execution.
    from _project import ensure_project_root

ensure_project_root()

from scripts.credentials import resolve_credential
from scripts.ledger_common import (
    load_config,
    parse_timestamp,
    profile_id,
    resolve_config_path,
    utc_period,
    validate_config,
    week_period,
    write_json,
)


LINEAR_ENDPOINT = "https://api.linear.app/graphql"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_ENDPOINT = "https://www.googleapis.com/calendar/v3"

JsonRequest = Callable[[str, str, dict[str, str], bytes | None], dict[str, Any]]
CredentialResolver = Callable[[str], str]


class ConnectorRefreshError(RuntimeError):
    """A direct connector could not produce a verified snapshot."""


def _request_json(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> dict[str, Any]:
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise ConnectorRefreshError(
            f"Connector request failed with HTTP {error.code}"
        ) from error
    except URLError as error:
        raise ConnectorRefreshError(
            "Connector request could not reach the service"
        ) from error
    except json.JSONDecodeError as error:
        raise ConnectorRefreshError("Connector returned invalid JSON") from error
    if not isinstance(data, dict):
        raise ConnectorRefreshError("Connector returned an unexpected response")
    return data


def _graphql(
    request_json: JsonRequest,
    credential: str,
    query: str,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = json.dumps(
        {"query": query, "variables": variables or {}}, separators=(",", ":")
    ).encode("utf-8")
    response = request_json(
        "POST",
        LINEAR_ENDPOINT,
        {"Authorization": credential, "Content-Type": "application/json"},
        payload,
    )
    if response.get("errors"):
        messages = [
            str(item.get("message", "GraphQL error"))
            for item in response["errors"][:3]
            if isinstance(item, dict)
        ]
        raise ConnectorRefreshError("Linear GraphQL error: " + "; ".join(messages))
    data = response.get("data")
    if not isinstance(data, dict):
        raise ConnectorRefreshError("Linear response is missing data")
    return data


def _account_matches(expected: str, values: list[object]) -> bool:
    target = expected.strip().casefold()
    return bool(target) and any(
        target == str(value).strip().casefold() for value in values if value
    )


def _timestamp_in_period(value: object, start: Any, end: Any) -> bool:
    parsed = parse_timestamp(value)
    return bool(parsed and start <= parsed < end)


def _linear_issue_in_period(
    issue: dict[str, Any], period_start: Any, period_end: Any
) -> bool:
    cycle = issue.get("cycle") or {}
    cycle_start = parse_timestamp(cycle.get("startsAt"))
    cycle_end = parse_timestamp(cycle.get("endsAt"))
    if (
        cycle_start
        and cycle_end
        and cycle_start < period_end
        and cycle_end > period_start
    ):
        return True
    return any(
        _timestamp_in_period(issue.get(field), period_start, period_end)
        for field in ("createdAt", "updatedAt", "completedAt", "canceledAt")
    )


def _refresh_linear(
    config: dict[str, Any],
    start: date,
    end: date,
    request_json: JsonRequest,
    resolver: CredentialResolver,
) -> tuple[Path, dict[str, Any]]:
    settings = config["integrations"]["linear"]
    credential = resolver(str(settings["credential_ref"]))
    identity_query = """
        query LedgerIdentity {
          viewer { id name email }
          teams(first: 250) { nodes { id name key } }
        }
    """
    identity = _graphql(request_json, credential, identity_query)
    viewer = identity.get("viewer") or {}
    expected_user = str(settings["expected_user"])
    if not _account_matches(
        expected_user, [viewer.get("id"), viewer.get("name"), viewer.get("email")]
    ):
        raise ConnectorRefreshError(
            "Authenticated Linear account does not match expected_user"
        )

    expected_team = str(settings["team"]).strip().casefold()
    teams = (identity.get("teams") or {}).get("nodes") or []
    team = next(
        (
            item
            for item in teams
            if expected_team
            in {
                str(item.get("id", "")).casefold(),
                str(item.get("name", "")).casefold(),
                str(item.get("key", "")).casefold(),
            }
        ),
        None,
    )
    if not team:
        raise ConnectorRefreshError("Configured Linear team is not available")

    issues_query = """
        query LedgerTeamIssues($teamId: String!, $after: String) {
          team(id: $teamId) {
            issues(first: 250, after: $after, orderBy: updatedAt) {
              nodes {
                id identifier title priority estimate createdAt updatedAt
                completedAt canceledAt
                state { name type }
                cycle { id name number startsAt endsAt }
              }
              pageInfo { hasNextPage endCursor }
            }
          }
        }
    """
    raw_issues: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        data = _graphql(
            request_json,
            credential,
            issues_query,
            {"teamId": team["id"], "after": cursor},
        )
        connection = (data.get("team") or {}).get("issues") or {}
        raw_issues.extend(
            item for item in connection.get("nodes", []) if isinstance(item, dict)
        )
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        next_cursor = page_info.get("endCursor")
        if not next_cursor or next_cursor == cursor:
            raise ConnectorRefreshError("Linear pagination returned an invalid cursor")
        cursor = str(next_cursor)

    period_start, period_end = utc_period(start, end, str(config["timezone"]))
    issues = []
    for issue in raw_issues:
        if not _linear_issue_in_period(issue, period_start, period_end):
            continue
        state = issue.get("state") or {}
        state_type = str(state.get("type", "")).casefold()
        estimate = issue.get("estimate")
        try:
            weight = float(estimate) if float(estimate) > 0 else 1
        except (TypeError, ValueError):
            weight = 1
        issues.append(
            {
                "id": issue.get("identifier") or issue.get("id"),
                "title": issue.get("title") or "Untitled issue",
                "status": state.get("name") or state_type or "Unknown",
                "completed": state_type == "completed"
                or bool(issue.get("completedAt")),
                "cancelled": state_type in {"canceled", "cancelled"}
                or bool(issue.get("canceledAt")),
                "weight": weight,
                "cycle": issue.get("cycle"),
                "updated_at": issue.get("updatedAt"),
            }
        )

    snapshot = {
        "source": "linear",
        "profile_id": profile_id(config),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "account": {
            "id": viewer.get("id"),
            "name": viewer.get("name"),
            "email": viewer.get("email"),
        },
        "team": {"id": team.get("id"), "name": team.get("name")},
        "issues": issues,
    }
    path = resolve_config_path(config, str(settings["snapshot"]))
    return path, snapshot


def _event_time(value: object) -> object:
    if not isinstance(value, dict):
        return None
    return value.get("dateTime") or value.get("date")


def _refresh_google_calendar(
    config: dict[str, Any],
    start: date,
    end: date,
    request_json: JsonRequest,
    resolver: CredentialResolver,
) -> tuple[Path, dict[str, Any]]:
    settings = config["integrations"]["google_calendar"]
    form = {
        "client_id": resolver(str(settings["client_id_ref"])),
        "refresh_token": resolver(str(settings["refresh_credential_ref"])),
        "grant_type": "refresh_token",
    }
    if settings.get("client_credential_ref"):
        form["client_secret"] = resolver(str(settings["client_credential_ref"]))
    token_response = request_json(
        "POST",
        GOOGLE_TOKEN_ENDPOINT,
        {"Content-Type": "application/x-www-form-urlencoded"},
        urlencode(form).encode("utf-8"),
    )
    access_credential = token_response.get("access_token")
    if not access_credential:
        raise ConnectorRefreshError(
            "Google OAuth refresh did not return an access token"
        )
    headers = {"Authorization": f"Bearer {access_credential}"}

    account = request_json(
        "GET",
        f"{GOOGLE_CALENDAR_ENDPOINT}/users/me/calendarList/primary",
        headers,
        None,
    )
    expected_user = str(settings["expected_user"])
    if not _account_matches(expected_user, [account.get("id"), account.get("summary")]):
        raise ConnectorRefreshError(
            "Authenticated Google Calendar account does not match expected_user"
        )

    period_start, period_end = utc_period(start, end, str(config["timezone"]))
    calendar_id = quote(str(settings.get("calendar", "primary")), safe="")
    params = {
        "timeMin": period_start.isoformat().replace("+00:00", "Z"),
        "timeMax": period_end.isoformat().replace("+00:00", "Z"),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": "2500",
        "showDeleted": "false",
    }
    raw_events: list[dict[str, Any]] = []
    while True:
        url = f"{GOOGLE_CALENDAR_ENDPOINT}/calendars/{calendar_id}/events?" + urlencode(
            params
        )
        response = request_json("GET", url, headers, None)
        raw_events.extend(
            item for item in response.get("items", []) if isinstance(item, dict)
        )
        next_page = response.get("nextPageToken")
        if not next_page:
            break
        params["pageToken"] = str(next_page)

    prefixes = [
        str(value).strip().casefold()
        for value in settings.get("focus_title_prefixes", [])
        if str(value).strip()
    ]
    events = []
    for event in raw_events:
        title = str(event.get("summary") or "Untitled event")
        event_type = str(event.get("eventType") or "default")
        start_value = _event_time(event.get("start"))
        end_value = _event_time(event.get("end"))
        timed = bool(
            isinstance(event.get("start"), dict)
            and event["start"].get("dateTime")
            and isinstance(event.get("end"), dict)
            and event["end"].get("dateTime")
        )
        focus = timed and (
            event_type == "focusTime"
            or any(title.casefold().startswith(prefix) for prefix in prefixes)
        )
        events.append(
            {
                "id": event.get("id"),
                "title": title,
                "kind": "focus" if focus else "event",
                "focus": focus,
                "start": start_value,
                "end": end_value,
                "status": event.get("status"),
                "event_type": event_type,
            }
        )

    snapshot = {
        "source": "google_calendar",
        "profile_id": profile_id(config),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "account": {"id": account.get("id"), "summary": account.get("summary")},
        "calendar": str(settings.get("calendar", "primary")),
        "events": events,
    }
    path = resolve_config_path(config, str(settings["snapshot"]))
    return path, snapshot


def refresh_direct_connector_snapshots(
    config: dict[str, Any],
    start: date,
    end: date,
    *,
    request_json: JsonRequest = _request_json,
    resolver: CredentialResolver = resolve_credential,
) -> list[dict[str, Any]]:
    errors = validate_config(config)
    if errors:
        raise ValueError("Invalid Ledger configuration: " + "; ".join(errors))

    pending = []
    integrations = config.get("integrations", {})
    refreshers = {
        "linear": _refresh_linear,
        "google_calendar": _refresh_google_calendar,
    }
    for name, refresher in refreshers.items():
        settings = integrations.get(name, {})
        if not settings.get("enabled") or settings.get("auth_mode", "mcp") != "direct":
            continue
        path, snapshot = refresher(config, start, end, request_json, resolver)
        item_key = "issues" if name == "linear" else "events"
        pending.append(
            {
                "name": name,
                "status": "refreshed",
                "snapshot": str(path),
                "item_count": len(snapshot[item_key]),
                "account_verified": True,
                "_path": path,
                "_data": snapshot,
            }
        )
    for item in pending:
        write_json(item.pop("_path"), item.pop("_data"))
    return pending


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--week", required=True)
    args = parser.parse_args()
    start, end = week_period(args.week)
    result = refresh_direct_connector_snapshots(load_config(args.config), start, end)
    print(json.dumps({"week": args.week, "connectors": result}, indent=2))


if __name__ == "__main__":
    main()
