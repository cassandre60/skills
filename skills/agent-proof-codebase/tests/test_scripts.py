"""Unit tests for agent-proof-codebase gate scripts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"


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


class AgentProofScriptsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_fail_to_pass_preserves_caller_branch_and_uses_clean_head_worktree(self) -> None:
        repo = self.tmp / "repo"
        _init_repo(repo)
        (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        _git(repo, "add", "calc.py")
        _git(repo, "commit", "-m", "buggy base")

        _git(repo, "checkout", "-b", "fix/add-bug")
        (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (repo / "test_calc.py").write_text(
            "from calc import add\nassert add(2, 3) == 5, 'ADD_BROKEN'\n",
            encoding="utf-8",
        )
        _git(repo, "add", "calc.py", "test_calc.py")
        _git(repo, "commit", "-m", "fix add with regression test")

        # Also plant an uncommitted broken edit in the caller's working tree:
        # fail_to_pass_check.sh must neither clobber it nor detach the caller's branch.
        (repo / "notes.txt").write_text("wip note\n", encoding="utf-8")

        env = dict(os.environ)
        env["EXPECT_PATTERN"] = "ADD_BROKEN"
        proc = subprocess.run(
            ["bash", str(SCRIPTS / "fail_to_pass_check.sh"), "main", f"{sys.executable} test_calc.py"],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")

        # Caller's symbolic branch ref MUST still be fix/add-bug (never detached!)
        branch_proc = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            branch_proc.returncode,
            0,
            f"caller HEAD was detached! stderr={branch_proc.stderr}",
        )
        self.assertEqual(branch_proc.stdout.strip(), "fix/add-bug")
        self.assertEqual((repo / "notes.txt").read_text(encoding="utf-8"), "wip note\n")

    def test_fail_to_pass_rejects_test_that_passes_on_base(self) -> None:
        repo = self.tmp / "vacuous"
        _init_repo(repo)
        (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        _git(repo, "add", "calc.py")
        _git(repo, "commit", "-m", "base")

        _git(repo, "checkout", "-b", "fix/noop")
        (repo / "test_calc.py").write_text("assert 1 + 1 == 2\n", encoding="utf-8")
        _git(repo, "add", "test_calc.py")
        _git(repo, "commit", "-m", "vacuous test")

        proc = subprocess.run(
            ["bash", str(SCRIPTS / "fail_to_pass_check.sh"), "main", f"{sys.executable} test_calc.py"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("tests pass on the base code", proc.stderr)

    def test_protected_paths_ratchet_and_spec_trace_gates(self) -> None:
        repo = self.tmp / "gates"
        _init_repo(repo)
        (repo / ".gates").mkdir()
        (repo / ".gates" / "protected-paths.txt").write_text("SPEC.md\n.gates/*\n", encoding="utf-8")
        (repo / "SPEC.md").write_text("# Spec\n- INV-001: balance non-negative\n", encoding="utf-8")
        (repo / "tests").mkdir()
        (repo / "tests" / "test_inv.py").write_text("# covers INV-001\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "seed gates")

        _git(repo, "checkout", "-b", "feat/touch-spec")
        (repo / "SPEC.md").write_text("# Spec\n- INV-001: updated\n- INV-002: uncovered\n", encoding="utf-8")
        _git(repo, "add", "SPEC.md")
        _git(repo, "commit", "-m", "edit spec")

        prot_fail = subprocess.run(
            [sys.executable, str(SCRIPTS / "protected_paths_check.py"), "--base", "main"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(prot_fail.returncode, 3)

        prot_ok = subprocess.run(
            [sys.executable, str(SCRIPTS / "protected_paths_check.py"), "--base", "main", "--approved"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(prot_ok.returncode, 0)

        trace_fail = subprocess.run(
            [sys.executable, str(SCRIPTS / "spec_trace_check.py"), "--spec", "SPEC.md", "--tests", "tests"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(trace_fail.returncode, 1)
        self.assertIn("INV-002", trace_fail.stdout)

        base_json = repo / "baseline.json"
        cur_json = repo / "current.json"
        base_json.write_text(
            json.dumps({"metrics": {"score": {"value": 0.8, "direction": "higher_is_better"}}}),
            encoding="utf-8",
        )
        cur_json.write_text(json.dumps({"score": 0.75}), encoding="utf-8")
        ratchet_fail = subprocess.run(
            [sys.executable, str(SCRIPTS / "ratchet_check.py"), "--baseline", str(base_json), "--current", str(cur_json)],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ratchet_fail.returncode, 1)


if __name__ == "__main__":
    unittest.main()
