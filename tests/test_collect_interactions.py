import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts import collect_interactions


def interaction_record(
    *,
    captured_at: str,
    event_id: str,
    content: str,
    source: str = "codex",
    device_id: str = "macbook-a",
) -> dict[str, object]:
    return {
        "format_version": 1,
        "captured_at": captured_at,
        "source": source,
        "device_id": device_id,
        "role": "user",
        "workspace": "Ledger",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "event": "user_message",
        "model": None,
        "agent_type": None,
        "character_count": len(content),
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "event_id": event_id,
        "content": content,
    }


class InteractionCollectionTests(unittest.TestCase):
    def test_inspects_contract_coverage_without_printing_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "events.jsonl"
            records = [
                interaction_record(
                    captured_at="2026-08-18T01:00:00Z",
                    event_id="a" * 64,
                    content="private prompt",
                ),
                interaction_record(
                    captured_at="2026-07-01T01:00:00Z",
                    event_id="b" * 64,
                    content="private answer",
                ),
                {"type": "assistant", "timestamp": "2026-08-18T02:00:00Z"},
            ]
            source.write_text(
                "\n".join(json.dumps(record) for record in records) + "\nnot-json\n",
                encoding="utf-8",
            )
            config = {
                "_config_root": str(root),
                "timezone": "Asia/Shanghai",
                "interaction_sources": [
                    {
                        "name": "scheduled-exports",
                        "kind": "ledger_jsonl",
                        "path": str(source),
                    }
                ],
            }

            result = collect_interactions.inspect_sources(config, "2026-W34")
            inspected = result["sources"][0]
            self.assertEqual(inspected["file_count"], 1)
            self.assertEqual(inspected["record_count"], 4)
            self.assertEqual(inspected["accepted_record_count"], 2)
            self.assertEqual(inspected["invalid_record_count"], 1)
            self.assertEqual(inspected["malformed_record_count"], 1)
            self.assertEqual(inspected["requested_week_event_count"], 1)
            self.assertNotIn("private prompt", json.dumps(result))
            self.assertFalse(result["content_printed"])

    def test_collects_scheduled_exports_and_deduplicates_event_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported = root / "exports"
            exported.mkdir()
            record = interaction_record(
                captured_at="2026-08-18T01:00:00Z",
                event_id="c" * 64,
                content="question",
                source="claude_code",
                device_id="desktop-b",
            )
            (exported / "events.jsonl").write_text(
                json.dumps(record) + "\n" + json.dumps(record) + "\n",
                encoding="utf-8",
            )
            config = {
                "_config_root": str(root),
                "timezone": "Asia/Shanghai",
                "interaction_sources": [
                    {
                        "name": "scheduled-exports",
                        "kind": "ledger_jsonl",
                        "path": str(exported),
                        "include_content": True,
                    }
                ],
            }

            result = collect_interactions.collect_interactions(
                config, date(2026, 8, 17), date(2026, 8, 24)
            )
            self.assertEqual(result["event_count"], 1)
            self.assertEqual(result["duplicate_count"], 1)
            self.assertEqual(result["events"][0]["source"], "claude_code")
            self.assertEqual(result["events"][0]["device_id"], "desktop-b")
            self.assertEqual(result["events"][0]["content"], "question")


if __name__ == "__main__":
    unittest.main()
