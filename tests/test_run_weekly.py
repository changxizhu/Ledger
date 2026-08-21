import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skill_paths import configure_paths

configure_paths()

from run_weekly import collect_run, finalize_run, weekly_report_name
from scripts.ledger_common import load_config


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class WeeklyRunTests(unittest.TestCase):
    def test_collect_and_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            git(repo, "init")
            git(repo, "config", "user.name", "Owner")
            git(repo, "config", "user.email", "owner@example.com")
            (repo / "note.md").write_text("evidence\n", encoding="utf-8")
            git(repo, "add", ".")
            git(repo, "commit", "-m", "evidence")
            config_path = root / "ledger.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'timezone = "Asia/Shanghai"',
                        'private_data_dir = ".ledger"',
                        "[identity]",
                        'person_id = "owner"',
                        'git_names = ["Owner"]',
                        'git_emails = ["owner@example.com"]',
                        "[[repositories]]",
                        'name = "repo"',
                        f'path = "{repo}"',
                        'kind = "notes"',
                        "required = true",
                        "require_synced = false",
                        "collect_worktree = true",
                        "include_diff_content = true",
                        "[weekly_index]",
                        "plan_weight = 0.30",
                        "progress_weight = 0.45",
                        "focus_weight = 0.15",
                        "evidence_weight = 0.10",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_config(config_path)
            with patch(
                "run_weekly.refresh_direct_connector_snapshots",
                return_value=[{"name": "linear", "status": "refreshed"}],
            ) as refresher:
                run_dir, evidence = collect_run(config, "2026-W34", refresh_direct=True)
            refresher.assert_called_once()
            self.assertEqual(evidence["direct_refresh"][0]["status"], "refreshed")
            self.assertEqual(run_dir.parent.name, "2026-W34")
            self.assertTrue((run_dir / "evidence.json").is_file())
            self.assertTrue((run_dir / "evidence-review.md").is_file())
            self.assertEqual(evidence["source_status"][0]["status"], "available")
            manifest = json.loads((run_dir / "run.json").read_text())
            self.assertEqual(manifest["files"]["evidence_review"], "evidence-review.md")

            reviewed = json.loads((run_dir / "weekly-input.draft.json").read_text())
            git_event = evidence["git"]["events"][0]
            git_file = git_event["files"][0]
            reviewed.update(
                {
                    "target_focus_hours": 10,
                    "actual_focus_hours": 8,
                    "tasks": [{"title": "Task", "weight": 1, "completed": True}],
                    "themes": [
                        {
                            "name": "Theme",
                            "weight": 1,
                            "progress": 0.8,
                            "evidence_count": 1,
                            "evidence_refs": [
                                {
                                    "event_type": "git_commit",
                                    "source": git_event["source"],
                                    "commit": git_event["commit"],
                                    "path": git_file["path"],
                                    "reason": "The committed note is the reviewed outcome.",
                                }
                            ],
                            "summary": "Result",
                        }
                    ],
                }
            )
            input_path = root / "reviewed.json"
            input_path.write_text(json.dumps(reviewed), encoding="utf-8")
            manifest = finalize_run(config, run_dir, input_path, False)
            self.assertEqual(manifest["status"], "finalized")
            report_path = run_dir.parent / "2026-08-week-3.html"
            self.assertEqual(weekly_report_name("2026-W34"), report_path.name)
            self.assertEqual(manifest["files"]["report"], "../2026-08-week-3.html")
            self.assertTrue(report_path.is_file())
            self.assertFalse((run_dir / "weekly-report.html").exists())
            self.assertFalse((run_dir / "weekly-report.md").exists())
            self.assertTrue((run_dir / "public-week.json").is_file())
            proposals = json.loads((run_dir / "proposals.json").read_text())
            self.assertIn(str(report_path), json.dumps(proposals))

            reviewed["themes"][0]["evidence_refs"][0]["path"] = "missing.md"
            input_path.write_text(json.dumps(reviewed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not resolve"):
                finalize_run(config, run_dir, input_path, False)
            reviewed["themes"][0]["evidence_refs"][0]["path"] = git_file["path"]

            reviewed["themes"][0]["evidence_count"] = 2
            input_path.write_text(json.dumps(reviewed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must equal"):
                finalize_run(config, run_dir, input_path, False)
            reviewed["themes"][0]["evidence_count"] = 1

            reviewed["profile_id"] = "someone-else"
            input_path.write_text(json.dumps(reviewed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "profile_id"):
                finalize_run(config, run_dir, input_path, False)

            reviewed["profile_id"] = "owner"
            reviewed["source_status"][0]["status"] = "unavailable"
            input_path.write_text(json.dumps(reviewed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source_status"):
                finalize_run(config, run_dir, input_path, False)

            reviewed["source_status"] = evidence["source_status"]
            reviewed["actual_focus_hours"] = "eight"
            input_path.write_text(json.dumps(reviewed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "shared data contract"):
                finalize_run(config, run_dir, input_path, False)


if __name__ == "__main__":
    unittest.main()
