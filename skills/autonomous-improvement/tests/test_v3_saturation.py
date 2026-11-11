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
        import os
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        (repo / ".autonomous").mkdir(parents=True, exist_ok=True)
        (repo / ".autonomous" / "saturation.json").write_text(
            json.dumps({"saturated": True, "timestamp": now_iso, "reason": "honest halt"}),
            encoding="utf-8",
        )

        env = dict(os.environ)
        env["ORCH_STATE_DIR"] = str(repo / ".autonomous" / "state")
        env["ORCH_MIN_FREE_GB"] = "0"
        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "fleet.py"), "needs-brain", "--json"],
            cwd=repo,
            env=env,
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
        env["ORCH_STATE_DIR"] = str(repo / ".autonomous" / "state")
        env["ORCH_MIN_FREE_GB"] = "0"

        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "fleet.py"), "needs-brain", "--json", "--require-gh"],
            cwd=repo,
            env=env,
        )
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertTrue(data.get("needs_brain"))

    def test_deep_standby_wakes_on_moved_main_and_owner_directive(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="event_wake_main_dir")
        support.write(repo / "tests" / "test_app.py", "def test_ok(): pass\n")
        (repo / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
        support.write(
            repo / ".github" / "workflows" / "ci.yml",
            "name: CI\non: [push, pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n    - run: python3 -m unittest\n",
        )
        support.commit(repo, "clean repo")

        bin_dir = support.path_with_only(self.tmp, names=("git",))
        gh = support.write(bin_dir / "gh", "#!/bin/sh\necho '[]'\n")
        gh.chmod(0o755)
        state_dir = repo / ".autonomous" / "state"
        env = support.env_with_path(bin_dir, ORCH_STATE_DIR=str(state_dir), ORCH_MIN_FREE_GB="0")

        # Initial wake records digest, then survey detects saturation + records main_sha
        nb0 = support.run([support.PYTHON, str(SCRIPTS / "fleet.py"), "needs-brain", "--json"], cwd=repo, env=env)
        self.assertEqual(nb0.returncode, 0)
        surv = support.run([support.PYTHON, str(SCRIPTS / "survey.py"), "--json"], cwd=repo, env=env)
        self.assertEqual(surv.returncode, 0)
        sat_data = json.loads((repo / ".autonomous" / "saturation.json").read_text(encoding="utf-8"))
        self.assertTrue(sat_data.get("saturated"))
        self.assertTrue(sat_data.get("main_sha"))

        # Scheduled wake while unchanged stays suppressed in deep standby
        nb_quiet = support.run([support.PYTHON, str(SCRIPTS / "fleet.py"), "needs-brain", "--json"], cwd=repo, env=env)
        self.assertEqual(nb_quiet.returncode, 0)
        self.assertFalse(json.loads(nb_quiet.stdout).get("needs_brain"))

        # 1. Moving main must wake even during deep standby
        support.write(repo / "src" / "extra.py", "x = 1\n")
        support.commit(repo, "owner pushes commit on main")
        nb_main = support.run([support.PYTHON, str(SCRIPTS / "fleet.py"), "needs-brain", "--json"], cwd=repo, env=env)
        self.assertEqual(nb_main.returncode, 0)
        self.assertTrue(json.loads(nb_main.stdout).get("needs_brain"), nb_main.stdout)

        # Second tick after moved main with no further change stays quiet
        nb_main2 = support.run([support.PYTHON, str(SCRIPTS / "fleet.py"), "needs-brain", "--json"], cwd=repo, env=env)
        self.assertFalse(json.loads(nb_main2.stdout).get("needs_brain"), nb_main2.stdout)

        # 2. Adding an owner directive must wake even during deep standby
        add_dir = support.run(
            [support.PYTHON, str(SCRIPTS / "fleet.py"), "directive", "add", "audit parser"],
            cwd=repo,
            env=env,
        )
        self.assertEqual(add_dir.returncode, 0, add_dir.stderr)
        nb_dir = support.run([support.PYTHON, str(SCRIPTS / "fleet.py"), "needs-brain", "--json"], cwd=repo, env=env)
        self.assertEqual(nb_dir.returncode, 0)
        self.assertTrue(json.loads(nb_dir.stdout).get("needs_brain"), nb_dir.stdout)

        # Second tick with same active directive stays quiet
        nb_dir2 = support.run([support.PYTHON, str(SCRIPTS / "fleet.py"), "needs-brain", "--json"], cwd=repo, env=env)
        self.assertFalse(json.loads(nb_dir2.stdout).get("needs_brain"), nb_dir2.stdout)


if __name__ == "__main__":
    unittest.main()
