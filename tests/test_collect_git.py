import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import collect_git


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


def git_at(repo: Path, timestamp: str, *args: str) -> str:
    environment = {
        **os.environ,
        "GIT_AUTHOR_DATE": timestamp,
        "GIT_COMMITTER_DATE": timestamp,
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


class GitCollectionTests(unittest.TestCase):
    def test_collects_commit_and_worktree_patches_with_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            git(repo, "init")
            git(repo, "config", "user.name", "Test User")
            git(repo, "config", "user.email", "test@example.com")
            (repo / "Notes").mkdir()
            (repo / "Code").mkdir()
            (repo / "Notes/week.md").write_text("first\n", encoding="utf-8")
            (repo / "Code/app.py").write_text("print(1)\n", encoding="utf-8")
            git(repo, "add", ".")
            git(repo, "commit", "-m", "initial")
            (repo / "Notes/week.md").write_text("first\nsecond\n", encoding="utf-8")
            (repo / "Notes/new.md").write_text("new note\n", encoding="utf-8")
            (repo / "Code/app.py").write_text("print(2)\n", encoding="utf-8")

            source = {
                "name": "notes",
                "path": str(repo),
                "kind": "notes",
                "include": ["Notes"],
                "collect_worktree": True,
                "include_diff_content": True,
                "max_patch_chars": 4000,
            }
            commits = collect_git.collect_repository(
                source, "2020-01-01", "2030-01-01", {"test@example.com"}
            )
            self.assertEqual(len(commits), 1)
            self.assertEqual([item["path"] for item in commits[0]["files"]], ["Notes/week.md"])
            self.assertIn("first", commits[0]["files"][0]["patch"])

            worktree = collect_git.collect_worktree(source)
            self.assertIsNotNone(worktree)
            paths = {item["path"] for item in worktree["files"]}
            self.assertEqual(paths, {"Notes/week.md", "Notes/new.md"})
            self.assertNotIn("Code/app.py", paths)

    def test_commit_window_accepts_explicit_timezone_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            git(repo, "init")
            git(repo, "config", "user.name", "Test User")
            git(repo, "config", "user.email", "test@example.com")
            note = repo / "note.md"
            note.write_text("before\n", encoding="utf-8")
            git(repo, "add", "note.md")
            git_at(repo, "2026-08-16T15:30:00Z", "commit", "-m", "before week")
            note.write_text("inside\n", encoding="utf-8")
            git(repo, "add", "note.md")
            git_at(repo, "2026-08-16T16:30:00Z", "commit", "-m", "inside week")

            source = {"name": "notes", "path": str(repo), "kind": "notes"}
            events = collect_git.collect_repository(
                source,
                "2026-08-16T16:00:00+00:00",
                "2026-08-23T16:00:00+00:00",
                {"test@example.com"},
            )
            self.assertEqual([event["subject"] for event in events], ["inside week"])


if __name__ == "__main__":
    unittest.main()
