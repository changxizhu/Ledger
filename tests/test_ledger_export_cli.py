import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from scripts.export_interactions import load_export_config
from scripts.ledger_export_cli import (
    SCHEDULE_LABEL,
    macos_schedule_payload,
    main,
    run_configured_export,
    scheduled_command,
    status_payload,
    write_export_config,
)


class LedgerExportCliTests(unittest.TestCase):
    def test_non_interactive_setup_creates_minimal_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            output = root / "Drive/Ledger/Interactions"
            stdout = StringIO()
            with redirect_stdout(stdout):
                result = main(
                    [
                        "setup",
                        "--config",
                        str(config),
                        "--device-id",
                        "macbook-b",
                        "--output-dir",
                        str(output),
                        "--provider",
                        "codex",
                        "--non-interactive",
                        "--no-test",
                    ]
                )

            self.assertEqual(result, 0)
            self.assertTrue(output.is_dir())
            settings = load_export_config(config)
            self.assertEqual(settings["device_id"], "macbook-b")
            self.assertEqual(settings["providers"], ["codex"])
            self.assertIn("Next: ledger-export run", stdout.getvalue())

    def test_configured_run_and_status_use_only_exporter_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            claude = root / "claude"
            claude.mkdir()
            (claude / "session.jsonl").write_text(
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": "2026-08-20T03:00:00Z",
                        "uuid": "message-1",
                        "message": {"role": "user", "content": "Review this result"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            config = root / "config.toml"
            output = root / "Drive/Ledger/Interactions"
            state = root / "state/export.json"
            write_export_config(
                config,
                device_id="desktop-b",
                output_dir=output,
                state_file=state,
                providers=["claude_code"],
                lookback_days=3650,
                include_content=True,
                claude_path=claude,
            )

            result = run_configured_export(config)
            status = status_payload(config)

            self.assertEqual(result["new_event_count"], 1)
            self.assertTrue(state.is_file())
            state_data = json.loads(state.read_text())
            self.assertTrue(state_data["last_run_at"])
            self.assertEqual(status["output_file_count"], 1)
            self.assertEqual(status["providers"][0]["name"], "claude_code")
            self.assertTrue(status["providers"][0]["available"])

    def test_macos_schedule_uses_installed_command_and_private_logs(self) -> None:
        config = Path("/tmp/ledger-exporter/config.toml")
        command = scheduled_command(config, Path("/usr/local/bin/ledger-export"))
        payload = macos_schedule_payload(
            command,
            Path("/tmp/ledger-exporter/state"),
            23,
            30,
        )

        self.assertEqual(command[0], "/usr/local/bin/ledger-export")
        self.assertEqual(command[-2:], ["--config", str(config.resolve())])
        self.assertEqual(payload["Label"], SCHEDULE_LABEL)
        self.assertEqual(payload["StartCalendarInterval"], {"Hour": 23, "Minute": 30})
        self.assertTrue(payload["StandardErrorPath"].endswith("export-error.log"))

    def test_python_script_schedule_keeps_the_interpreter(self) -> None:
        command = scheduled_command(
            Path("/tmp/config.toml"), Path("/tmp/ledger_export_cli.py")
        )
        self.assertEqual(
            command[:2], [sys.executable, str(Path("/tmp/ledger_export_cli.py").resolve())]
        )


if __name__ == "__main__":
    unittest.main()
