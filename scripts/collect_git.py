#!/usr/bin/env python3
"""Collect commit and changed-file facts from configured local repositories."""

from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any


def _git(repo: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return process.stdout


def _included(path: str, prefixes: list[str]) -> bool:
    if not prefixes:
        return True
    return any(path == prefix or path.startswith(prefix.rstrip("/") + "/") for prefix in prefixes)


def collect_repository(
    source: dict[str, Any], start: str, end: str, identities: set[str]
) -> list[dict[str, Any]]:
    repo = Path(source["path"]).expanduser().resolve()
    if not (repo / ".git").exists():
        raise ValueError(f"Not a Git repository: {repo}")

    hashes = _git(
        repo,
        "log",
        f"--since={start}T00:00:00",
        f"--before={end}T00:00:00",
        "--format=%H",
        "--all",
    ).splitlines()
    events: list[dict[str, Any]] = []
    prefixes = list(source.get("include", []))

    for commit_hash in hashes:
        header = _git(
            repo,
            "show",
            "-s",
            "--format=%aI%x1f%an%x1f%ae%x1f%s",
            commit_hash,
        ).rstrip("\n").split("\x1f", 3)
        authored_at, author_name, author_email, subject = header
        if identities and author_name.lower() not in identities and author_email.lower() not in identities:
            continue

        files = []
        for line in _git(repo, "show", "--format=", "--numstat", commit_hash).splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            added, deleted, path = parts
            if not _included(path, prefixes):
                continue
            files.append(
                {
                    "path": path,
                    "added": None if added == "-" else int(added),
                    "deleted": None if deleted == "-" else int(deleted),
                    "markdown": fnmatch.fnmatch(path.lower(), "*.md"),
                }
            )
        if files:
            events.append(
                {
                    "source": source["name"],
                    "kind": source.get("kind", "code"),
                    "commit": commit_hash,
                    "authored_at": authored_at,
                    "author": author_name,
                    "subject": subject,
                    "files": files,
                }
            )
    return events


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--start", required=True, help="Inclusive date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Exclusive date, YYYY-MM-DD")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    identity = config.get("identity", {})
    identities = {
        str(value).lower()
        for key in ("git_names", "git_emails")
        for value in identity.get(key, [])
    }
    events = []
    for source in config.get("repositories", []):
        events.extend(collect_repository(source, args.start, args.end, identities))
    events.sort(key=lambda item: item["authored_at"])

    result = {
        "period": {"start": args.start, "end": args.end},
        "event_count": len(events),
        "events": events,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()

