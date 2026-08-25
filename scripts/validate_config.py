#!/usr/bin/env python3
"""Validate a private Ledger configuration without exposing its contents."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .ledger_common import (
        integration_backends,
        load_config,
        profile_id,
        validate_config,
    )
except ImportError:  # Direct script execution.
    from ledger_common import integration_backends, load_config, profile_id, validate_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--skip-path-checks", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    errors = validate_config(config, check_paths=not args.skip_path_checks)
    integrations = {}
    for name in ("linear", "google_calendar", "google_tasks"):
        settings = config.get("integrations", {}).get(name, {})
        read_backend, write_backend = integration_backends(settings)
        if name == "google_tasks":
            credential_storage = "shared:google_calendar"
        else:
            inline_fields = (
                ("api_key",)
                if name == "linear"
                else ("client_id", "client_secret", "refresh_token")
            )
            reference_fields = (
                ("credential_ref",)
                if name == "linear"
                else (
                    "client_id_ref",
                    "client_credential_ref",
                    "refresh_credential_ref",
                )
            )
            if any(str(settings.get(field, "")).strip() for field in inline_fields):
                credential_storage = "inline"
            elif any(
                str(settings.get(field, "")).strip() for field in reference_fields
            ):
                credential_storage = "reference"
            else:
                credential_storage = "missing"
        integrations[name] = {
            "enabled": bool(settings.get("enabled", False)),
            "read_backend": read_backend,
            "write_backend": write_backend,
            "credential_storage": credential_storage,
            "browser_fallback": bool(settings.get("allow_browser_fallback", False)),
        }
    result = {
        "valid": not errors,
        "profile_id": profile_id(config),
        "repository_count": len(config.get("repositories", [])),
        "interaction_source_count": len(config.get("interaction_sources", [])),
        "integrations": integrations,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
