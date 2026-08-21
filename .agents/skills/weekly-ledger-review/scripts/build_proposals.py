#!/usr/bin/env python3
"""Build reviewable Linear and Calendar proposals without making external writes."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo


def build_proposals(
    data: dict[str, Any], timezone_name: str, report_path: str | None = None
) -> dict[str, Any]:
    period_end = date.fromisoformat(data["period"]["end"])
    next_tasks = list(data.get("next_plan", {}).get("tasks", []))
    carry_forward = [
        {
            "title": task["title"],
            "weight": task.get("weight", 1),
            "origin": "carry_forward",
        }
        for task in data.get("tasks", [])
        if not task.get("completed") and not task.get("cancelled")
    ]
    candidates = carry_forward + [
        {**task, "origin": task.get("origin", "next_plan")} for task in next_tasks
    ]
    planned = []
    seen_tasks = set()
    for task in candidates:
        key = task.get("id") or str(task.get("title", "")).strip().casefold()
        if not key or key in seen_tasks:
            continue
        seen_tasks.add(key)
        planned.append(task)

    zone = ZoneInfo(timezone_name)
    review_start = datetime.combine(period_end, time(9, 0), tzinfo=zone)
    review_end = review_start + timedelta(minutes=15)
    description_lines = ["Review the weekly study plan."]
    if report_path:
        description_lines.append(f"Weekly report: {report_path}")
    if planned:
        description_lines.append("Tasks:")
        description_lines.extend(f"- {task['title']}" for task in planned)

    return {
        "approval_required": True,
        "linear": {
            "action": "prepare_next_week",
            "week_start": period_end.isoformat(),
            "tasks": planned,
        },
        "google_calendar": {
            "action": "upsert_single_reminder",
            "title": "Review weekly study plan",
            "start": review_start.isoformat(),
            "end": review_end.isoformat(),
            "timezone": timezone_name,
            "popup_minutes_before": 10,
            "description": "\n".join(description_lines),
            "preserve_existing_time": True,
        },
    }
