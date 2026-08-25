#!/usr/bin/env python3
"""Aggregate finalized weekly records into a private monthly review."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ledger_common import write_json


def _month_period(month: str) -> tuple[date, date]:
    start = date.fromisoformat(f"{month}-01")
    end = date(start.year + (start.month == 12), (start.month % 12) + 1, 1)
    return start, end


def aggregate(records: list[dict[str, Any]], month: str) -> dict[str, Any]:
    # Inputs are ordered oldest to newest; a rerun replaces the earlier record
    # for the same ISO week without deleting the immutable private artifacts.
    month_start, month_end = _month_period(month)
    latest_by_week: dict[str, dict[str, Any]] = {}
    for record in records:
        period = record.get("period", {})
        period_start = date.fromisoformat(str(period.get("start", "")))
        period_end = date.fromisoformat(str(period.get("end", "")))
        if period_start < month_end and period_end > month_start:
            latest_by_week[str(record.get("week", "unknown"))] = record
    selected = list(latest_by_week.values())
    components: dict[str, list[float]] = defaultdict(list)
    scores = []
    theme_progress: dict[str, list[float]] = defaultdict(list)
    carry_forward = Counter()
    for record in selected:
        index = record.get("weekly_index", {})
        if isinstance(index.get("score"), (int, float)):
            scores.append(float(index["score"]))
        for name, value in index.get("components", {}).items():
            components[name].append(float(value))
        for theme in record.get("themes", []):
            theme_progress[str(theme.get("name", "Unnamed"))].append(
                float(theme.get("progress", 0)) * 100
            )
        carry_forward.update(
            task["title"]
            for task in record.get("tasks", [])
            if not task.get("completed")
            and not task.get("cancelled")
            and task.get("title")
        )

    return {
        "schema_version": 1,
        "month": month,
        "week_count": len(selected),
        "average_score": round(sum(scores) / len(scores), 1) if scores else None,
        "average_components": {
            name: round(sum(values) / len(values), 1)
            for name, values in components.items()
            if values
        },
        "themes": [
            {
                "name": name,
                "weeks": len(values),
                "average_progress": round(sum(values) / len(values), 1),
            }
            for name, values in sorted(theme_progress.items())
        ],
        "recurring_carry_forward": [
            {"title": title, "weeks": count}
            for title, count in carry_forward.most_common()
            if count > 1
        ],
    }


def _records(paths: list[Path]) -> list[dict[str, Any]]:
    records = []
    for path in paths:
        records.append(json.loads(path.read_text(encoding="utf-8")))
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", required=True, help="Month in YYYY-MM format")
    parser.add_argument("--runs-dir", type=Path)
    parser.add_argument("inputs", type=Path, nargs="*")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    paths = list(args.inputs)
    if args.runs_dir:
        paths.extend(sorted(args.runs_dir.glob("*-W*/*/weekly-scored.json")))
    result = aggregate(_records(paths), args.month)
    if args.output:
        write_json(args.output, result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
