import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "capture_interaction", ROOT / "hooks/capture_interaction.py"
)
assert SPEC and SPEC.loader
capture_interaction = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture_interaction)


class InteractionCaptureTests(unittest.TestCase):
    def test_content_is_private_by_default(self) -> None:
        event = capture_interaction.normalize_event(
            {
                "hook_event_name": "UserPromptSubmit",
                "prompt": "private question",
                "cwd": "/tmp/Ledger",
            },
            include_content=False,
        )
        self.assertEqual(event["character_count"], 16)
        self.assertNotIn("content", event)
        self.assertEqual(event["workspace"], "Ledger")

    def test_content_can_be_enabled(self) -> None:
        event = capture_interaction.normalize_event(
            {"hook_event_name": "Stop", "last_assistant_message": "answer"},
            include_content=True,
        )
        self.assertEqual(event["role"], "assistant")
        self.assertEqual(event["content"], "answer")

    def test_jsonl_append(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            capture_interaction.append_record(path, {"event": "SubagentStart"})
            self.assertEqual(path.read_text(), '{"event":"SubagentStart"}\n')


if __name__ == "__main__":
    unittest.main()

