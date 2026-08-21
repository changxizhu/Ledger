import unittest

from skill_paths import configure_paths

configure_paths()

from build_evidence_report import render_evidence_report


class EvidenceReportTests(unittest.TestCase):
    def test_report_contains_private_git_patches_but_not_interaction_text(self) -> None:
        evidence = {
            "week": "2026-W34",
            "period": {"start": "2026-08-17", "end": "2026-08-24"},
            "timezone": "Asia/Shanghai",
            "collected_at": "2026-08-19T13:00:00Z",
            "source_status": [
                {
                    "name": "repo",
                    "category": "repository",
                    "required": True,
                    "status": "available",
                    "event_count": 1,
                }
            ],
            "sync": {
                "repositories": [
                    {"name": "repo", "state": "synced", "dirty": False, "ahead": 0, "behind": 0}
                ]
            },
            "git": {
                "events": [
                    {
                        "source": "repo",
                        "commit": "1234567890",
                        "authored_at": "2026-08-18T10:00:00+08:00",
                        "subject": "Result",
                        "files": [
                            {
                                "path": "note.md",
                                "added": 2,
                                "deleted": 1,
                                "patch": "PRIVATE PATCH",
                            }
                        ],
                    }
                ],
                "worktree_events": [],
            },
            "interactions": {
                "sources": [],
                "events": [
                    {
                        "source": "codex",
                        "timestamp": "2026-08-18T10:00:00Z",
                        "role": "user",
                        "workspace": "Ledger",
                        "character_count": 14,
                        "content_sha256": "abcdef1234567890",
                        "content": "PRIVATE PROMPT",
                    }
                ],
            },
            "connectors": {"snapshots": {}, "calendar_focus_hours": None},
        }
        report = render_evidence_report(evidence)
        self.assertIn("## Source status", report)
        self.assertIn("## Commits", report)
        self.assertIn("## Working-tree changes", report)
        self.assertIn("## Interaction events", report)
        self.assertIn("## Git diff details", report)
        self.assertIn("12345678", report)
        self.assertIn("<details>", report)
        self.assertIn("PRIVATE PATCH", report)
        self.assertNotIn("PRIVATE PROMPT", report)


if __name__ == "__main__":
    unittest.main()
