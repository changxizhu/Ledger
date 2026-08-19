#!/usr/bin/env python3
"""Capture privacy-aware Codex hook events as local JSONL evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


TEXT_FIELDS = {
    "UserPromptSubmit": ("user", "prompt"),
    "Stop": ("assistant", "last_assistant_message"),
    "SubagentStop": ("subagent", "last_assistant_message"),
}


def default_output(payload: dict[str, Any]) -> Path:
    configured = os.environ.get("LEDGER_INTERACTION_INBOX")
    if configured:
        return Path(configured).expanduser()

    cwd = Path(str(payload.get("cwd") or Path.cwd())).expanduser()
    process = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if process.returncode == 0:
        return Path(process.stdout.strip()) / ".ledger" / "interactions.jsonl"
    return Path.home() / ".local" / "share" / "ledger" / "interactions.jsonl"


def normalize_event(payload: dict[str, Any], include_content: bool) -> dict[str, Any]:
    event_name = str(payload.get("hook_event_name", "Unknown"))
    role, field = TEXT_FIELDS.get(event_name, ("system", ""))
    text = str(payload.get(field) or "") if field else ""
    cwd = Path(str(payload.get("cwd") or ""))

    record: dict[str, Any] = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": "codex",
        "event": event_name,
        "role": role,
        "session_id": payload.get("session_id"),
        "turn_id": payload.get("turn_id"),
        "model": payload.get("model"),
        "workspace": cwd.name or None,
        "agent_id": payload.get("agent_id"),
        "agent_type": payload.get("agent_type"),
        "character_count": len(text),
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
    }
    if include_content and text:
        record["content"] = text
    return record


def append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(line)
        handle.flush()
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    os.chmod(path, 0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--include-content", action="store_true")
    args = parser.parse_args()

    payload = json.load(sys.stdin)
    output = args.output.expanduser() if args.output else default_output(payload)
    append_record(output, normalize_event(payload, args.include_content))

    # Stop hooks require valid JSON on stdout; an empty object means continue.
    if payload.get("hook_event_name") in {"Stop", "SubagentStop"}:
        print("{}")


if __name__ == "__main__":
    main()
