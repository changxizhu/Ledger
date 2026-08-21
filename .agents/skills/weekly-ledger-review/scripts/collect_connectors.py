#!/usr/bin/env python3
"""Load approved Linear and Google Calendar snapshots for a weekly run."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

try:
    from ._project import ensure_project_root
except ImportError:  # Direct script execution.
    from _project import ensure_project_root

ensure_project_root()

from scripts.ledger_common import (
    parse_timestamp,
    profile_id,
    resolve_config_path,
    utc_period,
)


CONNECTORS = {
    "linear": "issues",
    "google_calendar": "events",
}


def _account_matches(expected: str, data: dict[str, Any]) -> bool:
    account = data.get("account")
    if not isinstance(account, dict):
        return False
    target = expected.strip().casefold()
    return bool(target) and any(
        target == str(value).strip().casefold() for value in account.values() if value
    )


def _focus_hours(
    events: list[dict[str, Any]], period_start: Any, period_end: Any
) -> float:
    total_seconds = 0.0
    seen = set()
    for event in events:
        if not (event.get("focus") or event.get("kind") == "focus"):
            continue
        event_start = parse_timestamp(event.get("start"))
        event_end = parse_timestamp(event.get("end"))
        key = event.get("id") or (
            event.get("title"),
            event.get("start"),
            event.get("end"),
        )
        if key in seen:
            continue
        seen.add(key)
        if event_start and event_end and event_end > event_start:
            clipped_start = max(event_start, period_start)
            clipped_end = min(event_end, period_end)
            if clipped_end > clipped_start:
                total_seconds += (clipped_end - clipped_start).total_seconds()
    return round(total_seconds / 3600, 2)


def collect_connector_snapshots(
    config: dict[str, Any], start: date, end: date
) -> dict[str, Any]:
    integrations = config.get("integrations", {})
    snapshots: dict[str, Any] = {}
    statuses = []
    expected_period = {"start": start.isoformat(), "end": end.isoformat()}

    for name, items_key in CONNECTORS.items():
        settings = integrations.get(name, {})
        enabled = bool(settings.get("enabled", False))
        required = bool(settings.get("required", enabled))
        snapshot_value = settings.get("snapshot")
        status: dict[str, Any] = {
            "name": name,
            "category": "connector",
            "auth_mode": str(settings.get("auth_mode", "mcp")),
            "required": required,
            "status": "disabled" if not enabled else "unavailable",
        }
        if not enabled:
            statuses.append(status)
            continue
        if not snapshot_value:
            status["detail"] = "No snapshot path configured"
            statuses.append(status)
            continue

        path = resolve_config_path(config, str(snapshot_value))
        if not path.is_file():
            status["detail"] = "Approved connector snapshot is missing"
            statuses.append(status)
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            status["status"] = "error"
            status["detail"] = f"Cannot read snapshot: {error}"
            statuses.append(status)
            continue

        errors = []
        if data.get("source") != name:
            errors.append(f"source must be {name}")
        if data.get("profile_id") != profile_id(config):
            errors.append("profile_id does not match the Ledger profile")
        if data.get("period") != expected_period:
            errors.append("period does not match the requested week")
        if not parse_timestamp(data.get("captured_at")):
            errors.append("captured_at must be an ISO timestamp")
        if not isinstance(data.get(items_key), list):
            errors.append(f"{items_key} must be a list")
        expected_user = str(settings.get("expected_user", "")).strip()
        if expected_user and not _account_matches(expected_user, data):
            errors.append("snapshot account does not match expected_user")
        if errors:
            status["status"] = "error"
            status["detail"] = "; ".join(errors)
            statuses.append(status)
            continue

        status["status"] = "available"
        status["item_count"] = len(data[items_key])
        status["captured_at"] = data.get("captured_at")
        statuses.append(status)
        snapshots[name] = data

    calendar = snapshots.get("google_calendar", {})
    period_start, period_end = utc_period(start, end, str(config["timezone"]))
    return {
        "period": expected_period,
        "statuses": statuses,
        "snapshots": snapshots,
        "calendar_focus_hours": _focus_hours(
            calendar.get("events", []), period_start, period_end
        )
        if calendar
        else None,
    }
