"""Unit and integration tests for fork-upstream-sync tracker_template.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
TRACKER = SKILL_ROOT / "scripts" / "tracker_template.py"


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "Test User")
    _git(path, "config", "user.email", "test@example.com")


def _commit_file(repo: Path, rel: str, content: str, message: str) -> str:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


class TrackerTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _run_tracker(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(TRACKER), *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_shallow_clone_deepens_on_full_40_char_base_sha(self) -> None:
        upstream = self.tmp / "upstream"
        _init_repo(upstream)
        c1 = _commit_file(upstream, "src/core.py", "v = 1\n", "feat: initial (#1)")
        c2 = _commit_file(upstream, "src/core.py", "v = 2\n", "fix: core step 2 (#2)")
        _commit_file(upstream, "src/legacy/old.py", "x = 1\n", "feat: legacy (#3)")
        _commit_file(upstream, "l10n/app.arb", "{}\n", "New translations (#4)")
        _commit_file(upstream, "src/removed.py", "r = 1\n", "feat: removed file (#5)")
        c6 = _commit_file(upstream, "src/core.py", "v = 3\n", "fix: important bug (#6)")

        # Create a shallow fork clone that does NOT yet contain c2 (4 commits back).
        fork = self.tmp / "fork"
        subprocess.run(
            ["git", "clone", "--depth=1", f"file://{upstream}", str(fork)],
            capture_output=True,
            text=True,
            check=True,
        )
        _git(fork, "config", "user.name", "Fork Maintainer")
        _git(fork, "config", "user.email", "fork@example.com")
        # Remove src/removed.py in fork so commit #5 classifies as SKIPPED_NOT_IN_FORK
        _git(fork, "rm", "src/removed.py")
        _git(fork, "commit", "-m", "chore: remove unused file in fork")
        _git(fork, "remote", "add", "upstream", f"file://{upstream}")

        # Verify c2 is genuinely absent from the shallow fork object DB right now
        chk = subprocess.run(
            ["git", "cat-file", "-e", f"{c2}^{{commit}}"],
            cwd=fork,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(chk.returncode, 0, "c2 should be absent before sync")
        self.assertEqual(len(c2), 40)
        self.assertNotEqual(c1, c2)

        cfg_dir = fork / ".upstream"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.json").write_text(
            json.dumps(
                {
                    "upstream_repo": "owner/upstream",
                    "upstream_branch": "main",
                    "base_sha": c2,
                    "cut_paths": ["src/legacy"],
                    "chore_subjects": ["^New translations"],
                    "chore_files": [r"\.arb$"],
                    "known_applied": {},
                }
            ),
            encoding="utf-8",
        )

        # Fetch depth=2 only reaches c6 and c5 (still missing c2), forcing deepen!
        proc = self._run_tracker(fork, "sync", "--depth", "2")
        self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")

        ledger = json.loads((cfg_dir / "ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["last_synced_sha"], c6)
        self.assertEqual(ledger["stats"]["total"], 4)
        self.assertEqual(ledger["stats"]["candidates"], 1)
        self.assertEqual(ledger["stats"]["skipped"], 3)
        self.assertEqual(ledger["commits"][c6[:9]]["status"], "CANDIDATE")
        self.assertEqual(ledger["commits"][c6[:9]]["pr"], 6)

    def test_subdirectory_invocation_resolves_git_repo_root(self) -> None:
        upstream = self.tmp / "upstream"
        _init_repo(upstream)
        c1 = _commit_file(upstream, "src/app.py", "a = 1\n", "init (#10)")

        fork = self.tmp / "fork"
        _init_repo(fork)
        _commit_file(fork, "src/app.py", "a = 1\n", "fork init")
        _git(fork, "remote", "add", "upstream", f"file://{upstream}")
        _commit_file(upstream, "src/app.py", "a = 2\n", "fix: update app (#11)")
        _git(fork, "fetch", "upstream", "main")

        cfg_dir = fork / ".upstream"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.json").write_text(
            json.dumps(
                {
                    "upstream_repo": "owner/upstream",
                    "upstream_branch": "main",
                    "base_sha": c1,
                }
            ),
            encoding="utf-8",
        )

        nested = fork / "src" / "deep" / "nested"
        nested.mkdir(parents=True, exist_ok=True)

        sync_proc = self._run_tracker(nested, "sync", "--no-fetch")
        self.assertEqual(sync_proc.returncode, 0, sync_proc.stderr)
        self.assertTrue((cfg_dir / "ledger.json").is_file())
        self.assertFalse((nested / ".upstream").exists())

        cand_proc = self._run_tracker(nested, "candidates", "--format", "json")
        self.assertEqual(cand_proc.returncode, 0, cand_proc.stderr)
        candidates = json.loads(cand_proc.stdout)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["pr"], 11)

        mark_proc = self._run_tracker(
            nested, "mark", "11", "APPLIED", "--fork-pr", "42", "--notes", "ported"
        )
        self.assertEqual(mark_proc.returncode, 0, mark_proc.stderr)

        status_proc = self._run_tracker(nested, "status")
        self.assertEqual(status_proc.returncode, 0, status_proc.stderr)
        self.assertIn("No pending candidates.", status_proc.stdout)

    def test_missing_nonexistent_base_sha_fails_cleanly_without_traceback(self) -> None:
        upstream = self.tmp / "upstream"
        _init_repo(upstream)
        _commit_file(upstream, "src/app.py", "a = 1\n", "init")

        fork = self.tmp / "fork"
        _init_repo(fork)
        _commit_file(fork, "src/app.py", "a = 1\n", "fork init")
        _git(fork, "remote", "add", "upstream", f"file://{upstream}")
        _git(fork, "fetch", "upstream", "main")

        cfg_dir = fork / ".upstream"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.json").write_text(
            json.dumps(
                {
                    "upstream_repo": "owner/upstream",
                    "upstream_branch": "main",
                    "base_sha": "f" * 40,
                }
            ),
            encoding="utf-8",
        )

        proc = self._run_tracker(fork, "sync", "--no-fetch")
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("Traceback (most recent call last)", proc.stderr)
        self.assertIn("Error:", proc.stderr)


if __name__ == "__main__":
    unittest.main()
