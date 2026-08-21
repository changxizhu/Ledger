#!/usr/bin/env python3
"""Create a public-safe weekly aggregate from a private scored record."""

from __future__ import annotations

from typing import Any


def sanitize_week(
    data: dict[str, Any], include_public_names: bool = False
) -> dict[str, Any]:
    index = data["weekly_index"]
    themes = []
    for number, theme in enumerate(data.get("themes", []), start=1):
        themes.append(
            {
                "name": (
                    theme.get("public_name")
                    if include_public_names and theme.get("public_name")
                    else f"Theme {number}"
                ),
                "progress": round(float(theme.get("progress", 0)) * 100, 1),
            }
        )
    return {
        "schema_version": 1,
        "week": data["week"],
        "score": index["score"],
        "components": index["components"],
        "completed_task_count": sum(
            1 for task in data.get("tasks", []) if task.get("completed")
        ),
        "planned_task_count": len(data.get("tasks", [])),
        "theme_count": len(themes),
        "themes": themes,
    }
