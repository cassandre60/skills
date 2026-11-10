"""Tests for V3 Milestone 2: Survey signals for test gaps, CI audit, complexity, simplification, and fuzz."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import support

SKILL_ROOT = support.SKILL_ROOT
SCRIPTS = support.SCRIPTS


class SurveySignalsTests(unittest.TestCase):
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

    def test_untested_core_module_selects_test_gap_over_mutation(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="testgap")
        # Ensure CI is present and green
        support.write(
            repo / ".github" / "workflows" / "ci.yml",
            "name: CI\non: [push, pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest\n",
        )
        # Add a core module with NO test
        support.write(
            repo / "src" / "core_logic.py", "def compute(x):\n    return x * 2\n"
        )
        # Ledger has src/app.py audited
        ledger = repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md"
        ledger.write_text(
            support.ledger_text(
                support.row("src/app.py", "aa11bb"),
            ),
            encoding="utf-8",
        )
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "core logic added")

        proc = self.survey(repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIsNotNone(payload["chosen"])
        self.assertEqual(payload["chosen"]["tool"], "test-gap")

    def test_missing_ci_workflows_scores_ci_audit(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="ciaudit")
        # Repo has no .github/workflows
        proc = self.survey(repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        picks = {p["tool"]: p for p in payload["picks"]}
        self.assertIn("ci-audit", picks)
        self.assertGreaterEqual(picks["ci-audit"]["score"], 0.25)

    def test_dead_code_scores_simplification(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="deadcode")
        # Introduce unreachable statement after return
        support.write(
            repo / "src" / "app.py",
            "def f(a, b):\n    return a == b\n    print('dead')\n",
        )
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "dead code")
        proc = self.survey(repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        picks = {p["tool"]: p for p in payload["picks"]}
        self.assertIn("simplification", picks)
        self.assertGreaterEqual(picks["simplification"]["score"], 0.25)

    def test_parser_target_scores_fuzz(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="fuzzparser")
        support.write(
            repo / "src" / "json_parser.py",
            "def parse_tokens(s):\n    return s.split()\n",
        )
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "parser added")
        proc = self.survey(repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        picks = {p["tool"]: p for p in payload["picks"]}
        self.assertIn("fuzz", picks)
        self.assertGreaterEqual(picks["fuzz"]["score"], 0.25)

    def test_complexity_scores_ratchet_refactor(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="complexity")
        # Add a large file with > 500 lines
        lines = ["# Large file\n"] + [f"def func_{i}():\n    return {i}\n" for i in range(260)]
        support.write(repo / "src" / "large_module.py", "".join(lines))
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "large module added")
        proc = self.survey(repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        picks = {p["tool"]: p for p in payload["picks"]}
        self.assertIn("complexity", picks)
        self.assertGreaterEqual(picks["complexity"]["score"], 0.25)

    def test_dart_test_gap_finds_untested_file(self) -> None:
        repo, _ = support.init_repo(self.tmp, name="dartrepo")
        support.install_scripts(repo)
        support.write(
            repo / "scripts" / "autonomous" / "config.json",
            json.dumps(
                {
                    "test_for_source": [{"src": "^lib/(.*)\\.dart$", "test": "test/\\1_test.dart"}],
                    "candidates": {"A": {"include": ["lib/**/*.dart"], "exclude": []}},
                }
            ),
        )
        support.write(repo / "lib" / "service.dart", "class Service {}\n")
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "dart service added")

        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "test_gap.py"), "--json"],
            cwd=repo,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        untested_files = [c["source"] for c in data.get("candidates", [])]
        self.assertIn("lib/service.dart", untested_files)

    def test_typescript_test_gap_finds_untested_file(self) -> None:
        repo, _ = support.init_repo(self.tmp, name="tsrepo")
        support.install_scripts(repo)
        support.write(
            repo / "scripts" / "autonomous" / "config.json",
            json.dumps(
                {
                    "test_for_source": [{"src": "^src/(.*)\\.ts$", "test": "src/\\1.test.ts"}],
                    "candidates": {"A": {"include": ["src/**/*.ts"], "exclude": []}},
                }
            ),
        )
        support.write(repo / "src" / "service.ts", "export class Service {}\n")
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "ts service added")

        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "test_gap.py"), "--json"],
            cwd=repo,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        untested_files = [c["source"] for c in data.get("candidates", [])]
        self.assertIn("src/service.ts", untested_files)


if __name__ == "__main__":
    unittest.main()
