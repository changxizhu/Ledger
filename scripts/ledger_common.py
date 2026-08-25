#!/usr/bin/env python3
"""Shared configuration, validation, date, and JSON helpers for Ledger."""

from __future__ import annotations

import json
import tomllib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from .credentials import credential_reference_error
    from .time_utils import parse_timestamp
except ImportError:  # Direct script execution.
    from credentials import credential_reference_error
    from time_utils import parse_timestamp


DEFAULT_WEIGHTS = {
    "plan": 0.30,
    "progress": 0.45,
    "focus": 0.15,
    "evidence": 0.10,
}

SECRET_WORDS = ("api_key", "apikey", "password", "secret", "token")
INLINE_CREDENTIAL_PATHS = {
    "integrations.linear.api_key",
    "integrations.google_calendar.client_secret",
    "integrations.google_calendar.refresh_token",
}


def load_config(path: Path) -> dict[str, Any]:
    config_path = path.expanduser().resolve()
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    config["_config_path"] = str(config_path)
    config["_config_root"] = str(config_path.parent)
    return config


def resolve_config_path(config: dict[str, Any], value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(config["_config_root"]) / path
    return path.resolve()


def private_data_dir(config: dict[str, Any]) -> Path:
    return resolve_config_path(config, str(config.get("private_data_dir", ".ledger")))


def identity_aliases(config: dict[str, Any]) -> set[str]:
    identity = config.get("identity", {})
    return {
        str(value).strip().lower()
        for key in ("git_names", "git_emails")
        for value in identity.get(key, [])
        if str(value).strip()
    }


def profile_id(config: dict[str, Any]) -> str:
    return str(config.get("identity", {}).get("person_id", "owner")).strip()


def weekly_weights(config: dict[str, Any]) -> dict[str, float]:
    configured = config.get("weekly_index", {})
    return {
        name: float(configured.get(f"{name}_weight", default))
        for name, default in DEFAULT_WEIGHTS.items()
    }


def integration_backends(settings: dict[str, Any]) -> tuple[str, str]:
    """Return normalized read/write backends with legacy auth_mode support."""

    legacy = str(settings.get("auth_mode", "mcp"))
    legacy_read = "api" if legacy == "direct" else legacy
    read_backend = str(settings.get("read_backend", legacy_read))
    write_backend = str(settings.get("write_backend", "disabled"))
    return read_backend, write_backend


def week_period(week: str) -> tuple[date, date]:
    try:
        year_text, week_text = week.split("-W", 1)
        start = date.fromisocalendar(int(year_text), int(week_text), 1)
    except (TypeError, ValueError) as error:
        raise ValueError("Week must use ISO format YYYY-Www") from error
    return start, start + timedelta(days=7)


def utc_period(start: date, end: date, timezone_name: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone_name)
    local_start = datetime.combine(start, datetime.min.time(), tzinfo=zone)
    local_end = datetime.combine(end, datetime.min.time(), tzinfo=zone)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def validate_config(config: dict[str, Any], check_paths: bool = True) -> list[str]:
    errors: list[str] = []
    security = config.get("security", {})
    if not isinstance(security, dict):
        errors.append("security must be a table")
        security = {}
    allow_inline_credentials = security.get("allow_inline_credentials", False)
    if not isinstance(allow_inline_credentials, bool):
        errors.append("security.allow_inline_credentials must be true or false")
        allow_inline_credentials = False
    timezone_name = str(config.get("timezone", ""))
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        errors.append(f"Unknown timezone: {timezone_name or '<missing>'}")

    identity = config.get("identity", {})
    if not str(identity.get("person_id", "")).strip():
        errors.append("identity.person_id is required")
    if not identity_aliases(config):
        errors.append("At least one Git name or email alias is required")

    weights = weekly_weights(config)
    if any(value < 0 for value in weights.values()):
        errors.append("Weekly index weights cannot be negative")
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        errors.append("Weekly index weights must sum to 1.0")

    repositories = config.get("repositories", [])
    if not repositories:
        errors.append("At least one repository is required")
    seen_names: set[str] = set()
    for index, source in enumerate(repositories):
        label = f"repositories[{index}]"
        name = str(source.get("name", "")).strip()
        if not name:
            errors.append(f"{label}.name is required")
        elif name in seen_names:
            errors.append(f"Duplicate repository name: {name}")
        seen_names.add(name)
        if not str(source.get("path", "")).strip():
            errors.append(f"{label}.path is required")
            continue
        path = Path(str(source["path"])).expanduser().resolve()
        if check_paths and not (path / ".git").exists():
            errors.append(f"Not a Git repository: {path}")
        for prefix in source.get("include", []):
            candidate = Path(str(prefix))
            if candidate.is_absolute() or ".." in candidate.parts:
                errors.append(f"Unsafe include path in {name or label}: {prefix}")

    interaction_names: set[str] = set()
    for index, source in enumerate(config.get("interaction_sources", [])):
        label = f"interaction_sources[{index}]"
        name = str(source.get("name", "")).strip()
        if not name:
            errors.append(f"{label}.name is required")
        elif name in interaction_names:
            errors.append(f"Duplicate interaction source name: {name}")
        interaction_names.add(name)
        if source.get("kind") != "ledger_jsonl":
            errors.append(f"Unsupported interaction source kind in {name or label}")
        if not str(source.get("path", "")).strip():
            errors.append(f"{label}.path is required")

    _validate_interaction_export(config, errors)
    _validate_integrations(config, errors)
    _validate_google_tasks(config, errors)

    allowed_secret_paths = (
        INLINE_CREDENTIAL_PATHS if allow_inline_credentials else set()
    )
    _find_secret_fields(config, "", errors, allowed_secret_paths)
    return errors


def _validate_interaction_export(config: dict[str, Any], errors: list[str]) -> None:
    settings = config.get("interaction_export")
    if settings is None:
        return
    if not isinstance(settings, dict):
        errors.append("interaction_export must be a table")
        return
    for field in ("device_id", "output_dir"):
        if not str(settings.get(field, "")).strip():
            errors.append(f"interaction_export.{field} is required")
    providers = settings.get("providers", ["codex", "claude_code"])
    if not isinstance(providers, list) or not providers:
        errors.append("interaction_export.providers must be a non-empty list")
    elif any(provider not in {"codex", "claude_code"} for provider in providers):
        errors.append("interaction_export.providers contains an unsupported provider")
    lookback_days = settings.get("lookback_days", 8)
    if (
        not isinstance(lookback_days, int)
        or isinstance(lookback_days, bool)
        or lookback_days < 1
    ):
        errors.append("interaction_export.lookback_days must be an integer of at least 1")
    if not isinstance(settings.get("include_content", True), bool):
        errors.append("interaction_export.include_content must be true or false")


def _validate_integrations(config: dict[str, Any], errors: list[str]) -> None:
    integrations = config.get("integrations", {})
    security = config.get("security", {})
    allow_inline_credentials = bool(
        security.get("allow_inline_credentials", False)
        if isinstance(security, dict)
        else False
    )
    for name in ("linear", "google_calendar"):
        settings = integrations.get(name, {})
        enabled = bool(settings.get("enabled", False))
        auth_mode = str(settings.get("auth_mode", "mcp"))
        read_backend, write_backend = integration_backends(settings)
        label = f"integrations.{name}"
        if auth_mode not in {"mcp", "direct"}:
            errors.append(f"{label}.auth_mode must be mcp or direct")
        if read_backend not in {"mcp", "api"}:
            errors.append(f"{label}.read_backend must be mcp or api")
        if write_backend not in {"disabled", "api"}:
            errors.append(f"{label}.write_backend must be disabled or api")
        if "read_backend" in settings:
            legacy_read = "api" if auth_mode == "direct" else auth_mode
            if "auth_mode" in settings and read_backend != legacy_read:
                errors.append(
                    f"{label}.read_backend conflicts with legacy auth_mode"
                )
        browser_fallback = settings.get("allow_browser_fallback", False)
        if not isinstance(browser_fallback, bool):
            errors.append(f"{label}.allow_browser_fallback must be true or false")
        elif browser_fallback:
            errors.append(
                f"{label}.allow_browser_fallback must be false; connector writes never use browser UI"
            )
        if enabled and not str(settings.get("snapshot", "")).strip():
            errors.append(f"{label}.snapshot is required when enabled")
        if name == "linear" and enabled and not str(settings.get("team", "")).strip():
            errors.append(f"{label}.team is required when enabled")
        if not enabled and write_backend != "disabled":
            errors.append(f"{label}.write_backend must be disabled when integration is disabled")
        if not enabled or (read_backend != "api" and write_backend != "api"):
            continue
        if not str(settings.get("expected_user", "")).strip():
            errors.append(f"{label}.expected_user is required for API access")
        credential_fields = (
            (("api_key", "credential_ref", True),)
            if name == "linear"
            else (
                ("client_id", "client_id_ref", True),
                ("refresh_token", "refresh_credential_ref", True),
                ("client_secret", "client_credential_ref", False),
            )
        )
        for inline_field, reference_field, required in credential_fields:
            inline_value = str(settings.get(inline_field, "")).strip()
            reference = str(settings.get(reference_field, "")).strip()
            if inline_value and reference:
                errors.append(
                    f"{label} must configure only one of {inline_field} or {reference_field}"
                )
                continue
            if inline_value:
                if not allow_inline_credentials:
                    errors.append(
                        f"{label}.{inline_field} requires security.allow_inline_credentials = true"
                    )
                continue
            if reference:
                reference_error = credential_reference_error(reference)
                if reference_error:
                    errors.append(
                        f"{label}.{reference_field}: {reference_error}"
                    )
                continue
            if required:
                errors.append(
                    f"{label} requires {inline_field} or {reference_field} for API access"
                )


def _validate_google_tasks(config: dict[str, Any], errors: list[str]) -> None:
    integrations = config.get("integrations", {})
    settings = integrations.get("google_tasks")
    if settings is None:
        return
    if not isinstance(settings, dict):
        errors.append("integrations.google_tasks must be a table")
        return
    label = "integrations.google_tasks"
    enabled = bool(settings.get("enabled", False))
    read_backend, write_backend = integration_backends(settings)
    if not enabled:
        if write_backend != "disabled":
            errors.append(f"{label}.write_backend must be disabled when integration is disabled")
        return
    if read_backend != "api":
        errors.append(f"{label}.read_backend must be api")
    if write_backend != "api":
        errors.append(f"{label}.write_backend must be api")
    if settings.get("allow_browser_fallback", False) is not False:
        errors.append(f"{label}.allow_browser_fallback must be false")
    if str(settings.get("source", "linear")) != "linear":
        errors.append(f"{label}.source must be linear")
    if str(settings.get("credentials_from", "google_calendar")) != "google_calendar":
        errors.append(f"{label}.credentials_from must be google_calendar")
    if not str(settings.get("tasklist", "")).strip():
        errors.append(f"{label}.tasklist is required when enabled")
    if not integrations.get("linear", {}).get("enabled"):
        errors.append(f"{label} requires integrations.linear.enabled = true")
    if not integrations.get("google_calendar", {}).get("enabled"):
        errors.append(f"{label} requires integrations.google_calendar.enabled = true")


def _find_secret_fields(
    value: Any,
    prefix: str,
    errors: list[str],
    allowed_paths: set[str],
) -> None:
    if not isinstance(value, dict):
        return
    for key, child in value.items():
        if str(key).startswith("_"):
            continue
        field = f"{prefix}.{key}" if prefix else str(key)
        lowered = str(key).lower()
        if any(word in lowered for word in SECRET_WORDS) and field not in allowed_paths:
            errors.append(
                f"Secret-like field {field} is not allowed; keep credentials in connectors"
            )
        _find_secret_fields(child, field, errors, allowed_paths)


def validate_weekly_input(data: dict[str, Any]) -> list[str]:
    """Validate relationships that require semantic or cross-record checks."""

    errors: list[str] = []
    try:
        start, end = week_period(str(data.get("week", "")))
    except ValueError as error:
        errors.append(str(error))
        start = end = None

    period = data.get("period")
    if not isinstance(period, dict) or not period.get("start") or not period.get("end"):
        errors.append("period.start and period.end are required")
    elif start:
        if (
            period.get("start") != start.isoformat()
            or period.get("end") != end.isoformat()
        ):
            errors.append("period must match the Monday-to-Monday ISO week interval")

    statuses = data.get("source_status")
    statuses = statuses if isinstance(statuses, list) else []
    missing_required = [
        item.get("name", "unknown")
        for item in statuses
        if item.get("required") and item.get("status") != "available"
    ]
    if missing_required:
        errors.append("Required sources unavailable: " + ", ".join(missing_required))
    return errors
