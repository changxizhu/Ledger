#!/usr/bin/env python3
"""Calculate a weekly index from normalized, user-reviewed inputs."""

from __future__ import annotations

from typing import Any


DEFAULT_WEIGHTS = {
    "plan": 0.30,
    "progress": 0.45,
    "focus": 0.15,
    "evidence": 0.10,
}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _weighted_ratio(items: list[dict[str, Any]], value_key: str) -> float:
    if not items:
        return 0.0
    total_weight = sum(max(0.0, float(item.get("weight", 1))) for item in items)
    if total_weight == 0:
        return 0.0
    return sum(
        max(0.0, float(item.get("weight", 1)))
        * _clamp(float(item.get(value_key, 0)))
        for item in items
    ) / total_weight


def calculate_weekly_index(
    data: dict[str, Any], weights: dict[str, float] | None = None
) -> dict[str, Any]:
    selected = dict(DEFAULT_WEIGHTS if weights is None else weights)
    if abs(sum(selected.values()) - 1.0) > 1e-9:
        raise ValueError("Weekly index weights must sum to 1.0")

    tasks = [task for task in data.get("tasks", []) if not task.get("cancelled")]
    themes = data.get("themes", [])
    task_items = [
        {**task, "completion": 1.0 if task.get("completed") else 0.0}
        for task in tasks
    ]
    plan = _weighted_ratio(task_items, "completion")
    progress = _weighted_ratio(themes, "progress")

    target = max(0.0, float(data.get("target_focus_hours", 0)))
    actual = max(0.0, float(data.get("actual_focus_hours", 0)))
    focus = _clamp(actual / target) if target else 0.0
    evidence = (
        sum(1 for theme in themes if int(theme.get("evidence_count", 0)) > 0)
        / len(themes)
        if themes
        else 0.0
    )

    components = {
        "plan": round(plan * 100, 1),
        "progress": round(progress * 100, 1),
        "focus": round(focus * 100, 1),
        "evidence": round(evidence * 100, 1),
    }
    total = sum((components[name] / 100) * selected[name] for name in selected)

    result = dict(data)
    result["weekly_index"] = {
        "score": round(total * 100, 1),
        "components": components,
        "weights": selected,
    }
    return result
