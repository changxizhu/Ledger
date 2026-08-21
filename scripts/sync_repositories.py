#!/usr/bin/env python3
"""Inspect or safely fast-forward configured repositories."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

try:
    from .ledger_common import load_config, validate_config
except ImportError:  # Direct script execution.
    from ledger_common import load_config, validate_config


def _git(
    repo: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def inspect(repo: Path, fetch: bool) -> dict[str, object]:
    if fetch:
        _git(repo, "fetch", "--prune")
    dirty = bool(_git(repo, "status", "--porcelain").stdout.strip())
    upstream = _git(repo, "rev-parse", "--abbrev-ref", "@{upstream}", check=False)
    if upstream.returncode != 0:
        return {"path": str(repo), "dirty": dirty, "state": "no-upstream"}
    counts = _git(
        repo, "rev-list", "--left-right", "--count", "HEAD...@{upstream}"
    ).stdout
    ahead, behind = (int(value) for value in counts.split())
    if ahead and behind:
        state = "diverged"
    elif ahead:
        state = "ahead"
    elif behind:
        state = "behind"
    else:
        state = "synced"
    return {
        "path": str(repo),
        "dirty": dirty,
        "state": state,
        "ahead": ahead,
        "behind": behind,
    }


def _decision(state: str, dirty: bool, apply: bool) -> str:
    if state == "diverged":
        return "blocked_diverged"
    if state == "no-upstream":
        return "blocked_no_upstream"
    if state == "behind" and dirty:
        return "blocked_dirty"
    if dirty:
        return "commit_required"
    if state == "synced":
        return "none"
    if state == "ahead":
        return "push_required"
    if state == "behind":
        return "fast_forwarded" if apply else "fast_forward_available"
    return "blocked_unknown_state"


def synchronize(
    config: dict[str, Any], *, apply: bool = False, fetch: bool = True
) -> dict[str, Any]:
    errors = validate_config(config)
    if errors:
        raise ValueError("Invalid Ledger configuration: " + "; ".join(errors))

    results = []
    for source in config.get("repositories", []):
        repo = Path(str(source["path"])).expanduser().resolve()
        result: dict[str, Any] = {
            "name": source["name"],
            "path": str(repo),
            "required": bool(source.get("required", False)),
        }
        try:
            before = inspect(repo, fetch)
            action = _decision(str(before["state"]), bool(before["dirty"]), apply)
            result["before"] = before
            result["action"] = action
            if action == "fast_forwarded":
                _git(repo, "merge", "--ff-only", "@{upstream}")
            result["after"] = inspect(repo, False)
            if result["after"]["state"] == "synced" and not result["after"]["dirty"]:
                result["status"] = "ready"
            elif action in {"commit_required", "push_required"}:
                result["status"] = "action_required"
            else:
                result["status"] = "blocked"
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            result.update(
                {
                    "action": "error",
                    "status": "error",
                    "detail": str(error),
                }
            )
        results.append(result)

    return {
        "mode": "apply" if apply else "dry-run",
        "fetch_performed": fetch,
        "repositories": results,
        "writes_performed": [
            item["name"] for item in results if item.get("action") == "fast_forwarded"
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-fetch", action="store_true")
    args = parser.parse_args()

    result = synchronize(
        load_config(args.config), apply=args.apply, fetch=not args.no_fetch
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
