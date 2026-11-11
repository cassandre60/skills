"""Tests for V3 Milestone 3: Backlog, Fingerprints & Item Claims."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

import support

SKILL_ROOT = support.SKILL_ROOT
SCRIPTS = support.SCRIPTS


class BacklogAndClaimsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = support.tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_bug_scout_finds_swallowed_error(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="bugscout")
        support.write(
            repo / "src" / "worker.py",
            "def run_task():\n    try:\n        do_work()\n    except Exception:\n        pass\n",
        )
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "worker added")

        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "bug_scout.py"), "--json"],
            cwd=repo,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertGreater(data.get("count", 0), 0)
        rules = [c["rule_id"] for c in data.get("candidates", [])]
        self.assertIn("bug.swallowed-error", rules)

    def test_upstream_finds_manifest_targets(self) -> None:
        repo, _ = support.init_repo(self.tmp, name="upstream")
        support.install_scripts(repo)
        support.write(
            repo / "package.json",
            json.dumps({"name": "test-pkg", "dependencies": {"express": "^4.17.1"}}),
        )
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "manifest added")

        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "upstream.py"), "--json"],
            cwd=repo,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertGreater(data.get("count", 0), 0)
        self.assertEqual(data["candidates"][0]["track"], "research")

    def test_harvest_builds_backlog_with_fingerprints(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="harvest")
        # Untested file -> test gap
        support.write(
            repo / "src" / "core.py",
            "def add(a, b):\n    return a + b\n",
        )
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "core added")

        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "backlog.py"), "harvest"],
            cwd=repo,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

        backlog_file = repo / ".autonomous" / "backlog.json"
        self.assertTrue(backlog_file.is_file())
        data = json.loads(backlog_file.read_text(encoding="utf-8"))
        self.assertEqual(data.get("$schema"), "autonomous/backlog/v3")
        items = data.get("items", [])
        self.assertGreater(len(items), 0)
        first = items[0]
        self.assertIn("fingerprint", first)
        self.assertTrue(first["fingerprint"].startswith("sha256:"))
        self.assertIn("target_paths", first)
        self.assertIn("rule_id", first)

    def test_claim_item_checks_fingerprint_and_opens_draft_pr(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="claimitem", with_origin=True)
        support.commit(repo, "initial commit")
        support.push(repo)
        support.write(
            repo / "src" / "core.py",
            "def add(a, b):\n    return a + b\n",
        )
        support.commit(repo, "core added")
        support.push(repo)

        # Rebuild backlog
        support.run([support.PYTHON, str(SCRIPTS / "backlog.py"), "harvest"], cwd=repo)
        backlog_file = repo / ".autonomous" / "backlog.json"
        data = json.loads(backlog_file.read_text(encoding="utf-8"))
        item_id = data["items"][0]["id"]

        fix_root = Path(__file__).resolve().parent.parent / "evals" / "fixtures"
        stub = fix_root / "open-pr-python" / "bin"
        shutil = support.shutil
        shutil.copytree(stub, repo / "bin")
        support.run(["chmod", "+x", str(repo / "bin" / "gh")], cwd=repo)
        env = dict(os.environ)
        env["PATH"] = f"{repo / 'bin'}:{env.get('PATH', '')}"

        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "fleet.py"), "claim", "--item", item_id, "--json"],
            cwd=repo,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        claim_data = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertIn("fingerprint", claim_data)
        self.assertEqual(claim_data["item_id"], item_id)

    def test_sync_rejections_records_idea_fingerprint_with_expiry(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="syncreject")
        support.commit(repo, "initial commit")
        ledger = repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md"

        # Mock closed PR with fingerprint in body
        prs = [
            {
                "number": 42,
                "title": "Refactor router architecture",
                "state": "CLOSED",
                "mergedAt": None,
                "closedAt": "2026-11-10T12:00:00Z",
                "body": "<!-- autonomous:fingerprint sha256:112233445566 -->\nProposal details",
                "files": [{"path": "src/app.py"}],
            }
        ]
        gh_bin = repo / "bin"
        gh_bin.mkdir(parents=True, exist_ok=True)
        gh_script = gh_bin / "gh"
        gh_script.write_text(
            f'#!/bin/sh\nif [ "$1" = "pr" ] && [ "$2" = "list" ]; then\n  cat << "EOF"\n{json.dumps(prs)}\nEOF\n  exit 0\nfi\nexit 1\n',
            encoding="utf-8",
        )
        gh_script.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = f"{gh_bin}:{env.get('PATH', '')}"

        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "ledger.py"), "sync-rejections", "--json", "--require-gh"],
            cwd=repo,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = ledger.read_text(encoding="utf-8")
        self.assertIn("REJECTED", content)
        self.assertIn("sha256:112233445566", content)
        self.assertIn("expires:", content)


if __name__ == "__main__":
    unittest.main()
