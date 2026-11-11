"""Tests for V3 Milestone 5: Honest Halt & Deep Standby."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import support

SKILL_ROOT = support.SKILL_ROOT
SCRIPTS = support.SCRIPTS


class SaturationAndStandbyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = support.tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_runrecord_accepts_saturated_outcome(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="rr_sat")
        support.commit(repo, "initial commit")

        proc = support.run(
            [
                support.PYTHON,
                str(SCRIPTS / "runrecord.py"),
                "write",
                "--outcome",
                "saturated",
                "--chosen",
                "test-gap",
            ],
            cwd=repo,
        )
        self.assertEqual(proc.returncode, 0)
        rr_dir = repo / ".autonomous" / "run-records"
        records = list(rr_dir.glob("rr-*.json"))
        self.assertEqual(len(records), 1)
        data = json.loads(records[0].read_text(encoding="utf-8"))
        self.assertEqual(data.get("outcome"), "saturated")

    def test_survey_detects_saturation_and_emits_honest_halt(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="survey_sat")
        # Ensure tests exist for src/app.py so test_gap does not fire
        support.write(repo / "tests" / "test_app.py", "def test_ok(): pass\n")
        # Ensure .github/workflows exists and runs tests on PRs so ci_audit does not fire
        (repo / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
        support.write(
            repo / ".github" / "workflows" / "ci.yml",
            "name: CI\non: [push, pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n    - run: python3 -m unittest\n",
        )
        support.commit(repo, "clean repo with tests and workflows")

        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "survey.py"), "--json"],
            cwd=repo,
        )
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertIsNone(data.get("chosen"))
        self.assertTrue(data.get("saturated"))
        self.assertIn("saturated", data.get("reason", "").lower())
        self.assertTrue((repo / ".autonomous" / "saturation.json").is_file())

    def test_fleet_stats_includes_saturation_section(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="stats_sat")
        support.commit(repo, "initial commit")

        (repo / ".autonomous").mkdir(parents=True, exist_ok=True)
        (repo / ".autonomous" / "saturation.json").write_text(
            json.dumps({"saturated": True, "timestamp": "2026-11-11T12:00:00Z", "reason": "honest halt"}),
            encoding="utf-8",
        )

        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "fleet.py"), "stats", "--json"],
            cwd=repo,
        )
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertIn("saturation", data)
        self.assertTrue(data["saturation"].get("saturated"))

        # Also verify text / markdown output
        proc_txt = support.run(
            [support.PYTHON, str(SCRIPTS / "fleet.py"), "stats"],
            cwd=repo,
        )
        self.assertIn("Saturation Status", proc_txt.stdout)

    def test_deep_standby_suppresses_scheduled_interval_wake(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="standby_sat")
        support.commit(repo, "initial commit")

        import datetime
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        (repo / ".autonomous").mkdir(parents=True, exist_ok=True)
        (repo / ".autonomous" / "saturation.json").write_text(
            json.dumps({"saturated": True, "timestamp": now_iso, "reason": "honest halt"}),
            encoding="utf-8",
        )

        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "fleet.py"), "needs-brain", "--json"],
            cwd=repo,
        )
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertFalse(data.get("needs_brain"))
        self.assertIn("standby", data.get("reason", "").lower())

    def test_deep_standby_wakes_on_events(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="event_wake")
        support.commit(repo, "initial commit")

        import datetime
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        (repo / ".autonomous").mkdir(parents=True, exist_ok=True)
        (repo / ".autonomous" / "saturation.json").write_text(
            json.dumps({"saturated": True, "timestamp": now_iso, "reason": "honest halt"}),
            encoding="utf-8",
        )

        # Mock an open PR requiring attention
        prs = [
            {
                "number": 42,
                "title": "fix: resolve edge case",
                "state": "OPEN",
                "headRefOid": "abc123",
                "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "FAILURE"}],
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

        import os
        env = dict(os.environ)
        env["PATH"] = f"{gh_bin}:{env.get('PATH', '')}"

        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "fleet.py"), "needs-brain", "--json", "--require-gh"],
            cwd=repo,
            env=env,
        )
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertTrue(data.get("needs_brain"))


if __name__ == "__main__":
    unittest.main()
