"""Validate Ledger records against the committed JSON Schema contracts."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


SCHEMA_DIRECTORY = Path(__file__).resolve().parents[1] / "schemas"
SCHEMA_FILES = {
    "evidence-bundle": "evidence-bundle.schema.json",
    "interaction-event": "interaction-event.schema.json",
    "weekly-input": "weekly-input.schema.json",
}


@lru_cache(maxsize=None)
def _validator(contract: str) -> Draft202012Validator:
    try:
        filename = SCHEMA_FILES[contract]
    except KeyError as error:
        raise ValueError(f"Unknown Ledger data contract: {contract}") from error
    schema = json.loads((SCHEMA_DIRECTORY / filename).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def contract_errors(contract: str, document: Any) -> list[str]:
    errors = sorted(
        _validator(contract).iter_errors(document),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    rendered = []
    for error in errors:
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        rendered.append(f"{path}: {error.message}")
    return rendered
