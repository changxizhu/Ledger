#!/usr/bin/env python3
"""Report whether configured repositories are clean, ahead, behind, or diverged."""

from __future__ import annotations

import argparse
import json
import subprocess
import tomllib
from pathlib import Path


def _run(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def inspect(repo: Path, fetch: bool) -> dict[str, object]:
    if fetch:
        _run(repo, "fetch", "--prune")
    dirty = bool(_run(repo, "status", "--porcelain").stdout.strip())
    upstream = _run(repo, "rev-parse", "--abbrev-ref", "@{upstream}", check=False)
    if upstream.returncode != 0:
        return {"path": str(repo), "dirty": dirty, "state": "no-upstream"}
    counts = _run(repo, "rev-list", "--left-right", "--count", "HEAD...@{upstream}").stdout
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    results = [
        inspect(Path(source["path"]).expanduser().resolve(), args.fetch)
        for source in config.get("repositories", [])
    ]
    print(json.dumps({"repositories": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

