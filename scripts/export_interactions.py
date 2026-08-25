#!/usr/bin/env python3
"""Incrementally export local Codex and Claude transcripts for Ledger."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import tomllib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from .time_utils import parse_timestamp
except ImportError:  # Direct script execution.
    from time_utils import parse_timestamp


STATE_VERSION = 1
PROVIDER_PATHS = {
    "codex": Path("~/.codex/sessions"),
    "claude_code": Path("~/.claude/projects"),
}
DEFAULT_STATE_FILE = Path("~/.local/state/ledger/interaction-export.json")
SUPPORTED_PROVIDERS = tuple(PROVIDER_PATHS)
SECRET_PATTERNS = (
    re.compile(r"\b(?:sk-(?:proj-|ant-)?|gh[pousr]_)[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b"),
    re.compile(
        r"(?i)(\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|"
        r"password|secret|authorization)\b\s*[:=]\s*)([^\s,;]+)"
    ),
)


def sanitize_device_id(value: str) -> str:
    device_id = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    if not device_id:
        raise ValueError("device ID must contain a letter or number")
    return device_id


def _configured_path(value: Any, config_root: Path) -> Path | None:
    if value is None or not str(value).strip():
        return None
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else config_root / path


def load_export_config(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    with resolved.open("rb") as handle:
        document = tomllib.load(handle)
    settings = document.get("interaction_export")
    if not isinstance(settings, dict):
        raise ValueError(f"Missing [interaction_export] in {resolved}")

    providers = settings.get("providers", list(SUPPORTED_PROVIDERS))
    if not isinstance(providers, list) or not providers:
        raise ValueError("interaction_export.providers must be a non-empty list")
    unsupported = sorted(set(str(value) for value in providers) - set(SUPPORTED_PROVIDERS))
    if unsupported:
        raise ValueError(f"Unsupported interaction providers: {', '.join(unsupported)}")
    lookback_days = settings.get("lookback_days", 8)
    if not isinstance(lookback_days, int) or isinstance(lookback_days, bool):
        raise ValueError("interaction_export.lookback_days must be an integer")
    if lookback_days < 1:
        raise ValueError("interaction_export.lookback_days must be at least 1")
    include_content = settings.get("include_content", True)
    if not isinstance(include_content, bool):
        raise ValueError("interaction_export.include_content must be true or false")

    return {
        "device_id": settings.get("device_id"),
        "output_dir": _configured_path(settings.get("output_dir"), resolved.parent),
        "state_file": _configured_path(settings.get("state_file"), resolved.parent),
        "codex_path": _configured_path(settings.get("codex_path"), resolved.parent),
        "claude_path": _configured_path(settings.get("claude_path"), resolved.parent),
        "providers": [str(value) for value in providers],
        "lookback_days": lookback_days,
        "include_content": include_content,
    }


def redact_content(text: str) -> tuple[str, int]:
    redacted = text
    count = 0
    for index, pattern in enumerate(SECRET_PATTERNS):
        if index == 2:
            redacted, replacements = pattern.subn(r"\1[REDACTED]", redacted)
        elif index == 1:
            redacted, replacements = pattern.subn("Bearer [REDACTED]", redacted)
        else:
            redacted, replacements = pattern.subn("[REDACTED]", redacted)
        count += replacements
    return redacted, count


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": STATE_VERSION, "files": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("version") != STATE_VERSION or not isinstance(state.get("files"), dict):
        raise ValueError(f"Unsupported interaction export state: {path}")
    return state


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _candidate_files(root: Path, cutoff: datetime, known: set[str]) -> list[Path]:
    if root.is_file():
        candidates = [root]
    elif root.is_dir():
        candidates = sorted(root.rglob("*.jsonl"))
    else:
        return []

    selected = []
    for path in candidates:
        key = str(path.resolve())
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if key in known or modified >= cutoff:
            selected.append(path)
    return selected


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _text_from_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(part for part in parts if part)
    return ""


def _export_record(
    event: dict[str, Any],
    *,
    device_id: str,
    include_content: bool,
) -> tuple[dict[str, Any] | None, int]:
    timestamp = parse_timestamp(event.get("timestamp") or event.get("captured_at"))
    if not timestamp:
        return None, 0

    content = _text(event.get("content"))
    redaction_count = 0
    if content:
        content, redaction_count = redact_content(content)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None
    exported = {
        "format_version": 1,
        "captured_at": timestamp.isoformat(),
        "source": event.get("source"),
        "device_id": device_id,
        "role": event.get("role", "unknown"),
        "workspace": event.get("workspace"),
        "session_id": event.get("session_id"),
        "turn_id": event.get("turn_id"),
        "event": event.get("event"),
        "model": event.get("model"),
        "agent_type": event.get("agent_type"),
        "character_count": len(content),
        "content_sha256": content_hash,
    }
    event_key = "|".join(
        str(exported.get(field) or "")
        for field in (
            "source",
            "device_id",
            "session_id",
            "turn_id",
            "role",
            "event",
            "captured_at",
            "content_sha256",
        )
    )
    exported["event_id"] = hashlib.sha256(event_key.encode("utf-8")).hexdigest()
    if include_content and content:
        exported["content"] = content
    return exported, redaction_count


def _normalize_codex_rollout(
    record: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any] | None:
    record_type = record.get("type")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None

    if record_type == "session_meta":
        context["session_id"] = payload.get("id") or payload.get("session_id")
        context["workspace"] = Path(str(payload.get("cwd") or "")).name or None
        return None
    if record_type == "turn_context":
        context["turn_id"] = payload.get("turn_id") or context.get("turn_id")
        context["model"] = payload.get("model") or context.get("model")
        cwd = str(payload.get("cwd") or "")
        if cwd:
            context["workspace"] = Path(cwd).name
        return None
    if record_type != "event_msg":
        return None

    event_type = payload.get("type")
    if event_type == "task_started":
        context["turn_id"] = payload.get("turn_id") or context.get("turn_id")
        return None
    if event_type not in {"user_message", "agent_message"}:
        return None
    if event_type == "agent_message" and payload.get("phase") not in {
        None,
        "",
        "final_answer",
    }:
        return None

    content = _text(payload.get("message"))
    return {
        "timestamp": record.get("timestamp"),
        "source": "codex",
        "role": "user" if event_type == "user_message" else "assistant",
        "workspace": context.get("workspace"),
        "session_id": context.get("session_id"),
        "turn_id": context.get("turn_id"),
        "event": event_type,
        "model": context.get("model"),
        "agent_type": None,
        "content": content,
    }


def _normalize_claude_record(
    record: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any] | None:
    del context
    if record.get("type") not in {"user", "assistant", "system"}:
        return None
    timestamp = parse_timestamp(record.get("timestamp") or record.get("created_at"))
    if not timestamp:
        return None
    message = record.get("message", {})
    role = message.get("role") if isinstance(message, dict) else None
    role = role or record.get("type") or record.get("role") or "unknown"
    raw_content = message.get("content") if isinstance(message, dict) else None
    content = _text_from_content(
        raw_content if raw_content is not None else record.get("content")
    )
    cwd = str(record.get("cwd") or "")
    return {
        "timestamp": timestamp.isoformat(),
        "source": "claude_code",
        "role": role,
        "workspace": Path(cwd).name if cwd else record.get("workspace"),
        "session_id": record.get("sessionId") or record.get("session_id"),
        "turn_id": record.get("uuid") or record.get("messageId"),
        "event": record.get("type"),
        "model": (
            message.get("model") if isinstance(message, dict) else None
        )
        or record.get("model"),
        "agent_type": "sidechain" if record.get("isSidechain") else None,
        "content": content,
    }


def _read_increment(
    path: Path,
    file_state: dict[str, Any],
    normalizer: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any] | None],
    *,
    device_id: str,
    include_content: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any], int, int]:
    context = dict(file_state.get("context") or {})
    offset = int(file_state.get("offset", 0) or 0)
    try:
        size = path.stat().st_size
    except OSError:
        return [], file_state, 0, 0
    if offset > size:
        offset = 0
        context = {}

    events = []
    malformed = 0
    redactions = 0
    with path.open("rb") as handle:
        handle.seek(offset)
        while True:
            line_start = handle.tell()
            raw_line = handle.readline()
            if not raw_line:
                break
            if not raw_line.endswith(b"\n"):
                handle.seek(line_start)
                break
            offset = handle.tell()
            if not raw_line.strip():
                continue
            try:
                raw = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                malformed += 1
                continue
            event = normalizer(raw, context)
            if not event:
                continue
            exported, replacement_count = _export_record(
                event,
                device_id=device_id,
                include_content=include_content,
            )
            if exported:
                events.append(exported)
                redactions += replacement_count

    return events, {"offset": offset, "context": context}, malformed, redactions


def _existing_event_ids(directory: Path, event_date: str) -> set[str]:
    event_ids: set[str] = set()
    if not directory.is_dir():
        return event_ids
    for candidate in sorted(directory.glob(f"{event_date}*.jsonl")):
        with candidate.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_id = record.get("event_id") if isinstance(record, dict) else None
                if isinstance(event_id, str) and event_id:
                    event_ids.add(event_id)
    return event_ids


def _set_private_mode_if_supported(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError as error:
        unsupported = {errno.EOPNOTSUPP, errno.ENOTSUP, errno.ENOSYS}
        if error.errno not in unsupported:
            raise


def _write_event_batches(
    output_root: Path,
    events: list[dict[str, Any]],
    progress: Callable[[str], None] | None = None,
) -> tuple[list[str], int, int]:
    grouped: dict[tuple[Path, str], list[dict[str, Any]]] = {}
    for event in events:
        timestamp = parse_timestamp(event["captured_at"])
        if not timestamp:
            continue
        directory = (
            output_root / str(event["device_id"]) / str(event["source"])
        )
        grouped.setdefault((directory, timestamp.date().isoformat()), []).append(event)

    output_files = []
    written_count = 0
    duplicate_count = 0
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_id = f"{run_id}-{uuid.uuid4().hex[:8]}"
    batches = sorted(
        grouped.items(), key=lambda item: (str(item[0][0]), item[0][1])
    )
    for batch_index, ((directory, event_date), records) in enumerate(batches, start=1):
        label = f"{directory.name} {event_date}"
        if progress:
            progress(f"Checking destination {batch_index}/{len(batches)}: {label}...")
        directory.mkdir(parents=True, exist_ok=True)
        existing_ids = _existing_event_ids(directory, event_date)
        pending = []
        for record in records:
            event_id = record.get("event_id")
            if isinstance(event_id, str) and event_id in existing_ids:
                duplicate_count += 1
                continue
            pending.append(record)
            if isinstance(event_id, str) and event_id:
                existing_ids.add(event_id)
        if not pending:
            if progress:
                progress(f"Already present: {label} ({len(records)} events).")
            continue

        path = directory / f"{event_date}--{run_id}.jsonl"
        payload = "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in pending
        )
        size_mib = len(payload.encode("utf-8")) / (1024 * 1024)
        if progress:
            progress(
                f"Uploading {batch_index}/{len(batches)}: {label} "
                f"({len(pending)} events, {size_mib:.1f} MiB)..."
            )
        path.write_text(payload, encoding="utf-8")
        _set_private_mode_if_supported(path)
        output_files.append(str(path))
        written_count += len(pending)
        if progress:
            progress(f"Uploaded: {path.name}")
    return output_files, written_count, duplicate_count


def export_interactions(
    *,
    sources: dict[str, Path],
    output_dir: Path,
    state_file: Path,
    device_id: str,
    lookback_days: int = 8,
    include_content: bool = True,
    dry_run: bool = False,
    now: datetime | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if lookback_days < 1:
        raise ValueError("lookback_days must be at least 1")
    device_id = sanitize_device_id(device_id)
    output_dir = output_dir.expanduser().resolve()
    state_file = state_file.expanduser().resolve()
    state = _load_state(state_file)
    run_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    next_state = {
        "version": STATE_VERSION,
        "files": dict(state["files"]),
        "last_run_at": run_time.isoformat(),
        "device_id": device_id,
        "output_dir": str(output_dir),
    }
    cutoff = run_time - timedelta(days=lookback_days)

    all_events = []
    statuses = []
    total_redactions = 0
    total_malformed = 0
    normalizers = {
        "codex": _normalize_codex_rollout,
        "claude_code": _normalize_claude_record,
    }
    known = set(state["files"])

    for provider, configured_path in sources.items():
        if provider not in normalizers:
            raise ValueError(f"Unsupported interaction provider: {provider}")
        root = configured_path.expanduser().resolve()
        if progress:
            progress(f"Scanning {provider} transcripts...")
        files = _candidate_files(root, cutoff, known)
        if progress:
            progress(f"Reading {provider}: {len(files)} transcript files selected.")
        provider_events = 0
        provider_malformed = 0
        for file_index, path in enumerate(files, start=1):
            if progress:
                progress(f"Reading {provider} file {file_index}/{len(files)}...")
            key = str(path.resolve())
            events, updated, malformed, redactions = _read_increment(
                path,
                state["files"].get(key, {}),
                normalizers[provider],
                device_id=device_id,
                include_content=include_content,
            )
            next_state["files"][key] = updated
            all_events.extend(events)
            provider_events += len(events)
            provider_malformed += malformed
            total_redactions += redactions
        total_malformed += provider_malformed
        statuses.append(
            {
                "provider": provider,
                "path": str(root),
                "status": "available" if root.exists() else "unavailable",
                "file_count": len(files),
                "new_event_count": provider_events,
                "malformed_record_count": provider_malformed,
            }
        )
        if progress:
            progress(f"Prepared {provider}: {provider_events} new events.")

    all_events.sort(key=lambda event: (event["captured_at"], event["event_id"]))
    if dry_run:
        output_files = []
        written_event_count = 0
        duplicate_event_count = 0
    else:
        if progress:
            progress(f"Preparing {len(all_events)} events for the destination...")
        output_files, written_event_count, duplicate_event_count = _write_event_batches(
            output_dir, all_events, progress
        )
    if not dry_run:
        if progress:
            progress("Saving the local incremental cursor...")
        _write_state(state_file, next_state)
        if progress:
            progress("Export complete.")

    return {
        "device_id": device_id,
        "output_dir": str(output_dir),
        "state_file": str(state_file),
        "dry_run": dry_run,
        "content_included": include_content,
        "redaction_enabled": True,
        "new_event_count": len(all_events),
        "written_event_count": written_event_count,
        "duplicate_event_count": duplicate_event_count,
        "redacted_value_count": total_redactions,
        "malformed_record_count": total_malformed,
        "output_files": output_files,
        "sources": statuses,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        help="Private TOML file containing [interaction_export] device settings.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Override the configured synchronized output directory.",
    )
    parser.add_argument(
        "--device-id",
        help="Override the configured stable device label.",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        help="Override the configured local incremental-state file.",
    )
    parser.add_argument("--codex-path", type=Path)
    parser.add_argument(
        "--claude-path", type=Path
    )
    parser.add_argument(
        "--provider",
        choices=("both", "codex", "claude_code"),
        help="Override the configured provider list.",
    )
    parser.add_argument("--lookback-days", type=int)
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        configured = load_export_config(args.config) if args.config else {}
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        parser.error(str(error))

    output_dir = (
        args.output_dir
        or os.environ.get("LEDGER_INTERACTION_EXPORT_DIR")
        or configured.get("output_dir")
    )
    device_id = (
        args.device_id or os.environ.get("LEDGER_DEVICE_ID") or configured.get("device_id")
    )
    state_file = args.state_file or configured.get("state_file") or DEFAULT_STATE_FILE
    lookback_days = (
        args.lookback_days
        if args.lookback_days is not None
        else int(configured.get("lookback_days", 8))
    )
    include_content = bool(configured.get("include_content", True)) and not args.metadata_only

    if output_dir is None:
        parser.error("output_dir is required in [interaction_export] or --output-dir")
    if not device_id:
        parser.error("device_id is required in [interaction_export] or --device-id")

    available_sources = {
        "codex": args.codex_path
        or configured.get("codex_path")
        or PROVIDER_PATHS["codex"],
        "claude_code": args.claude_path
        or configured.get("claude_path")
        or PROVIDER_PATHS["claude_code"],
    }
    providers = configured.get("providers", list(SUPPORTED_PROVIDERS))
    if args.provider:
        providers = list(SUPPORTED_PROVIDERS) if args.provider == "both" else [args.provider]
    sources = {provider: available_sources[provider] for provider in providers}

    result = export_interactions(
        sources=sources,
        output_dir=Path(output_dir),
        state_file=Path(state_file),
        device_id=str(device_id),
        lookback_days=lookback_days,
        include_content=include_content,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
