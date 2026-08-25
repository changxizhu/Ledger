#!/usr/bin/env python3
"""Resolve referenced or explicitly allowed inline Ledger credentials."""

from __future__ import annotations

import os
import platform
import re
import subprocess
from collections.abc import Callable, Mapping
from urllib.parse import unquote


ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CredentialError(ValueError):
    """A credential reference is invalid or cannot be resolved."""


def credential_reference_error(reference: object) -> str | None:
    value = str(reference or "").strip()
    if value.startswith("env:"):
        name = value.removeprefix("env:")
        if ENV_NAME.fullmatch(name):
            return None
        return "environment credential references must use env:VARIABLE_NAME"
    if value.startswith("keychain:"):
        locator = value.removeprefix("keychain:")
        if "/" in locator:
            service, account = locator.split("/", 1)
            if service and account:
                return None
        return "Keychain references must use keychain:SERVICE/ACCOUNT"
    return "credential references must use env: or keychain:"


def resolve_credential(
    reference: str,
    *,
    environ: Mapping[str, str] | None = None,
    system_name: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> str:
    error = credential_reference_error(reference)
    if error:
        raise CredentialError(error)

    if reference.startswith("env:"):
        name = reference.removeprefix("env:")
        source = environ if environ is not None else os.environ
        value = source.get(name, "")
        if not value:
            raise CredentialError(f"Environment credential {name} is unavailable")
        return value

    if (system_name or platform.system()) != "Darwin":
        raise CredentialError("Keychain credential references require macOS")
    locator = reference.removeprefix("keychain:")
    service_text, account_text = locator.split("/", 1)
    service = unquote(service_text)
    account = unquote(account_text)
    command_runner = runner or subprocess.run
    completed = command_runner(
        [
            "security",
            "find-generic-password",
            "-w",
            "-s",
            service,
            "-a",
            account,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise CredentialError("The referenced macOS Keychain credential is unavailable")
    return completed.stdout.rstrip("\r\n")


def resolve_config_credential(
    settings: Mapping[str, object],
    inline_field: str,
    reference_field: str,
    *,
    resolver: Callable[[str], str] | None = None,
) -> str:
    """Resolve one credential from an inline value or an external reference."""

    inline_value = str(settings.get(inline_field, "")).strip()
    reference = str(settings.get(reference_field, "")).strip()
    if inline_value and reference:
        raise CredentialError(
            f"Configure only one of {inline_field} or {reference_field}"
        )
    if inline_value:
        return inline_value
    if reference:
        return (resolver or resolve_credential)(reference)
    raise CredentialError(
        f"Configure {inline_field} or {reference_field}"
    )
