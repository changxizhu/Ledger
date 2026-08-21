import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.ledger_common import load_config
from scripts.sync_repositories import synchronize


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


class RepositorySyncTests(unittest.TestCase):
    def test_dry_run_then_fast_forward(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote = root / "remote.git"
            seed = root / "seed"
            local = root / "local"
            other = root / "other"
            subprocess.run(
                ["git", "init", "--bare", str(remote)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            git(root, "clone", str(remote), str(seed))
            git(seed, "config", "user.name", "Owner")
            git(seed, "config", "user.email", "owner@example.com")
            (seed / "note.md").write_text("one\n", encoding="utf-8")
            git(seed, "add", "note.md")
            git(seed, "commit", "-m", "initial")
            git(seed, "push", "-u", "origin", "HEAD")
            git(root, "clone", str(remote), str(local))
            git(root, "clone", str(remote), str(other))
            git(other, "config", "user.name", "Owner")
            git(other, "config", "user.email", "owner@example.com")
            (other / "note.md").write_text("two\n", encoding="utf-8")
            git(other, "add", "note.md")
            git(other, "commit", "-m", "remote update")
            git(other, "push")

            config_path = root / "ledger.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'timezone = "Asia/Shanghai"',
                        '[identity]',
                        'person_id = "owner"',
                        'git_names = ["Owner"]',
                        'git_emails = ["owner@example.com"]',
                        '[[repositories]]',
                        'name = "notes"',
                        f'path = "{local}"',
                        'required = true',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_config(config_path)

            dry_run = synchronize(config, apply=False, fetch=True)
            self.assertEqual(
                dry_run["repositories"][0]["action"], "fast_forward_available"
            )
            self.assertEqual((local / "note.md").read_text(), "one\n")

            applied = synchronize(config, apply=True, fetch=True)
            self.assertEqual(applied["repositories"][0]["action"], "fast_forwarded")
            self.assertEqual(applied["repositories"][0]["after"]["state"], "synced")
            self.assertEqual((local / "note.md").read_text(), "two\n")

            (local / "note.md").write_text("local edit\n", encoding="utf-8")
            dirty = synchronize(config, apply=False, fetch=False)
            self.assertEqual(dirty["repositories"][0]["action"], "commit_required")
            self.assertEqual(dirty["repositories"][0]["status"], "action_required")

    def test_dirty_checkout_is_never_updated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            git(repo, "init")
            git(repo, "config", "user.name", "Owner")
            git(repo, "config", "user.email", "owner@example.com")
            (repo / "note.md").write_text("tracked\n", encoding="utf-8")
            git(repo, "add", "note.md")
            git(repo, "commit", "-m", "initial")
            (repo / "note.md").write_text("local edit\n", encoding="utf-8")
            config_path = root / "ledger.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'timezone = "Asia/Shanghai"',
                        '[identity]',
                        'person_id = "owner"',
                        'git_names = ["Owner"]',
                        '[[repositories]]',
                        'name = "notes"',
                        f'path = "{repo}"',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = synchronize(load_config(config_path), apply=True, fetch=False)
            self.assertEqual(result["repositories"][0]["action"], "blocked_no_upstream")
            self.assertEqual((repo / "note.md").read_text(), "local edit\n")


if __name__ == "__main__":
    unittest.main()
