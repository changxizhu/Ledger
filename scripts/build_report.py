#!/usr/bin/env python3
"""Render a concise Markdown review from a scored weekly record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def render_report(data: dict[str, Any]) -> str:
    index = data["weekly_index"]
    components = index["components"]
    tasks = data.get("tasks", [])
    themes = data.get("themes", [])
    done = [task for task in tasks if task.get("completed")]
    open_tasks = [task for task in tasks if not task.get("completed")]

    lines = [
        f"# Weekly Review: {data['week']}",
        "",
        f"**Weekly index:** {index['score']}/100",
        "",
        "| Plan | Progress | Focus time | Evidence |",
        "| ---: | ---: | ---: | ---: |",
        (
            f"| {components['plan']} | {components['progress']} | "
            f"{components['focus']} | {components['evidence']} |"
        ),
        "",
        "## Themes",
        "",
    ]
    for theme in themes:
        progress = round(float(theme.get("progress", 0)) * 100)
        lines.append(f"- **{theme['name']} ({progress}%):** {theme.get('summary', '')}")

    lines.extend(["", "## Completed", ""])
    lines.extend(f"- {task['title']}" for task in done)
    if not done:
        lines.append("- None recorded")

    lines.extend(["", "## Carry forward", ""])
    lines.extend(f"- {task['title']}" for task in open_tasks)
    if not open_tasks:
        lines.append("- Nothing to carry forward")

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The index is supporting evidence, not a judgment of personal value. "
            "Review unexpected scores against the underlying tasks and themes.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8"))
    report = render_report(data)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    else:
        print(report, end="")


if __name__ == "__main__":
    main()

