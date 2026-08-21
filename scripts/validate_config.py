#!/usr/bin/env python3
"""Validate a private Ledger configuration without exposing its contents."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .ledger_common import load_config, profile_id, validate_config
except ImportError:  # Direct script execution.
    from ledger_common import load_config, profile_id, validate_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--skip-path-checks", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    errors = validate_config(config, check_paths=not args.skip_path_checks)
    result = {
        "valid": not errors,
        "profile_id": profile_id(config),
        "repository_count": len(config.get("repositories", [])),
        "interaction_source_count": len(config.get("interaction_sources", [])),
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
