"""Phase 3: autonomy resolution — the SKILL.md contract and the survey oracle.

Red-first suite for the plan's Phase 3: a bare invocation must resolve
autonomy itself (auto mode, survey, one pass, at most one PR or issue), the
Roles reference must not make the skill's presence alone an Orchestrator
trigger, and `survey.py` must rank tools from 0-token signals without gh,
config, or a profile.
"""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

import support

SKILL_ROOT = support.SKILL_ROOT
SCRIPTS = support.SCRIPTS


def _flat(text: str) -> str:
    return " ".join(text.split())


class SkillAutonomyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    def test_skill_has_autonomy_resolution_section(self) -> None:
        flat = _flat(self.skill)
        self.assertIn("Autonomy resolution", flat)
        self.assertIn("bare invocation", flat)
        self.assertIn("`auto`", flat)
        self.assertIn("`ask`", flat)
        self.assertIn("owner-ask", flat)

    def test_ask_owner_phrase_lives_only_in_ask_mode(self) -> None:
        for line in self.skill.splitlines():
            if "ask the owner what to improve" in line:
                self.assertIn("ask", line.lower())
                self.assertIn("Explicit task", self.skill)

    def test_final_report_format_documented(self) -> None:
        flat = _flat(self.skill)
        self.assertIn("Final report", flat)
        self.assertIn("runner-up", flat)
        self.assertIn("what needs the owner", flat)
        self.assertIn("not configured", flat)

    def test_roles_fleet_conditioned_not_only_instruction(self) -> None:
        # Flatten so assertions survive markdown re-wrapping ("explicit
        # \n  owner ask" is the same phrase as "explicit owner ask").
        roles = " ".join(
            (SKILL_ROOT / "references" / "04-workflow-and-roles.md")
            .read_text(encoding="utf-8")
            .split()
        )
        self.assertNotIn("only instruction", roles)
        self.assertIn("session.environment", roles)
        self.assertIn("explicit owner", roles)
        unattended = " ".join(
            (SKILL_ROOT / "references" / "05-unattended-operation.md")
            .read_text(encoding="utf-8")
            .split()
        )
        self.assertNotIn("only instruction", unattended)


class SurveyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = support.tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def survey(self, repo: Path, *args: str, env=None):
        return support.run(
            [support.PYTHON, str(SCRIPTS / "survey.py"), "--json", *args],
            cwd=repo,
            env=env,
        )

    def _clean_repo(self) -> Path:
        repo, _ = support.python_repo(self.tmp, name="clean")
        ledger = repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md"
        ledger.write_text(
            support.ledger_text(
                support.row("src/app.py", "aa11bb"),
                support.row("tests/test_app.py", "cc22dd", status="SOUND"),
            ),
            encoding="utf-8",
        )
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "ledger sound")
        return repo

    def _red_ci_repo(self) -> Path:
        repo, _ = support.python_repo(self.tmp, name="redci")
        fix_root = Path(__file__).resolve().parent.parent / "evals" / "fixtures"
        stub = fix_root / "red-ci-python" / "bin"
        shutil.copytree(stub, repo / "bin")
        support.run(["chmod", "+x", str(repo / "bin" / "gh")], cwd=repo)
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "gh stub")
        env = support.env_with_path(repo / "bin")
        return repo, env

    def test_red_ci_selects_defect_repair(self) -> None:
        repo, env = self._red_ci_repo()
        proc = self.survey(repo, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIsNotNone(payload["chosen"])
        self.assertEqual(payload["chosen"]["tool"], "defect-repair")

    def test_docs_drift_selects_docs_reconcile(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="drift")
        (repo / "README.md").write_text(
            "See [the guide](docs/guide.md) for details.\n", encoding="utf-8"
        )
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "drift")
        proc = self.survey(repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIsNotNone(payload["chosen"])
        self.assertEqual(payload["chosen"]["tool"], "docs-reconcile")

    def test_clean_repo_returns_null(self) -> None:
        repo = self._clean_repo()
        proc = self.survey(repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIsNone(payload["chosen"])

    def test_identical_input_gives_byte_identical_output(self) -> None:
        repo, env = self._red_ci_repo()
        first = self.survey(repo, env=env)
        second = self.survey(repo, env=env)
        self.assertEqual(first.stdout, second.stdout)

    def test_works_with_no_gh_no_config_no_profile(self) -> None:
        repo, _ = support.init_repo(self.tmp, name="bare")
        support.write(repo / "src" / "app.py", "def f(a, b):\n    return a == b\n")
        support.write(
            repo / "tests" / "test_app.py",
            "from src.app import f\n\ndef test_f():\n    assert f(1, 1) is True\n",
        )
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "base")
        bin_dir = support.path_with_only(self.tmp, names=("git",))
        env = support.env_with_path(bin_dir)
        proc = self.survey(repo, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIn("picks", payload)

    def test_unconfigured_stream_a_needs_config_and_not_chosen(self) -> None:
        repo, _ = support.init_repo(self.tmp, name="nocfg")
        support.write(repo / "src" / "app.py", "def f(a, b):\n    return a == b\n")
        support.write(
            repo / "tests" / "test_app.py",
            "from src.app import f\n\ndef test_f():\n    assert f(1, 1) is True\n",
        )
        (repo / "README.md").write_text("# app\n", encoding="utf-8")
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "base")
        proc = self.survey(repo)
        payload = json.loads(proc.stdout)
        picks = {p["tool"]: p for p in payload["picks"]}
        self.assertIn("mutation", picks)
        self.assertTrue(picks["mutation"]["needs_config"])
        if payload["chosen"] is not None:
            self.assertNotEqual(payload["chosen"]["tool"], "mutation")


if __name__ == "__main__":
    unittest.main()
