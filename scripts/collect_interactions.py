#!/usr/bin/env python3
"""Collect and inspect synchronized Ledger interaction events."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import platform
from datetime import date
from pathlib import Path
from typing import Any

try:
    from .ledger_common import load_config, parse_timestamp, utc_period, week_period
    from .schema_validation import contract_errors
except ImportError:  # Direct script execution.
    from ledger_common import load_config, parse_timestamp, utc_period, week_period
    from schema_validation import contract_errors


def _candidate_files(path_text: str) -> list[Path]:
    expanded = str(Path(path_text).expanduser())
    if any(character in expanded for character in "*?["):
        return sorted(Path(value) for value in glob.glob(expanded, recursive=True))
    path = Path(expanded)
    if path.is_dir():
        return sorted(path.rglob("*.jsonl"))
    return [path]


def _text_from_content(value: Any) -> str:
    return value if isinstance(value, str) else ""


def normalize_ledger(record: dict[str, Any], include_content: bool) -> dict[str, Any] | None:
    if contract_errors("interaction-event", record):
        return None
    timestamp = parse_timestamp(record["captured_at"])
    if not timestamp:
        return None
    content = _text_from_content(record.get("content"))
    normalized = {
        "timestamp": timestamp.isoformat(),
        "source": record["source"],
        "device_id": record["device_id"],
        "role": record["role"],
        "workspace": record.get("workspace"),
        "session_id": record.get("session_id"),
        "turn_id": record.get("turn_id"),
        "event": record.get("event"),
        "event_id": record["event_id"],
        "model": record.get("model"),
        "agent_type": record.get("agent_type"),
        "character_count": record["character_count"],
        "content_sha256": record.get("content_sha256")
        or (hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None),
    }
    if include_content and content:
        normalized["content"] = content
    return normalized


def _resolved_source(config: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(source)
    path = Path(str(source["path"])).expanduser()
    if not path.is_absolute():
        path = Path(config["_config_root"]) / path
    resolved["path"] = str(path)
    return resolved


def _scan_source(
    source: dict[str, Any],
    *,
    include_content: bool,
    start_utc: Any = None,
    end_utc: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    files = [path for path in _candidate_files(str(source["path"])) if path.is_file()]
    events: list[dict[str, Any]] = []
    record_count = 0
    accepted_count = 0
    invalid_count = 0
    malformed_count = 0
    earliest = latest = None

    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            record_count += 1
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                malformed_count += 1
                continue
            event = normalize_ledger(raw, include_content)
            if not event:
                invalid_count += 1
                continue
            accepted_count += 1
            timestamp = parse_timestamp(event["timestamp"])
            if not timestamp:
                invalid_count += 1
                accepted_count -= 1
                continue
            earliest = timestamp if earliest is None or timestamp < earliest else earliest
            latest = timestamp if latest is None or timestamp > latest else latest
            if start_utc is None or end_utc is None or start_utc <= timestamp < end_utc:
                event["source_name"] = source["name"]
                events.append(event)

    return events, {
        "name": source["name"],
        "category": "interactions",
        "kind": "ledger_jsonl",
        "required": bool(source.get("required", False)),
        "status": "available" if files else "unavailable",
        "configured_path": str(source["path"]),
        "file_count": len(files),
        "record_count": record_count,
        "accepted_record_count": accepted_count,
        "invalid_record_count": invalid_count,
        "malformed_record_count": malformed_count,
        "event_count": len(events),
        "earliest_event": earliest.isoformat() if earliest else None,
        "latest_event": latest.isoformat() if latest else None,
    }


def collect_interactions(
    config: dict[str, Any], start: date, end: date
) -> dict[str, Any]:
    start_utc, end_utc = utc_period(start, end, str(config["timezone"]))
    events: list[dict[str, Any]] = []
    sources = []
    for source in config.get("interaction_sources", []):
        source_events, status = _scan_source(
            _resolved_source(config, source),
            include_content=bool(source.get("include_content", False)),
            start_utc=start_utc,
            end_utc=end_utc,
        )
        events.extend(source_events)
        sources.append(status)

    events.sort(key=lambda item: item["timestamp"])
    unique_events = []
    seen: set[str] = set()
    duplicate_count = 0
    for event in events:
        event_id = str(event["event_id"])
        if event_id in seen:
            duplicate_count += 1
            continue
        seen.add(event_id)
        unique_events.append(event)
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "event_count": len(unique_events),
        "duplicate_count": duplicate_count,
        "sources": sources,
        "events": unique_events,
    }


def inspect_sources(config: dict[str, Any], week: str | None = None) -> dict[str, Any]:
    if week:
        start, end = week_period(week)
        start_utc, end_utc = utc_period(start, end, str(config["timezone"]))
    else:
        start = end = None
        start_utc = end_utc = None

    results = []
    for source in config.get("interaction_sources", []):
        resolved = _resolved_source(config, source)
        _, status = _scan_source(
            resolved,
            include_content=False,
            start_utc=start_utc,
            end_utc=end_utc,
        )
        status["resolved_path"] = str(Path(str(resolved["path"])).resolve())
        status["exists"] = Path(str(resolved["path"])).exists() or status["file_count"] > 0
        status["requested_week"] = week
        status["requested_week_event_count"] = status.pop("event_count") if week else None
        results.append(status)

    return {
        "platform": platform.system(),
        "timezone": config.get("timezone"),
        "week": week,
        "period": {"start": start.isoformat(), "end": end.isoformat()} if week else None,
        "sources": results,
        "content_printed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser(
        "inspect", help="Report file and date coverage without printing message content."
    )
    inspect_parser.add_argument("--config", type=Path, required=True)
    inspect_parser.add_argument("--week")
    args = parser.parse_args()

    if args.command == "inspect":
        result = inspect_sources(load_config(args.config), args.week)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
