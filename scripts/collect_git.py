#!/usr/bin/env python3
"""Collect commit and changed-file facts from configured local repositories."""

from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from .ledger_common import identity_aliases, load_config, write_json
except ImportError:  # Direct script execution.
    from ledger_common import identity_aliases, load_config, write_json


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


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n[diff truncated]\n", True


def _git_boundary(value: str) -> str:
    return value if "T" in value else f"{value}T00:00:00"


def _patch(repo: Path, revision: str, path: str, limit: int) -> tuple[str, bool]:
    text = _git(
        repo,
        "show" if revision != "HEAD" else "diff",
        *(["--format="] if revision != "HEAD" else []),
        "--no-ext-diff",
        "--unified=3",
        *([revision] if revision != "HEAD" else ["HEAD"]),
        "--",
        path,
    )
    return _truncate(text, limit)


def collect_repository(
    source: dict[str, Any], start: str, end: str, identities: set[str]
) -> list[dict[str, Any]]:
    repo = Path(source["path"]).expanduser().resolve()
    if not (repo / ".git").exists():
        raise ValueError(f"Not a Git repository: {repo}")

    hashes = _git(
        repo,
        "log",
        f"--since={_git_boundary(start)}",
        f"--before={_git_boundary(end)}",
        "--format=%H",
        "--all",
    ).splitlines()
    events: list[dict[str, Any]] = []
    prefixes = list(source.get("include", []))
    include_diff_content = bool(source.get("include_diff_content", False))
    max_patch_chars = max(1000, int(source.get("max_patch_chars", 12000)))

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
            if include_diff_content:
                patch, truncated = _patch(repo, commit_hash, path, max_patch_chars)
                files[-1]["patch"] = patch
                files[-1]["patch_truncated"] = truncated
        if files:
            events.append(
                {
                    "source": source["name"],
                    "kind": source.get("kind", "code"),
                    "event_type": "commit",
                    "commit": commit_hash,
                    "authored_at": authored_at,
                    "author": author_name,
                    "subject": subject,
                    "files": files,
                }
            )
    return events


def _status_paths(repo: Path) -> list[tuple[str, str]]:
    entries = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0")
    paths: list[tuple[str, str]] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        if not entry:
            index += 1
            continue
        status = entry[:2]
        path = entry[3:]
        if "R" in status or "C" in status:
            index += 1
        paths.append((status, path))
        index += 1
    return paths


def _worktree_file(repo: Path, status: str, path: str, include_patch: bool, limit: int) -> dict[str, Any]:
    untracked = status == "??"
    added: int | None = None
    deleted: int | None = None
    binary = False

    if untracked:
        file_path = repo / path
        try:
            raw = file_path.read_bytes()
            if b"\0" in raw:
                binary = True
            else:
                added = len(raw.decode("utf-8", errors="replace").splitlines())
                deleted = 0
        except OSError:
            pass
    else:
        stats = _git(repo, "diff", "HEAD", "--numstat", "--", path).splitlines()
        if stats:
            parts = stats[0].split("\t", 2)
            if len(parts) == 3:
                binary = parts[0] == "-" or parts[1] == "-"
                added = None if parts[0] == "-" else int(parts[0])
                deleted = None if parts[1] == "-" else int(parts[1])

    result: dict[str, Any] = {
        "path": path,
        "status": status,
        "added": added,
        "deleted": deleted,
        "binary": binary,
        "markdown": fnmatch.fnmatch(path.lower(), "*.md"),
    }
    if include_patch and not binary:
        if untracked:
            try:
                content = (repo / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
            patch_text = f"--- /dev/null\n+++ b/{path}\n@@ untracked file @@\n{content}"
            patch, truncated = _truncate(patch_text, limit)
        else:
            patch, truncated = _patch(repo, "HEAD", path, limit)
        result["patch"] = patch
        result["patch_truncated"] = truncated
    return result


def collect_worktree(source: dict[str, Any]) -> dict[str, Any] | None:
    if not source.get("collect_worktree", False):
        return None
    repo = Path(source["path"]).expanduser().resolve()
    prefixes = list(source.get("include", []))
    include_diff_content = bool(source.get("include_diff_content", False))
    max_patch_chars = max(1000, int(source.get("max_patch_chars", 12000)))
    files = [
        _worktree_file(repo, status, path, include_diff_content, max_patch_chars)
        for status, path in _status_paths(repo)
        if _included(path, prefixes)
    ]
    if not files:
        return None
    return {
        "source": source["name"],
        "kind": source.get("kind", "code"),
        "event_type": "working_tree",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--start", required=True, help="Inclusive date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Exclusive date, YYYY-MM-DD")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    config = load_config(args.config)
    zone = ZoneInfo(str(config["timezone"]))
    start_boundary = datetime.combine(
        date.fromisoformat(args.start), datetime.min.time(), tzinfo=zone
    ).isoformat()
    end_boundary = datetime.combine(
        date.fromisoformat(args.end), datetime.min.time(), tzinfo=zone
    ).isoformat()
    identities = identity_aliases(config)
    events = []
    worktree_events = []
    for source in config.get("repositories", []):
        events.extend(
            collect_repository(source, start_boundary, end_boundary, identities)
        )
        worktree = collect_worktree(source)
        if worktree:
            worktree_events.append(worktree)
    events.sort(key=lambda item: item["authored_at"])

    result = {
        "period": {"start": args.start, "end": args.end},
        "event_count": len(events),
        "events": events,
        "worktree_event_count": len(worktree_events),
        "worktree_events": worktree_events,
    }
    if args.output:
        write_json(args.output, result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
