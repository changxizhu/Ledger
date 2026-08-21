#!/usr/bin/env python3
"""User-friendly setup and scheduling for the lightweight Ledger exporter."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shlex
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    from .export_interactions import (
        DEFAULT_STATE_FILE,
        PROVIDER_PATHS,
        export_interactions,
        load_export_config,
        sanitize_device_id,
    )
except ImportError:  # Installed command or direct script execution.
    from export_interactions import (
        DEFAULT_STATE_FILE,
        PROVIDER_PATHS,
        export_interactions,
        load_export_config,
        sanitize_device_id,
    )


SCHEDULE_LABEL = "com.ledger.interaction-export"
SUPPORTED_PROVIDERS = tuple(PROVIDER_PATHS)


def default_config_path() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
        return root / "LedgerExporter" / "config.toml"
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "ledger-exporter" / "config.toml"


def default_state_path() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        return root / "LedgerExporter" / "export-state.json"
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return root / "ledger-exporter" / "export-state.json"


def discover_google_drive_roots() -> list[Path]:
    candidates: list[Path] = []
    home = Path.home()
    if sys.platform == "darwin":
        candidates.extend(
            path / "My Drive"
            for path in sorted((home / "Library/CloudStorage").glob("GoogleDrive-*"))
        )
    elif os.name == "nt":
        configured = os.environ.get("GOOGLE_DRIVE_PATH")
        if configured:
            candidates.append(Path(configured).expanduser())
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            candidates.append(Path(f"{letter}:/My Drive"))
    else:
        candidates.extend((home / "Google Drive", home / "GoogleDrive", home / "Drive"))
    unique: list[Path] = []
    for candidate in candidates:
        if candidate.is_dir() and candidate not in unique:
            unique.append(candidate)
    return unique


def discover_providers() -> list[str]:
    available = [
        name for name, path in PROVIDER_PATHS.items() if path.expanduser().exists()
    ]
    return available or list(SUPPORTED_PROVIDERS)


def _toml_string(value: str | Path) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def write_export_config(
    path: Path,
    *,
    device_id: str,
    output_dir: Path,
    state_file: Path,
    providers: list[str],
    lookback_days: int,
    include_content: bool,
    codex_path: Path | None = None,
    claude_path: Path | None = None,
) -> None:
    unsupported = sorted(set(providers) - set(SUPPORTED_PROVIDERS))
    if unsupported:
        raise ValueError(f"Unsupported providers: {', '.join(unsupported)}")
    if not providers:
        raise ValueError("At least one provider is required")
    if lookback_days < 1:
        raise ValueError("lookback_days must be at least 1")
    lines = [
        "[interaction_export]",
        f"device_id = {_toml_string(sanitize_device_id(device_id))}",
        f"output_dir = {_toml_string(output_dir.expanduser().resolve())}",
        f"state_file = {_toml_string(state_file.expanduser().resolve())}",
        "providers = [" + ", ".join(_toml_string(value) for value in providers) + "]",
        f"lookback_days = {lookback_days}",
        f"include_content = {'true' if include_content else 'false'}",
    ]
    if codex_path:
        lines.append(f"codex_path = {_toml_string(codex_path.expanduser().resolve())}")
    if claude_path:
        lines.append(f"claude_path = {_toml_string(claude_path.expanduser().resolve())}")
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default or ""


def _confirm(prompt: str, default: bool = False) -> bool:
    marker = "Y/n" if default else "y/N"
    answer = input(f"{prompt} [{marker}]: ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def _configured_sources(settings: dict[str, Any]) -> dict[str, Path]:
    available = {
        "codex": settings.get("codex_path") or PROVIDER_PATHS["codex"],
        "claude_code": settings.get("claude_path") or PROVIDER_PATHS["claude_code"],
    }
    return {provider: Path(available[provider]) for provider in settings["providers"]}


def run_configured_export(
    config_path: Path, *, dry_run: bool = False, metadata_only: bool = False
) -> dict[str, Any]:
    settings = load_export_config(config_path)
    if not settings.get("device_id"):
        raise ValueError("device_id is missing from the exporter configuration")
    if not settings.get("output_dir"):
        raise ValueError("output_dir is missing from the exporter configuration")
    state_file = settings.get("state_file") or DEFAULT_STATE_FILE
    return export_interactions(
        sources=_configured_sources(settings),
        output_dir=Path(settings["output_dir"]),
        state_file=Path(state_file),
        device_id=str(settings["device_id"]),
        lookback_days=int(settings["lookback_days"]),
        include_content=bool(settings["include_content"]) and not metadata_only,
        dry_run=dry_run,
    )


def _print_run_summary(result: dict[str, Any]) -> None:
    action = "Would export" if result["dry_run"] else "Exported"
    print(f"{action} {result['new_event_count']} new interaction events.")
    for source in result["sources"]:
        print(
            f"  {source['provider']}: {source['status']} "
            f"({source['new_event_count']} new, {source['file_count']} files checked)"
        )
    print(f"Google Drive folder: {result['output_dir']}")
    if not result["dry_run"]:
        print(f"Local state: {result['state_file']}")


def setup_command(args: argparse.Namespace) -> int:
    config_path = (args.config or default_config_path()).expanduser().resolve()
    interactive = not args.non_interactive and sys.stdin.isatty()
    if config_path.exists() and not args.force:
        if not interactive or not _confirm(f"Replace existing configuration {config_path}"):
            raise ValueError(f"Configuration already exists: {config_path}")

    device_default = sanitize_device_id(socket.gethostname().split(".")[0])
    device_id = args.device_id or (
        _ask("Device name", device_default) if interactive else device_default
    )

    drive_roots = discover_google_drive_roots()
    detected_output = drive_roots[0] / "Ledger/Interactions" if drive_roots else None
    if args.output_dir:
        output_dir = args.output_dir
    elif interactive:
        selected = _ask(
            "Google Drive interaction folder",
            str(detected_output) if detected_output else None,
        )
        if not selected:
            raise ValueError("Google Drive interaction folder is required")
        output_dir = Path(selected)
    elif detected_output:
        output_dir = detected_output
    else:
        raise ValueError("Use --output-dir because Google Drive was not detected")

    providers = args.providers or discover_providers()
    if interactive and not args.providers:
        selected = _ask("Providers, comma separated", ",".join(providers))
        providers = [value.strip() for value in selected.split(",") if value.strip()]

    state_file = args.state_file or default_state_path()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    write_export_config(
        config_path,
        device_id=device_id,
        output_dir=output_dir,
        state_file=state_file,
        providers=providers,
        lookback_days=args.lookback_days,
        include_content=not args.metadata_only,
        codex_path=args.codex_path,
        claude_path=args.claude_path,
    )
    print(f"Configuration created: {config_path}")
    print(f"Device: {sanitize_device_id(device_id)}")
    print(f"Providers: {', '.join(providers)}")
    if not args.no_test:
        print("\nTesting local transcript access...")
        _print_run_summary(run_configured_export(config_path, dry_run=True))
    print("\nNext: ledger-export run")
    print("Then: ledger-export schedule install")
    return 0


def run_command(args: argparse.Namespace) -> int:
    config_path = (args.config or default_config_path()).expanduser().resolve()
    result = run_configured_export(
        config_path, dry_run=args.dry_run, metadata_only=args.metadata_only
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_run_summary(result)
    return 0


def _schedule_paths() -> dict[str, Path]:
    home = Path.home()
    if sys.platform == "darwin":
        return {"definition": home / "Library/LaunchAgents" / f"{SCHEDULE_LABEL}.plist"}
    if os.name == "nt":
        return {}
    root = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "systemd/user"
    return {
        "service": root / "ledger-exporter.service",
        "timer": root / "ledger-exporter.timer",
    }


def scheduled_command(config_path: Path, entrypoint: Path | None = None) -> list[str]:
    entrypoint = (entrypoint or Path(sys.argv[0])).expanduser().resolve()
    prefix = [sys.executable, str(entrypoint)] if entrypoint.suffix == ".py" else [str(entrypoint)]
    return [*prefix, "run", "--config", str(config_path.expanduser().resolve())]


def macos_schedule_payload(
    command: list[str], log_dir: Path, hour: int, minute: int
) -> dict[str, Any]:
    return {
        "Label": SCHEDULE_LABEL,
        "ProgramArguments": command,
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(log_dir / "export.log"),
        "StandardErrorPath": str(log_dir / "export-error.log"),
        "ProcessType": "Background",
    }


def install_schedule(config_path: Path, hour: int, minute: int) -> Path | None:
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Schedule time must be a valid 24-hour time")
    command = scheduled_command(config_path)
    paths = _schedule_paths()
    log_dir = default_state_path().parent
    log_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        definition = paths["definition"]
        definition.parent.mkdir(parents=True, exist_ok=True)
        with definition.open("wb") as handle:
            plistlib.dump(macos_schedule_payload(command, log_dir, hour, minute), handle)
        uid_target = f"gui/{os.getuid()}"
        subprocess.run(
            ["launchctl", "bootout", uid_target, str(definition)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(["launchctl", "bootstrap", uid_target, str(definition)], check=True)
        return definition
    if os.name == "nt":
        subprocess.run(
            [
                "schtasks",
                "/Create",
                "/F",
                "/SC",
                "DAILY",
                "/ST",
                f"{hour:02d}:{minute:02d}",
                "/TN",
                "LedgerExporter",
                "/TR",
                subprocess.list2cmdline(command),
            ],
            check=True,
        )
        return None

    service = paths["service"]
    timer = paths["timer"]
    service.parent.mkdir(parents=True, exist_ok=True)
    service.write_text(
        "\n".join(
            [
                "[Unit]",
                "Description=Export local interactions for Ledger",
                "",
                "[Service]",
                "Type=oneshot",
                f"ExecStart={shlex.join(command)}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    timer.write_text(
        "\n".join(
            [
                "[Unit]",
                "Description=Run Ledger interaction export daily",
                "",
                "[Timer]",
                f"OnCalendar=*-*-* {hour:02d}:{minute:02d}:00",
                "Persistent=true",
                "",
                "[Install]",
                "WantedBy=timers.target",
                "",
            ]
        ),
        encoding="utf-8",
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", timer.name], check=True
    )
    return timer


def remove_schedule() -> None:
    paths = _schedule_paths()
    if sys.platform == "darwin":
        definition = paths["definition"]
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}", str(definition)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        definition.unlink(missing_ok=True)
        return
    if os.name == "nt":
        subprocess.run(
            ["schtasks", "/Delete", "/F", "/TN", "LedgerExporter"], check=False
        )
        return
    subprocess.run(
        ["systemctl", "--user", "disable", "--now", "ledger-exporter.timer"],
        check=False,
    )
    for path in paths.values():
        path.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)


def schedule_installed() -> bool:
    paths = _schedule_paths()
    if os.name == "nt":
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", "LedgerExporter"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    return any(path.exists() for path in paths.values())


def _read_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def status_payload(config_path: Path) -> dict[str, Any]:
    settings = load_export_config(config_path)
    state_file = Path(settings.get("state_file") or DEFAULT_STATE_FILE).expanduser().resolve()
    state = _read_state(state_file)
    output_dir = Path(settings["output_dir"]).expanduser().resolve()
    device_dir = output_dir / sanitize_device_id(str(settings["device_id"]))
    output_files = sorted(device_dir.rglob("*.jsonl")) if device_dir.is_dir() else []
    latest_output = max(output_files, key=lambda path: path.stat().st_mtime) if output_files else None
    return {
        "configured": True,
        "config_file": str(config_path.expanduser().resolve()),
        "device_id": settings["device_id"],
        "providers": [
            {
                "name": name,
                "path": str(path.expanduser().resolve()),
                "available": path.expanduser().exists(),
            }
            for name, path in _configured_sources(settings).items()
        ],
        "output_dir": str(output_dir),
        "output_file_count": len(output_files),
        "latest_output": str(latest_output) if latest_output else None,
        "state_file": str(state_file),
        "last_run_at": state.get("last_run_at"),
        "schedule_installed": schedule_installed(),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def status_command(args: argparse.Namespace) -> int:
    config_path = (args.config or default_config_path()).expanduser().resolve()
    payload = status_payload(config_path)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"Device: {payload['device_id']}")
    print(f"Last export: {payload['last_run_at'] or 'Not run yet'}")
    print(f"Schedule: {'installed' if payload['schedule_installed'] else 'not installed'}")
    for provider in payload["providers"]:
        state = "available" if provider["available"] else "unavailable"
        print(f"  {provider['name']}: {state} ({provider['path']})")
    print(f"Export files: {payload['output_file_count']}")
    print(f"Google Drive folder: {payload['output_dir']}")
    return 0


def schedule_command(args: argparse.Namespace) -> int:
    if args.schedule_action == "install":
        config_path = (args.config or default_config_path()).expanduser().resolve()
        if not config_path.is_file():
            raise ValueError(f"Configuration does not exist: {config_path}")
        definition = install_schedule(config_path, args.hour, args.minute)
        print(f"Daily export scheduled for {args.hour:02d}:{args.minute:02d}.")
        if definition:
            print(f"Schedule definition: {definition}")
        return 0
    remove_schedule()
    print("Scheduled export removed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup", help="Create a device configuration")
    setup.add_argument("--config", type=Path)
    setup.add_argument("--device-id")
    setup.add_argument("--output-dir", type=Path)
    setup.add_argument("--state-file", type=Path)
    setup.add_argument("--provider", dest="providers", action="append", choices=SUPPORTED_PROVIDERS)
    setup.add_argument("--codex-path", type=Path)
    setup.add_argument("--claude-path", type=Path)
    setup.add_argument("--lookback-days", type=int, default=8)
    setup.add_argument("--metadata-only", action="store_true")
    setup.add_argument("--force", action="store_true")
    setup.add_argument("--non-interactive", action="store_true")
    setup.add_argument("--no-test", action="store_true")
    setup.set_defaults(handler=setup_command)

    run = subparsers.add_parser("run", help="Export new interactions now")
    run.add_argument("--config", type=Path)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--metadata-only", action="store_true")
    run.add_argument("--json", action="store_true")
    run.set_defaults(handler=run_command)

    status = subparsers.add_parser("status", help="Show exporter health")
    status.add_argument("--config", type=Path)
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=status_command)

    schedule = subparsers.add_parser("schedule", help="Manage the daily background export")
    schedule_subparsers = schedule.add_subparsers(dest="schedule_action", required=True)
    install = schedule_subparsers.add_parser("install", help="Install the daily task")
    install.add_argument("--config", type=Path)
    install.add_argument("--hour", type=int, default=23)
    install.add_argument("--minute", type=int, default=30)
    remove = schedule_subparsers.add_parser("remove", help="Remove the daily task")
    remove.set_defaults(config=None, hour=None, minute=None)
    schedule.set_defaults(handler=schedule_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(f"ledger-export: error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
