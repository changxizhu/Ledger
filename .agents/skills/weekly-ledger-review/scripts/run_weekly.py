#!/usr/bin/env python3
"""Collect and finalize an auditable weekly Ledger run."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from ._project import ensure_project_root
    from .build_evidence_report import render_evidence_report
    from .build_proposals import build_proposals
    from .build_html_report import render_report
    from .collect_connectors import collect_connector_snapshots
    from .refresh_connectors import refresh_direct_connector_snapshots
    from .sanitize_export import sanitize_week
    from .score_week import calculate_weekly_index
except ImportError:  # Direct script execution.
    from _project import ensure_project_root
    from build_evidence_report import render_evidence_report
    from build_proposals import build_proposals
    from build_html_report import render_report
    from collect_connectors import collect_connector_snapshots
    from refresh_connectors import refresh_direct_connector_snapshots
    from sanitize_export import sanitize_week
    from score_week import calculate_weekly_index

ensure_project_root()

from scripts.collect_git import collect_repository, collect_worktree
from scripts.collect_interactions import collect_interactions
from scripts.ledger_common import (
    identity_aliases,
    load_config,
    private_data_dir,
    profile_id,
    utc_period,
    validate_config,
    validate_weekly_input,
    week_period,
    weekly_weights,
    write_json,
)
from scripts.schema_validation import contract_errors
from scripts.sync_repositories import inspect


def _new_run_dir(config: dict[str, Any], week: str) -> Path:
    root = private_data_dir(config)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    week_root = root / "runs" / week
    path = week_root / stamp
    suffix = 2
    while path.exists():
        path = week_root / f"{stamp}-{suffix}"
        suffix += 1
    path.mkdir(parents=True)
    os.chmod(root, 0o700)
    os.chmod(week_root, 0o700)
    os.chmod(path, 0o700)
    return path


def weekly_report_name(week: str) -> str:
    """Return the canonical report name for an ISO week."""
    start, _ = week_period(week)
    week_of_month = ((start.day - 1) // 7) + 1
    return f"{start:%Y-%m}-week-{week_of_month}.html"


def _weekly_report_path(config: dict[str, Any], week: str) -> Path:
    return private_data_dir(config) / "runs" / week / weekly_report_name(week)


def collect_run(
    config: dict[str, Any],
    week: str,
    fetch: bool = False,
    refresh_direct: bool = False,
) -> tuple[Path, dict[str, Any]]:
    errors = validate_config(config)
    if errors:
        raise ValueError("Invalid Ledger configuration: " + "; ".join(errors))
    start, end = week_period(week)
    start_utc, end_utc = utc_period(start, end, str(config["timezone"]))
    direct_refresh = (
        refresh_direct_connector_snapshots(config, start, end) if refresh_direct else []
    )
    run_dir = _new_run_dir(config, week)
    aliases = identity_aliases(config)

    sync_results = []
    git_events = []
    worktree_events = []
    source_status = []
    for source in config.get("repositories", []):
        required = bool(source.get("required", False))
        try:
            sync = inspect(Path(source["path"]).expanduser().resolve(), fetch)
            sync["name"] = source["name"]
            sync_results.append(sync)
            events = collect_repository(
                source, start_utc.isoformat(), end_utc.isoformat(), aliases
            )
            git_events.extend(events)
            worktree = collect_worktree(source)
            if worktree:
                worktree_events.append(worktree)
            stale = bool(source.get("require_synced", True)) and (
                sync["state"] != "synced" or bool(sync["dirty"])
            )
            source_status.append(
                {
                    "name": source["name"],
                    "category": "repository",
                    "required": required,
                    "status": "stale" if stale else "available",
                    "sync_state": sync["state"],
                    "dirty": sync["dirty"],
                    "fetch_performed": fetch,
                    "event_count": len(events),
                }
            )
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            source_status.append(
                {
                    "name": source.get("name", "unknown"),
                    "category": "repository",
                    "required": required,
                    "status": "error",
                    "detail": str(error),
                }
            )

    git_events.sort(key=lambda item: item["authored_at"])
    git_data = {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "event_count": len(git_events),
        "events": git_events,
        "worktree_event_count": len(worktree_events),
        "worktree_events": worktree_events,
    }
    interactions = collect_interactions(config, start, end)
    source_status.extend(interactions["sources"])
    connectors = collect_connector_snapshots(config, start, end)
    source_status.extend(connectors["statuses"])

    evidence = {
        "schema_version": 1,
        "profile_id": profile_id(config),
        "week": week,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "timezone": config["timezone"],
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "source_status": source_status,
        "sync": {"repositories": sync_results},
        "git": git_data,
        "interactions": interactions,
        "connectors": connectors,
        "direct_refresh": direct_refresh,
    }
    evidence_errors = contract_errors("evidence-bundle", evidence)
    if evidence_errors:
        raise ValueError(
            "Collected evidence violates the shared data contract: "
            + "; ".join(evidence_errors)
        )
    evidence_path = run_dir / "evidence.json"
    evidence_report_path = run_dir / "evidence-review.md"
    write_json(evidence_path, evidence)
    evidence_report_path.write_text(render_evidence_report(evidence), encoding="utf-8")

    linear = connectors["snapshots"].get("linear", {})
    draft = {
        "schema_version": 1,
        "profile_id": profile_id(config),
        "week": week,
        "period": evidence["period"],
        "target_focus_hours": config.get("planning", {}).get("target_focus_hours"),
        "actual_focus_hours": connectors.get("calendar_focus_hours"),
        "tasks": linear.get("issues", []),
        "themes": [],
        "reflection": [],
        "next_plan": {"tasks": []},
        "source_status": source_status,
        "evidence_file": str(evidence_path),
        "evidence_review_file": str(evidence_report_path),
    }
    write_json(run_dir / "weekly-input.draft.json", draft)
    manifest = {
        "schema_version": 1,
        "status": "collected",
        "week": week,
        "profile_id": profile_id(config),
        "created_at": evidence["collected_at"],
        "files": {
            "evidence": "evidence.json",
            "evidence_review": "evidence-review.md",
            "draft_input": "weekly-input.draft.json",
        },
    }
    write_json(run_dir / "run.json", manifest)
    return run_dir, evidence


def _reference_key(reference: dict[str, Any]) -> tuple[str, ...]:
    event_type = str(reference.get("event_type", ""))
    source = str(reference.get("source", ""))
    if event_type == "git_commit":
        return (
            event_type,
            source,
            str(reference.get("commit", "")),
            str(reference.get("path", "")),
        )
    if event_type == "git_worktree":
        return (event_type, source, str(reference.get("path", "")))
    return (event_type, source, str(reference.get("event_id", "")))


def _collected_reference_keys(evidence: dict[str, Any]) -> set[tuple[str, ...]]:
    keys: set[tuple[str, ...]] = set()
    for event in evidence.get("git", {}).get("events", []):
        for item in event.get("files", []):
            keys.add(
                (
                    "git_commit",
                    str(event.get("source", "")),
                    str(event.get("commit", "")),
                    str(item.get("path", "")),
                )
            )
    for event in evidence.get("git", {}).get("worktree_events", []):
        for item in event.get("files", []):
            keys.add(
                (
                    "git_worktree",
                    str(event.get("source", "")),
                    str(item.get("path", "")),
                )
            )
    for event in evidence.get("interactions", {}).get("events", []):
        keys.add(
            (
                "interaction",
                str(event.get("source_name") or event.get("source") or ""),
                str(event.get("event_id", "")),
            )
        )
    snapshots = evidence.get("connectors", {}).get("snapshots", {})
    linear = snapshots.get("linear", {})
    linear_source = str(linear.get("source") or "linear")
    for issue in linear.get("issues", []):
        keys.add(("linear_issue", linear_source, str(issue.get("id", ""))))
    calendar = snapshots.get("google_calendar", {})
    calendar_source = str(calendar.get("source") or "google_calendar")
    for event in calendar.get("events", []):
        keys.add(("calendar_event", calendar_source, str(event.get("id", ""))))
    return keys


def _validate_theme_evidence_refs(
    data: dict[str, Any], evidence: dict[str, Any]
) -> list[str]:
    available = _collected_reference_keys(evidence)
    errors: list[str] = []
    for theme_index, theme in enumerate(data.get("themes", [])):
        references = theme.get("evidence_refs", [])
        if int(theme.get("evidence_count", 0)) != len(references):
            errors.append(
                f"themes[{theme_index}].evidence_count must equal the number of evidence_refs"
            )
        seen: set[tuple[str, ...]] = set()
        for ref_index, reference in enumerate(references):
            key = _reference_key(reference)
            if key in seen:
                errors.append(
                    f"themes[{theme_index}].evidence_refs[{ref_index}] duplicates another reference"
                )
            seen.add(key)
            if key not in available:
                errors.append(
                    f"themes[{theme_index}].evidence_refs[{ref_index}] does not resolve to evidence.json"
                )
    return errors


def finalize_run(
    config: dict[str, Any], run_dir: Path, input_path: Path, allow_incomplete: bool
) -> dict[str, Any]:
    config_errors = validate_config(config)
    if config_errors:
        raise ValueError("Invalid Ledger configuration: " + "; ".join(config_errors))
    run_dir = run_dir.expanduser().resolve()
    runs_root = (private_data_dir(config) / "runs").resolve()
    if not run_dir.is_relative_to(runs_root):
        raise ValueError(
            "Run directory must be inside the configured private runs directory"
        )

    manifest_path = run_dir / "run.json"
    evidence_path = run_dir / "evidence.json"
    if not manifest_path.is_file() or not evidence_path.is_file():
        raise ValueError("Run directory is missing run.json or evidence.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    data = json.loads(input_path.read_text(encoding="utf-8"))
    evidence_errors = contract_errors("evidence-bundle", evidence)
    if evidence_errors:
        raise ValueError(
            "Collected evidence violates the shared data contract: "
            + "; ".join(evidence_errors)
        )
    errors = [
        f"shared data contract: {error}"
        for error in contract_errors("weekly-input", data)
    ]
    errors.extend(validate_weekly_input(data))
    if data.get("profile_id") != profile_id(config):
        errors.append("profile_id does not match the Ledger configuration")
    if data.get("profile_id") != manifest.get("profile_id"):
        errors.append("profile_id does not match the run manifest")
    if data.get("week") != manifest.get("week") or data.get("week") != evidence.get(
        "week"
    ):
        errors.append("week does not match the collected run")
    if data.get("period") != evidence.get("period"):
        errors.append("period does not match the collected evidence")
    if data.get("source_status") != evidence.get("source_status"):
        errors.append("source_status must be preserved exactly from evidence.json")
    errors.extend(_validate_theme_evidence_refs(data, evidence))
    if allow_incomplete:
        errors = [
            error
            for error in errors
            if not error.startswith("Required sources unavailable:")
        ]
    if errors:
        raise ValueError("Weekly input is not ready: " + "; ".join(errors))

    reviewed_input_path = run_dir / "weekly-input.json"
    write_json(reviewed_input_path, data)
    scored = calculate_weekly_index(data, weekly_weights(config))
    scored_path = run_dir / "weekly-scored.json"
    report_path = _weekly_report_path(config, str(data["week"]))
    proposal_path = run_dir / "proposals.json"
    public_path = run_dir / "public-week.json"
    write_json(scored_path, scored)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(scored), encoding="utf-8")
    for legacy_report_path in (
        run_dir / "weekly-report.html",
        run_dir / "weekly-report.md",
    ):
        if legacy_report_path.is_file():
            legacy_report_path.unlink()
    write_json(
        proposal_path,
        build_proposals(scored, str(config["timezone"]), str(report_path)),
    )
    include_public_names = bool(
        config.get("public_export", {}).get("include_theme_names", False)
    )
    write_json(public_path, sanitize_week(scored, include_public_names))

    manifest["status"] = "finalized"
    manifest["finalized_at"] = datetime.now(timezone.utc).isoformat()
    manifest["files"].update(
        {
            "reviewed_input": reviewed_input_path.name,
            "scored": scored_path.name,
            "report": Path(os.path.relpath(report_path, run_dir)).as_posix(),
            "proposals": proposal_path.name,
            "public_export": public_path.name,
        }
    )
    write_json(manifest_path, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--config", type=Path, required=True)
    collect_parser.add_argument("--week", required=True)
    collect_parser.add_argument("--fetch", action="store_true")
    collect_parser.add_argument(
        "--refresh-direct",
        action="store_true",
        help="Refresh connectors configured with auth_mode=direct before collection",
    )

    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--config", type=Path, required=True)
    finalize_parser.add_argument("--run-dir", type=Path, required=True)
    finalize_parser.add_argument("--input", type=Path)
    finalize_parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.command == "collect":
        run_dir, evidence = collect_run(
            config, args.week, args.fetch, args.refresh_direct
        )
        print(
            json.dumps(
                {
                    "status": "collected",
                    "run_dir": str(run_dir),
                    "source_status": evidence["source_status"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        run_dir = args.run_dir.expanduser().resolve()
        input_path = args.input or (run_dir / "weekly-input.json")
        manifest = finalize_run(config, run_dir, input_path, args.allow_incomplete)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
