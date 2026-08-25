#!/usr/bin/env python3
"""Render a private, human-readable verification table from evidence.json."""

from __future__ import annotations

from html import escape
from datetime import datetime
from pathlib import Path
from typing import Any


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = str(value).replace("|", "\\|").replace("\n", "<br>")
    return text


def _status_value(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("type") or value.get("status") or "-")
    return str(value) if value not in (None, "") else "-"


def _changes(files: list[dict[str, Any]]) -> tuple[int, int]:
    added = sum(int(item.get("added") or 0) for item in files)
    deleted = sum(int(item.get("deleted") or 0) for item in files)
    return added, deleted


def _duration_hours(event: dict[str, Any]) -> str:
    try:
        start = datetime.fromisoformat(str(event["start"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(event["end"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return "-"
    if end <= start:
        return "-"
    return f"{(end - start).total_seconds() / 3600:.2f}"


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    if rows:
        lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    else:
        lines.append("| " + " | ".join(["No records", *("-" for _ in headers[1:])]) + " |")
    return lines


def _diff_block(summary: str, patch: Any, truncated: bool) -> list[str]:
    text = str(patch or "")
    lines = ["<details>", f"<summary>{escape(summary)}</summary>", ""]
    if text:
        fence = "```"
        while fence in text:
            fence += "`"
        lines.extend([f"{fence}diff", text, fence])
    else:
        lines.append("No textual patch was captured for this file.")
    if truncated:
        lines.extend(["", "_Patch truncated at the configured collection limit._"])
    lines.extend(["", "</details>", ""])
    return lines


def render_evidence_report(evidence: dict[str, Any]) -> str:
    lines = [
        f"# Evidence Verification: {evidence['week']}",
        "",
        f"**Period:** {evidence['period']['start']} to {evidence['period']['end']}  ",
        f"**Timezone:** {evidence.get('timezone', '-')}  ",
        f"**Collected:** {evidence.get('collected_at', '-')}  ",
        "**Privacy:** Private. Do not publish this report or the source evidence.",
        "",
        "## Verification checklist",
        "",
        "- [ ] Repository sync and dirty states are correct.",
        "- [ ] Commit dates, subjects, and changed-file counts belong to this week.",
        "- [ ] Working-tree files are expected and should be included in the review.",
        "- [ ] Interaction source coverage and date range are plausible.",
        "- [ ] Linear tasks match the authoritative weekly plan.",
        "- [ ] Calendar events and focus hours match the requested period.",
        "- [ ] Missing, disabled, stale, or errored sources are acceptable before analysis.",
        "",
        "## Source status",
        "",
    ]
    source_rows = []
    for source in evidence.get("source_status", []):
        detail = source.get("detail") or source.get("sync_state") or source.get("kind")
        source_rows.append(
            [
                "Pending",
                source.get("name"),
                source.get("category"),
                source.get("required", False),
                source.get("status"),
                detail,
                source.get("event_count", source.get("item_count")),
            ]
        )
    lines.extend(
        _table(
            ["Review", "Source", "Category", "Required", "Status", "Detail", "Items"],
            source_rows,
        )
    )

    lines.extend(["", "## Repository synchronization", ""])
    sync_rows = [
        [
            item.get("name") or Path(str(item.get("path", ""))).name,
            item.get("state"),
            item.get("dirty"),
            item.get("ahead"),
            item.get("behind"),
        ]
        for item in evidence.get("sync", {}).get("repositories", [])
    ]
    lines.extend(_table(["Repository", "State", "Dirty", "Ahead", "Behind"], sync_rows))

    lines.extend(["", "## Commits", ""])
    commit_rows = []
    for event in evidence.get("git", {}).get("events", []):
        files = event.get("files", [])
        added, deleted = _changes(files)
        commit_rows.append(
            [
                "Pending",
                event.get("source"),
                str(event.get("commit", ""))[:8],
                event.get("authored_at"),
                event.get("subject"),
                len(files),
                added,
                deleted,
            ]
        )
    lines.extend(
        _table(
            ["Review", "Repository", "Commit", "Authored", "Subject", "Files", "+", "-"],
            commit_rows,
        )
    )

    lines.extend(["", "## Working-tree changes", ""])
    worktree_rows = []
    for event in evidence.get("git", {}).get("worktree_events", []):
        for item in event.get("files", []):
            worktree_rows.append(
                [
                    "Pending",
                    event.get("source"),
                    item.get("status"),
                    item.get("path"),
                    item.get("added"),
                    item.get("deleted"),
                    item.get("binary", False),
                    item.get("patch_truncated", False),
                ]
            )
    lines.extend(
        _table(
            ["Review", "Repository", "Status", "Path", "+", "-", "Binary", "Patch truncated"],
            worktree_rows,
        )
    )

    lines.extend(["", "## Git diff details", ""])
    diff_count = 0
    for event in evidence.get("git", {}).get("events", []):
        for item in event.get("files", []):
            diff_count += 1
            summary = (
                f"Commit | {event.get('source', '-')} | "
                f"{str(event.get('commit', ''))[:8]} | {item.get('path', '-')} | "
                f"+{item.get('added', 0)} -{item.get('deleted', 0)}"
            )
            lines.extend(
                _diff_block(
                    summary,
                    item.get("patch"),
                    bool(item.get("patch_truncated", False)),
                )
            )
    for event in evidence.get("git", {}).get("worktree_events", []):
        for item in event.get("files", []):
            diff_count += 1
            summary = (
                f"Worktree | {event.get('source', '-')} | {item.get('status', '-')} | "
                f"{item.get('path', '-')} | +{item.get('added', 0)} -{item.get('deleted', 0)}"
            )
            lines.extend(
                _diff_block(
                    summary,
                    item.get("patch"),
                    bool(item.get("patch_truncated", False)),
                )
            )
    if diff_count == 0:
        lines.append("No Git file changes were collected.")

    lines.extend(["", "## Interaction sources", ""])
    interaction_rows = [
        [
            item.get("name"),
            item.get("kind"),
            item.get("status"),
            item.get("file_count"),
            item.get("event_count"),
            item.get("skipped_record_count"),
            item.get("malformed_record_count"),
        ]
        for item in evidence.get("interactions", {}).get("sources", [])
    ]
    lines.extend(
        _table(
            ["Source", "Kind", "Status", "Files", "Weekly events", "Skipped types", "Malformed"],
            interaction_rows,
        )
    )

    lines.extend(["", "## Interaction events", ""])
    event_rows = [
        [
            event.get("source_name") or event.get("source"),
            event.get("timestamp"),
            event.get("role"),
            event.get("workspace"),
            event.get("character_count"),
            str(event.get("content_sha256") or "")[:12],
        ]
        for event in evidence.get("interactions", {}).get("events", [])
    ]
    lines.extend(
        _table(
            ["Source", "Timestamp", "Role", "Workspace", "Characters", "Content hash"],
            event_rows,
        )
    )

    linear = evidence.get("connectors", {}).get("snapshots", {}).get("linear", {})
    lines.extend(["", "## Linear tasks", ""])
    linear_rows = [
        [
            issue.get("id"),
            issue.get("title"),
            _status_value(issue.get("status")),
            issue.get("completed"),
            issue.get("weight"),
        ]
        for issue in linear.get("issues", [])
    ]
    lines.extend(_table(["ID", "Title", "Status", "Completed", "Weight"], linear_rows))

    calendar = evidence.get("connectors", {}).get("snapshots", {}).get("google_calendar", {})
    lines.extend(["", "## Calendar events", ""])
    calendar_rows = [
        [
            event.get("id"),
            event.get("title") or event.get("summary"),
            event.get("kind") or event.get("event_type"),
            event.get("start"),
            event.get("end"),
            _duration_hours(event),
        ]
        for event in calendar.get("events", [])
    ]
    lines.extend(_table(["ID", "Title", "Kind", "Start", "End", "Hours"], calendar_rows))
    lines.extend(
        [
            "",
            f"**Calculated focus hours:** {_cell(evidence.get('connectors', {}).get('calendar_focus_hours'))}",
            "",
            "## Verification result",
            "",
            "- Status: Pending user review",
            "- Corrections or exclusions:",
            "- Approved for theme analysis: No",
            "",
            "Git patches are included above for private verification. Interaction text remains only in `evidence.json`.",
            "",
        ]
    )
    return "\n".join(lines)
