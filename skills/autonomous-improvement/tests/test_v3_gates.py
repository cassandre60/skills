"""Tests for V3 Milestone 4: Track Briefs, Diff Budgets & Governor Track Caps."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import support

SKILL_ROOT = support.SKILL_ROOT
SCRIPTS = support.SCRIPTS


class TrackGatesAndGovernorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = support.tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_track_briefs_documented_and_short(self) -> None:
        roles_doc = (SKILL_ROOT / "references" / "04-workflow-and-roles.md").read_text(encoding="utf-8")
        self.assertIn("## Track Briefs", roles_doc)
        for track in ("CI / Tooling", "Test Engineer", "Bug Scout & Fixer", "Refactorer", "Research Analyst"):
            self.assertIn(track, roles_doc)

    def test_diff_budget_exceeded_fails_verify_gate(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="diffbudget")
        support.commit(repo, "initial commit")

        # Configure strict diff budget
        support.write_config(
            repo,
            {
                **support.load_example(),
                "format_check_cmd": None,
                "analyze_cmd": None,
                "gate_cmd": None,
                "fleet": {"diff_budget": {"max_files": 2, "max_lines": 50, "enforce": True}},
            },
        )
        support.commit(repo, "config added")

        # Exceed file budget on feature branch: add 3 files
        support.git(repo, "checkout", "-b", "feature/budget")
        support.write(repo / "src" / "f1.py", "x = 1\n")
        support.write(repo / "src" / "f2.py", "x = 2\n")
        support.write(repo / "src" / "f3.py", "x = 3\n")
        support.commit(repo, "too many files")

        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "orchestrator.py"), "verify-gate", "--worktree", str(repo)],
            cwd=repo,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("diff budget exceeded", (proc.stderr + proc.stdout).lower())

    def test_track_caps_reported_in_govern(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="trackcaps")
        support.commit(repo, "initial commit")

        support.write_config(
            repo,
            {
                **support.load_example(),
                "fleet": {"track_caps": {"refactor": 1}},
            },
        )
        support.commit(repo, "config added")

        # Mock open PR for refactor track
        prs = [
            {
                "number": 10,
                "title": "refactor: simplify parser logic",
                "state": "OPEN",
                "labels": [{"name": "track:refactor"}],
                "statusCheckRollup": [],
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
            [support.PYTHON, str(SCRIPTS / "fleet.py"), "govern", "--json", "--require-gh"],
            cwd=repo,
            env=env,
        )
        data = json.loads(proc.stdout)
        self.assertIn("track_caps", data)
        self.assertEqual(data["track_caps"].get("refactor", {}).get("open"), 1)
        self.assertTrue(data["track_caps"].get("refactor", {}).get("capped"))

        # Govern with explicit --track refactor blocks dispatch
        proc_track = support.run(
            [support.PYTHON, str(SCRIPTS / "fleet.py"), "govern", "--track", "refactor", "--json", "--require-gh"],
            cwd=repo,
            env=env,
        )
        self.assertNotEqual(proc_track.returncode, 0)
        track_data = json.loads(proc_track.stdout)
        self.assertFalse(track_data["dispatch"])
        self.assertIn("track cap reached for refactor", track_data["reason"])

    def test_track_gate_enforcement_in_verify_gate(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="trackgates")
        support.commit(repo, "initial commit")

        # Configure no base checks
        support.write_config(
            repo,
            {
                **support.load_example(),
                "format_check_cmd": None,
                "analyze_cmd": None,
                "gate_cmd": None,
                "verify_gate": {"weakening": "off", "evidence": "off"},
            },
        )
        support.commit(repo, "config added")

        support.git(repo, "checkout", "-b", "feature/tracktest")
        support.write(repo / "src" / "app.py", "x = 1\n")
        support.commit(repo, "prod change")

        # 1. Research track is issue-only -> fails
        proc_research = support.run(
            [support.PYTHON, str(SCRIPTS / "orchestrator.py"), "verify-gate", "--worktree", str(repo), "--track", "research"],
            cwd=repo,
        )
        self.assertNotEqual(proc_research.returncode, 0)
        self.assertIn("issue-only", (proc_research.stderr + proc_research.stdout).lower())

        # 2. CI track touching production code -> fails
        proc_ci = support.run(
            [support.PYTHON, str(SCRIPTS / "orchestrator.py"), "verify-gate", "--worktree", str(repo), "--track", "ci"],
            cwd=repo,
        )
        self.assertNotEqual(proc_ci.returncode, 0)
        self.assertIn("ci track violation", (proc_ci.stderr + proc_ci.stdout).lower())

        # 3. Test track without evidence artifact -> fails
        proc_test = support.run(
            [support.PYTHON, str(SCRIPTS / "orchestrator.py"), "verify-gate", "--worktree", str(repo), "--track", "test"],
            cwd=repo,
        )
        self.assertNotEqual(proc_test.returncode, 0)
        self.assertIn("evidence artifact", (proc_test.stderr + proc_test.stdout).lower())

    def test_survey_anti_starvation_and_penalty(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="surveybal")
        support.commit(repo, "initial commit")

        # Write 10 run records where 'test-gap' was repeatedly chosen
        rr_dir = repo / ".autonomous" / "run-records"
        rr_dir.mkdir(parents=True, exist_ok=True)
        for i in range(10):
            record = {
                "schema": 1,
                "id": f"rr-{i:03d}",
                "ts": f"2026-11-10T10:{i:02d}:00Z",
                "chosen": "test-gap",
            }
            (rr_dir / f"rr-{i:03d}.json").write_text(json.dumps(record), encoding="utf-8")

        # Create a trigger for docs-reconcile / ci track so score > 0
        support.write(repo / "README.md", "Drifted content\n")

        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "survey.py"), "--json"],
            cwd=repo,
        )
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        picks = {p["tool"]: p for p in data.get("picks", [])}

        # test-gap was picked in last 3 runs, so it received recency penalty
        # docs-reconcile was not picked in 10 runs and has eligible signals, so boosted
        if "test-gap" in picks and "docs-reconcile" in picks:
            self.assertIn(picks["test-gap"]["track"], ("test",))


if __name__ == "__main__":
    unittest.main()
