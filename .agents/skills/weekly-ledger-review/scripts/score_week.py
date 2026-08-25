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

UNAVAILABLE_PLAN_STATUSES = {"disabled", "error", "missing", "stale", "unavailable"}


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


def _optional_nonnegative(data: dict[str, Any], key: str) -> float | None:
    value = data.get(key)
    if value is None:
        return None
    return max(0.0, float(value))


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
    plan_status = str(data.get("plan_status", {}).get("status", "")).lower()
    plan_available = plan_status not in UNAVAILABLE_PLAN_STATUSES
    plan = _weighted_ratio(task_items, "completion") if plan_available else None
    progress = _weighted_ratio(themes, "progress") if themes else None

    target = _optional_nonnegative(data, "target_focus_hours")
    actual = _optional_nonnegative(data, "actual_focus_hours")
    focus_available = target is not None and target > 0 and actual is not None
    focus = _clamp(actual / target) if focus_available else None
    evidence = (
        sum(1 for theme in themes if int(theme.get("evidence_count", 0)) > 0)
        / len(themes)
        if themes
        else None
    )

    components = {
        "plan": round(plan * 100, 1) if plan is not None else None,
        "progress": round(progress * 100, 1) if progress is not None else None,
        "focus": round(focus * 100, 1) if focus is not None else None,
        "evidence": round(evidence * 100, 1) if evidence is not None else None,
    }
    available_weight = sum(
        selected[name] for name, value in components.items() if value is not None
    )
    total = (
        sum(
            (components[name] / 100) * selected[name]
            for name in selected
            if components[name] is not None
        )
        / available_weight
        if available_weight
        else 0.0
    )
    effective_weights = {
        name: (
            round(selected[name] / available_weight, 6)
            if components[name] is not None and available_weight
            else 0.0
        )
        for name in selected
    }

    result = dict(data)
    result["weekly_index"] = {
        "score": round(total * 100, 1),
        "components": components,
        "component_status": {
            name: "available" if value is not None else "unavailable"
            for name, value in components.items()
        },
        "score_coverage": round(available_weight * 100, 1),
        "weights": selected,
        "effective_weights": effective_weights,
    }
    return result
