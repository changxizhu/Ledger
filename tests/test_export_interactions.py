import errno
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.export_interactions import (
    export_interactions,
    load_export_config,
    redact_content,
)
from scripts.schema_validation import contract_errors


class InteractionExportTests(unittest.TestCase):
    def test_loads_device_specific_export_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "ledger.toml"
            config_path.write_text(
                "\n".join(
                    [
                        "[interaction_export]",
                        'device_id = "macbook-a"',
                        'output_dir = "drive/interactions"',
                        'state_file = "state/export.json"',
                        'providers = ["codex"]',
                        "lookback_days = 14",
                        "include_content = false",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            settings = load_export_config(config_path)

            self.assertEqual(settings["device_id"], "macbook-a")
            self.assertEqual(
                settings["output_dir"], (root / "drive/interactions").resolve()
            )
            self.assertEqual(
                settings["state_file"], (root / "state/export.json").resolve()
            )
            self.assertEqual(settings["providers"], ["codex"])
            self.assertEqual(settings["lookback_days"], 14)
            self.assertFalse(settings["include_content"])

    def test_incremental_export_normalizes_and_redacts_both_providers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_root = root / "codex"
            claude_root = root / "claude"
            output = root / "drive"
            state = root / "state.json"
            codex_root.mkdir()
            claude_root.mkdir()

            codex_path = codex_root / "rollout.jsonl"
            codex_path.write_text(
                "\n".join(
                    json.dumps(record)
                    for record in (
                        {
                            "timestamp": "2026-08-20T01:00:00Z",
                            "type": "session_meta",
                            "payload": {"id": "codex-session", "cwd": "/tmp/Ledger"},
                        },
                        {
                            "timestamp": "2026-08-20T01:01:00Z",
                            "type": "event_msg",
                            "payload": {"type": "task_started", "turn_id": "turn-1"},
                        },
                        {
                            "timestamp": "2026-08-20T01:01:01Z",
                            "type": "turn_context",
                            "payload": {"turn_id": "turn-1", "model": "test-model"},
                        },
                        {
                            "timestamp": "2026-08-20T01:01:02Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "user_message",
                                "message": "Use api_key=private-value in the test",
                            },
                        },
                        {
                            "timestamp": "2026-08-20T01:01:03Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "agent_message",
                                "phase": "final_answer",
                                "message": "Completed",
                            },
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            claude_path = claude_root / "session.jsonl"
            claude_path.write_text(
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2026-08-20T02:00:00Z",
                        "cwd": "/tmp/Research",
                        "sessionId": "claude-session",
                        "uuid": "message-1",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Claude result"}],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            arguments = {
                "sources": {"codex": codex_root, "claude_code": claude_root},
                "output_dir": output,
                "state_file": state,
                "device_id": "macbook-a",
                "lookback_days": 3650,
                "now": datetime(2026, 8, 20, tzinfo=timezone.utc),
            }
            first = export_interactions(**arguments)
            second = export_interactions(**arguments)

            self.assertEqual(first["new_event_count"], 3)
            self.assertEqual(first["written_event_count"], 3)
            self.assertEqual(first["redacted_value_count"], 1)
            self.assertEqual(second["new_event_count"], 0)
            self.assertTrue(state.exists())

            codex_output = next(
                (output / "macbook-a" / "codex").glob("2026-08-20--*.jsonl")
            )
            claude_output = next(
                (output / "macbook-a" / "claude_code").glob(
                    "2026-08-20--*.jsonl"
                )
            )
            codex_records = [json.loads(line) for line in codex_output.read_text().splitlines()]
            claude_records = [
                json.loads(line) for line in claude_output.read_text().splitlines()
            ]
            self.assertEqual(len(codex_records), 2)
            self.assertEqual(len(claude_records), 1)
            self.assertNotIn("private-value", codex_output.read_text())
            self.assertIn("[REDACTED]", codex_records[0]["content"])
            self.assertEqual(claude_records[0]["device_id"], "macbook-a")
            for record in codex_records + claude_records:
                self.assertEqual(contract_errors("interaction-event", record), [])

            with claude_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "type": "user",
                            "timestamp": "2026-08-20T03:00:00Z",
                            "sessionId": "claude-session",
                            "uuid": "message-2",
                            "message": {"role": "user", "content": "Follow up"},
                        }
                    )
                    + "\n"
                )
            third = export_interactions(**arguments)
            self.assertEqual(third["new_event_count"], 1)
            self.assertEqual(third["written_event_count"], 1)

    def test_metadata_only_keeps_hash_and_count_without_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            claude_root = root / "claude"
            claude_root.mkdir()
            (claude_root / "session.jsonl").write_text(
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": "2026-08-20T03:00:00Z",
                        "uuid": "message-1",
                        "message": {"role": "user", "content": "private text"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output = root / "output"
            result = export_interactions(
                sources={"claude_code": claude_root},
                output_dir=output,
                state_file=root / "state.json",
                device_id="desktop-b",
                lookback_days=3650,
                include_content=False,
                now=datetime(2026, 8, 20, tzinfo=timezone.utc),
            )
            output_file = next(
                (output / "desktop-b" / "claude_code").glob(
                    "2026-08-20--*.jsonl"
                )
            )
            record = json.loads(output_file.read_text().strip())
            self.assertEqual(result["new_event_count"], 1)
            self.assertEqual(record["character_count"], len("private text"))
            self.assertTrue(record["content_sha256"])
            self.assertNotIn("content", record)

    def test_cloud_mount_chmod_failure_and_missing_state_retry_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            claude_root = root / "claude"
            output = root / "drive"
            state = root / "state.json"
            claude_root.mkdir()
            (claude_root / "session.jsonl").write_text(
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": "2026-08-20T03:00:00Z",
                        "uuid": "message-1",
                        "message": {"role": "user", "content": "Follow up"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            arguments = {
                "sources": {"claude_code": claude_root},
                "output_dir": output,
                "state_file": state,
                "device_id": "booster-ubuntu",
                "lookback_days": 3650,
                "now": datetime(2026, 8, 20, tzinfo=timezone.utc),
            }
            real_chmod = os.chmod
            progress_messages: list[str] = []

            def cloud_chmod(path: str | Path, mode: int) -> None:
                if str(path).startswith(str(output)):
                    raise OSError(errno.EOPNOTSUPP, "Operation not supported", str(path))
                real_chmod(path, mode)

            with patch("scripts.export_interactions.os.chmod", side_effect=cloud_chmod):
                first = export_interactions(
                    **arguments, progress=progress_messages.append
                )

            self.assertEqual(first["written_event_count"], 1)
            self.assertTrue(state.exists())
            self.assertTrue(
                any(message.startswith("Uploading 1/1:") for message in progress_messages)
            )
            self.assertEqual(progress_messages[-1], "Export complete.")
            first_output = Path(first["output_files"][0])
            first_output.rename(first_output.with_name("2026-08-20.jsonl"))
            state.unlink()

            retry = export_interactions(**arguments)

            self.assertEqual(retry["new_event_count"], 1)
            self.assertEqual(retry["written_event_count"], 0)
            self.assertEqual(retry["duplicate_event_count"], 1)
            self.assertTrue(state.exists())
            self.assertEqual(
                len(
                    list(
                        (output / "booster-ubuntu" / "claude_code").glob(
                            "2026-08-20*.jsonl"
                        )
                    )
                ),
                1,
            )

    def test_redaction_handles_provider_tokens_and_bearer_values(self) -> None:
        text, count = redact_content(
            "sk-ant-abcdefghijklmnopqrstuvwxyz Bearer abcdefghijklmnop password=hunter2"
        )
        self.assertEqual(count, 3)
        self.assertNotIn("hunter2", text)
        self.assertEqual(text.count("[REDACTED]"), 3)


if __name__ == "__main__":
    unittest.main()
