import subprocess
import unittest

from scripts.credentials import (
    CredentialError,
    resolve_config_credential,
    resolve_credential,
)


class CredentialTests(unittest.TestCase):
    def test_resolves_environment_reference(self) -> None:
        self.assertEqual(
            resolve_credential("env:LEDGER_TEST", environ={"LEDGER_TEST": "value"}),
            "value",
        )

    def test_rejects_raw_or_missing_credentials(self) -> None:
        with self.assertRaisesRegex(CredentialError, "env: or keychain"):
            resolve_credential("raw-secret", environ={})
        with self.assertRaisesRegex(CredentialError, "unavailable"):
            resolve_credential("env:PATH", environ={})

    def test_reads_macos_keychain_by_service_and_account(self) -> None:
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "keychain-value\n", "")

        value = resolve_credential(
            "keychain:ledger-linear/owner",
            system_name="Darwin",
            runner=runner,
        )
        self.assertEqual(value, "keychain-value")
        self.assertEqual(
            calls[0][0],
            [
                "security",
                "find-generic-password",
                "-w",
                "-s",
                "ledger-linear",
                "-a",
                "owner",
            ],
        )

    def test_resolves_inline_config_credential(self) -> None:
        self.assertEqual(
            resolve_config_credential(
                {"api_key": "inline-value"}, "api_key", "credential_ref"
            ),
            "inline-value",
        )

    def test_rejects_ambiguous_config_credential(self) -> None:
        with self.assertRaisesRegex(CredentialError, "only one"):
            resolve_config_credential(
                {"api_key": "inline", "credential_ref": "env:LINEAR_KEY"},
                "api_key",
                "credential_ref",
            )


if __name__ == "__main__":
    unittest.main()
