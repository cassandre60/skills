"""Phase 4: portable config lookup (4.1) and config-less foundation runs (4.3).

The lookup order, resolved once in common.config_path:

    1. --config flag (absolute, or relative to cwd)
    2. AUTONOMOUS_CONFIG env var (absolute, or relative to cwd)
    3. <repo>/.autonomous/config.json            (canonical, when it exists)
    4. <repo>/scripts/autonomous/config.json     (legacy copy layout, unchanged)

The repo root always comes from git in the current directory, never from the
script's location — the skill folder may live anywhere. The foundation
scanners that need no config must keep running with none (docs_truth,
advisory secrets, survey); the two candidate-driven commands keep their
named refusal but name every place they looked.
"""

from __future__ import annotations

import json
import os
import shutil
import unittest
from pathlib import Path

import support

SCRIPTS = support.SCRIPTS


class ConfigLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = support.tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.repo, _ = support.init_repo(self.tmp, name="repo")
        support.install_scripts(self.repo)
        support.write(self.repo / "src" / "modern.py", "def f():\n    return 1\n")
        support.write(self.repo / "src" / "legacy.py", "def g():\n    return 2\n")
        support.write(self.repo / "src" / "flag.py", "def h():\n    return 3\n")
        support.write(
            self.repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md", support.ledger_text()
        )

    def _config(self, include: str) -> dict:
        cfg = support.load_example()
        cfg["candidates"]["A"]["include"] = [include]
        cfg["test_for_source"] = [
            {"src": r"^src/(?:.*/)?([^/]+)\.py$", "test": r"tests/test_\1.py"}
        ]
        return cfg

    def _write(self, path: Path, include: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._config(include)), encoding="utf-8")

    def _commit(self) -> None:
        support.git(self.repo, "add", "-A")
        support.git(self.repo, "commit", "-qm", "cfg")

    def _next(self, *extra: str, env=None):
        return support.run(
            [support.PYTHON, str(SCRIPTS / "ledger.py"), "next", "--stream", "A", "--json", *extra],
            cwd=self.repo,
            env=env,
        )

    def test_modern_path_wins_over_legacy(self) -> None:
        self._write(self.repo / "scripts" / "autonomous" / "config.json", "src/legacy.py")
        self._write(self.repo / ".autonomous" / "config.json", "src/modern.py")
        self._commit()
        proc = self._next()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("src/modern.py", proc.stdout)

    def test_legacy_layout_still_works(self) -> None:
        self._write(self.repo / "scripts" / "autonomous" / "config.json", "src/legacy.py")
        self._commit()
        proc = self._next()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("src/legacy.py", proc.stdout)

    def test_env_var_selects_config(self) -> None:
        external = self.tmp / "external-config.json"
        self._write(external, "src/flag.py")
        self._commit()
        env = dict(os.environ, AUTONOMOUS_CONFIG=str(external))
        proc = self._next(env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("src/flag.py", proc.stdout)

    def test_env_var_missing_file_is_a_named_error(self) -> None:
        self._commit()
        env = dict(os.environ, AUTONOMOUS_CONFIG=str(self.tmp / "nope.json"))
        proc = self._next(env=env)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("AUTONOMOUS_CONFIG", proc.stderr)
        self.assertIn("config not found", proc.stderr)

    def test_explicit_flag_beats_env_var(self) -> None:
        via_flag = self.tmp / "flag-config.json"
        via_env = self.tmp / "env-config.json"
        self._write(via_flag, "src/flag.py")
        self._write(via_env, "src/legacy.py")
        self._commit()
        env = dict(os.environ, AUTONOMOUS_CONFIG=str(via_env))
        proc = self._next("--config", str(via_flag), env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("src/flag.py", proc.stdout)

    def test_repo_root_comes_from_cwd_not_script_location(self) -> None:
        # The scripts live in the skill tree; the repo (with its own
        # .autonomous/config.json and no legacy copy) is elsewhere.
        self._write(self.repo / ".autonomous" / "config.json", "src/modern.py")
        self._commit()
        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "ledger.py"), "next", "--stream", "A", "--json"],
            cwd=self.repo,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("src/modern.py", proc.stdout)

    def test_missing_config_message_names_every_location(self) -> None:
        self._commit()
        proc = self._next()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no config file at", proc.stderr)
        self.assertIn(".autonomous/config.json", proc.stderr)
        self.assertIn("scripts/autonomous/config.json", proc.stderr)


class ConfigLessRunsTests(unittest.TestCase):
    """4.3 pins: the foundation scanners run in a repo with no config at all."""

    def setUp(self) -> None:
        self._tmp = support.tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.repo, _ = support.init_repo(self.tmp, name="bare")
        support.write(self.repo / "src" / "app.py", "def f(a, b):\n    return a == b\n")
        (self.repo / "README.md").write_text("# app\n", encoding="utf-8")
        support.git(self.repo, "add", "-A")
        support.git(self.repo, "commit", "-qm", "base")

    def _script(self, name: str, *args: str):
        return support.run(
            [support.PYTHON, str(SCRIPTS / name), *args], cwd=self.repo
        )

    def test_docs_truth_runs_without_config(self) -> None:
        proc = self._script("docs_truth.py", "check")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("0 findings", proc.stdout)

    def test_advisory_secrets_runs_without_config(self) -> None:
        proc = self._script("advisory.py", "secrets")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("0", proc.stdout)

    def test_survey_runs_without_config(self) -> None:
        proc = self._script("survey.py", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIn("picks", payload)


class FleetTierTests(unittest.TestCase):
    """4.6: trust tier T1 keeps publish in issue mode; stats reports eligibility only."""

    def setUp(self) -> None:
        self._tmp = support.tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.repo, _ = support.python_repo(self.tmp, name="tiers")
        support.commit(self.repo, "base2")

    def _publish(self, *args: str):
        return support.run(
            [support.PYTHON, str(SCRIPTS / "fleet.py"), "publish", *args],
            cwd=self.repo,
        )

    def _set_tier(self, tier: str) -> None:
        path = self.repo / "scripts" / "autonomous" / "config.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["trust"] = {"tier": tier}
        path.write_text(json.dumps(cfg), encoding="utf-8")

    def _stub_gh(self, merged: int, closed_unmerged: int) -> Path:
        import shlex

        prs = []
        for i in range(merged):
            prs.append(
                {
                    "number": i + 1,
                    "title": f"Stream A: pr {i + 1}",
                    "headRefName": f"task/pr-{i + 1}",
                    "state": "MERGED",
                    "createdAt": "2026-10-01T00:00:00Z",
                    "mergedAt": "2026-10-02T00:00:00Z",
                    "closedAt": "2026-10-02T00:00:00Z",
                    "body": "",
                }
            )
        for i in range(closed_unmerged):
            prs.append(
                {
                    "number": 100 + i,
                    "title": f"Stream A: closed {i + 1}",
                    "headRefName": f"task/closed-{i + 1}",
                    "state": "CLOSED",
                    "createdAt": "2026-10-01T00:00:00Z",
                    "mergedAt": None,
                    "closedAt": "2026-10-03T00:00:00Z",
                    "body": "",
                }
            )
        payload = json.dumps(prs)
        bin_dir = support.stub_path(
            self.tmp,
            {"gh": f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(payload)}\n"},
        )
        git_link = bin_dir / "git"
        if not git_link.exists():
            git_link.symlink_to(shutil.which("git"))
        return bin_dir

    def test_publish_pr_mode_refused_at_explicit_t1(self) -> None:
        self._set_tier("T1")
        proc = self._publish("--pr", "7")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("issue-mode only", proc.stderr)
        self.assertIn("T1", proc.stderr)

    def test_publish_pr_mode_refused_under_ai_policy_even_at_t2(self) -> None:
        self._set_tier("T2")
        (self.repo / "CONTRIBUTING.md").write_text(
            "# Contributing\n\nAll changes must be human-authored.\n",
            encoding="utf-8",
        )
        proc = self._publish("--pr", "7")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("issue-mode only", proc.stderr)
        self.assertIn("AI-contribution policy", proc.stderr)

    def test_publish_pr_mode_legacy_config_without_trust_key_unchanged(self) -> None:
        # A config with no trust key (pre-tier legacy layout) keeps today's
        # behavior: the gate fires only on explicit T1 or an AI policy.
        proc = self._publish("--pr", "7")
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("issue-mode only", proc.stderr)
        self.assertIn("publishable", proc.stderr)

    def test_publish_pr_mode_passes_tier_gate_at_t2(self) -> None:
        self._set_tier("T2")
        proc = self._publish("--pr", "7")
        # Past the tier gate: it now fails on the missing evidence artifact.
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("issue-mode only", proc.stderr)
        self.assertIn("publishable", proc.stderr)

    def test_stats_reports_t2_eligibility(self) -> None:
        bin_dir = self._stub_gh(merged=8, closed_unmerged=4)
        env = support.env_with_path(bin_dir)
        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "fleet.py"), "stats", "--json", "--days", "0"],
            cwd=self.repo,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["decided_items"], 12)
        self.assertTrue(data["t2_eligible"], data.get("t2_reason"))
        text = support.run(
            [support.PYTHON, str(SCRIPTS / "fleet.py"), "stats", "--days", "0"],
            cwd=self.repo,
            env=env,
        )
        self.assertIn("eligible for T2", text.stdout)

    def test_stats_silent_when_not_eligible(self) -> None:
        bin_dir = self._stub_gh(merged=2, closed_unmerged=1)
        env = support.env_with_path(bin_dir)
        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "fleet.py"), "stats", "--days", "0"],
            cwd=self.repo,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("eligible for T2", proc.stdout)


class Python39AndOptimizePortabilityTests(unittest.TestCase):
    def test_docs_truth_symbol_drift_works_without_stdlib_module_names(self) -> None:
        import sys
        import tempfile

        sys.path.insert(0, str(SCRIPTS))
        import docs_truth

        saved = getattr(sys, "stdlib_module_names", None)
        try:
            if hasattr(sys, "stdlib_module_names"):
                delattr(sys, "stdlib_module_names")
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                doc = root / "README.md"
                doc.write_text("```python\nimport json\nfrom pathlib import Path\n```\n", encoding="utf-8")
                blocks = docs_truth.parse_fences(doc.read_text(encoding="utf-8"))
                findings = docs_truth.scan_symbol_drift(doc, root, blocks, [root])
                self.assertEqual(findings, [])
        finally:
            if saved is not None:
                sys.stdlib_module_names = saved  # type: ignore[attr-defined]

    def test_docs_truth_executable_blocks_use_sys_executable(self) -> None:
        import subprocess
        import sys
        import tempfile
        from unittest import mock

        sys.path.insert(0, str(SCRIPTS))
        import docs_truth

        blocks = [{"lang": "python", "runnable": True, "code": "print(1)", "start_line": 1}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = root / "README.md"
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="1\n", stderr="")
            with mock.patch.object(docs_truth.subprocess, "run", return_value=completed) as run_mock:
                findings = docs_truth.scan_runnable(doc, root, blocks, timeout=5)
                self.assertEqual(findings, [])
                called_argv = run_mock.call_args[0][0]
                self.assertEqual(called_argv[0], sys.executable)

    def test_scripts_help_works_under_python_oo_stripped_docstrings(self) -> None:
        for name in (
            "advisory.py",
            "conventions.py",
            "docs_truth.py",
            "flakes.py",
            "fuzz_shrink.py",
            "meta.py",
            "onboard.py",
            "perf_gate.py",
            "pins.py",
            "profile.py",
            "ratchet.py",
            "repair.py",
            "runrecord.py",
            "survey.py",
        ):
            with self.subTest(script=name):
                proc = support.run(
                    [support.PYTHON, "-OO", str(SCRIPTS / name), "--help"],
                    cwd=SCRIPTS.parent.parent,
                )
                self.assertEqual(proc.returncode, 0, f"{name}: {proc.stderr}")

    def test_docs_truth_self_check_on_skill_spec_is_clean(self) -> None:
        skill_root = SCRIPTS.parent.parent
        proc = support.run(
            [
                support.PYTHON,
                str(SCRIPTS / "docs_truth.py"),
                "check",
                "--paths",
                ".",
                "--spec",
                "SPEC.md",
                "--json",
            ],
            cwd=skill_root,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["candidates"], [])

    def test_pure_python_test_classes_do_not_require_linux_flock(self) -> None:
        import ast

        tree = ast.parse((SCRIPTS.parent.parent / "tests" / "test_v2.py").read_text(encoding="utf-8"))
        pure_python_classes = {
            "EvalHarnessTests",
            "RepairStreamTests",
            "FuzzSeedTests",
            "StreamLTests",
        }
        flagged = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name in pure_python_classes:
                if node.decorator_list:
                    flagged.append(node.name)
        self.assertEqual(flagged, [])

    def test_check_skills_ignores_dot_autonomous_runtime_subdir(self) -> None:
        import importlib.util
        import tempfile

        repo_root = SCRIPTS.parent.parent.parent.parent
        spec = importlib.util.spec_from_file_location(
            "check_skills", repo_root / "scripts" / "check_skills.py"
        )
        assert spec is not None and spec.loader is not None
        check_skills = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check_skills)

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_root = Path(tmpdir)
            skill_dir = fake_root / "skills" / "sample"
            (skill_dir / ".autonomous").mkdir(parents=True)
            (skill_dir / ".autonomous" / "plan-progress.md").write_text(
                "Run `scripts/check_discovery.sh` at repo root.\n", encoding="utf-8"
            )
            check_skills.ROOT = fake_root
            issues: list = []
            check_skills.check_relative_references(skill_dir, issues)
            self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
