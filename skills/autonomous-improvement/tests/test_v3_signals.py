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
        import hashlib

        raw_app = (repo / "src" / "app.py").read_bytes()
        app_h = hashlib.sha1(f"blob {len(raw_app)}\0".encode() + raw_app).hexdigest()[:9]
        raw_test = (repo / "tests" / "test_app.py").read_bytes()
        test_h = hashlib.sha1(f"blob {len(raw_test)}\0".encode() + raw_test).hexdigest()[:9]
        ledger = repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md"
        ledger.write_text(
            support.ledger_text(
                support.row("src/app.py", f"{app_h}:{test_h}"),
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
        # Repo has .github but no .github/workflows
        (repo / ".github").mkdir(parents=True, exist_ok=True)
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "add .github")
        proc = self.survey(repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        picks = {p["tool"]: p for p in payload["picks"]}
        self.assertIn("ci-audit", picks)
        self.assertGreaterEqual(picks["ci-audit"]["score"], 0.25)
        self.assertEqual(picks["ci-audit"]["tier"], "T1")

        bproc = support.run(
            [support.PYTHON, str(SCRIPTS / "backlog.py"), "harvest", "--json"],
            cwd=repo,
        )
        self.assertEqual(bproc.returncode, 0, bproc.stderr)
        ci_items = [it for it in json.loads(bproc.stdout).get("items", []) if it.get("track") == "ci"]
        self.assertTrue(ci_items)
        self.assertTrue(all(it.get("tier") == "T1" for it in ci_items), ci_items)

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

    def test_bug_scout_scores_bug_track_in_survey(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="bugsurvey")
        # Ensure tests exist so test_gap does not score
        support.write(repo / "tests" / "test_app.py", "def test_ok(): pass\n")
        (repo / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
        support.write(
            repo / ".github" / "workflows" / "ci.yml",
            "name: CI\non: [push, pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest\n",
        )
        # Add swallowed error
        support.write(
            repo / "src" / "worker.py",
            "def run_task():\n    try:\n        do_work()\n    except Exception:\n        pass\n",
        )
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "swallowed error")

        proc = self.survey(repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        picks = {p["tool"]: p for p in payload["picks"]}
        self.assertIn("bug-scout", picks)
        self.assertGreaterEqual(picks["bug-scout"]["score"], 0.25)
        self.assertEqual(picks["bug-scout"]["track"], "bug")
        self.assertEqual(payload["chosen"]["tool"], "bug-scout")

    def test_upstream_scores_research_track_in_survey(self) -> None:
        repo, _ = support.init_repo(self.tmp, name="upsurvey")
        support.install_scripts(repo)
        support.write(
            repo / "package.json",
            json.dumps({"name": "pkg", "dependencies": {"express": "^4.17.1"}}),
        )
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "manifest added")

        proc = self.survey(repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        picks = {p["tool"]: p for p in payload["picks"]}
        self.assertIn("upstream", picks)
        self.assertEqual(picks["upstream"]["track"], "research")
        self.assertGreaterEqual(picks["upstream"]["score"], 0.25)

    def test_l10n_generated_files_are_skipped_by_complexity_and_test_gap(self) -> None:
        repo, _ = support.init_repo(self.tmp, name="l10nskip")
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
        # Huge generated l10n file
        lines = ["// generated l10n\nclass L10nPt {\n"] + [f"  String get key_{i} => 'val_{i}';\n" for i in range(300)] + ["}\n"]
        support.write(repo / "lib" / "l10n" / "l10n_pt.dart", "".join(lines))
        support.write(repo / "lib" / "real_service.dart", "class RealService {}\n")
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "l10n and real service")

        # complexity scan must skip l10n_pt.dart
        proc_cx = support.run([support.PYTHON, str(SCRIPTS / "complexity.py"), "--json"], cwd=repo)
        self.assertEqual(proc_cx.returncode, 0)
        cx_cands = [c["file"] for c in json.loads(proc_cx.stdout).get("candidates", [])]
        self.assertNotIn("lib/l10n/l10n_pt.dart", cx_cands)

        # test_gap scan must skip l10n_pt.dart and only find real_service.dart
        proc_tg = support.run([support.PYTHON, str(SCRIPTS / "test_gap.py"), "--json"], cwd=repo)
        self.assertEqual(proc_tg.returncode, 0)
        tg_cands = [c["source"] for c in json.loads(proc_tg.stdout).get("candidates", [])]
        self.assertNotIn("lib/l10n/l10n_pt.dart", tg_cands)
        self.assertIn("lib/real_service.dart", tg_cands)

    def test_anti_starvation_normalizes_free_form_chosen_strings(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="normanti")
        support.commit(repo, "initial")

        # Write run records with realistic free-form text like agent wakes write
        rr_dir = repo / ".autonomous" / "run-records"
        rr_dir.mkdir(parents=True, exist_ok=True)
        for i in range(10):
            record = {
                "schema": 1,
                "id": f"rr-{i:03d}",
                "ts": f"2026-11-12T10:{i:02d}:00Z",
                "chosen": f"test-gap: lib/src/model/common/perf_{i}.dart",
            }
            (rr_dir / f"rr-{i:03d}.json").write_text(json.dumps(record), encoding="utf-8")

        # Create defect lead for bug-scout
        support.write(
            repo / "src" / "worker.py",
            "def run_task():\n    try:\n        do_work()\n    except Exception:\n        pass\n",
        )
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "defect added")

        proc = self.survey(repo)
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        # bug-scout hasn't run in 10 dispatches -> must receive anti-starvation boost
        picks = {p["tool"]: p for p in data.get("picks", [])}
        self.assertIn("bug-scout", picks)
        self.assertEqual(data["chosen"]["tool"], "bug-scout")

    def test_vacuous_test_smell_scores_adversarial_in_survey(self) -> None:
        repo, _ = support.python_repo(self.tmp, name="advsmell")
        support.write(
            repo / "tests" / "test_vacuous.py",
            "import time\n\ndef test_sleepy_vacuous():\n    time.sleep(0.2)\n    assert True\n",
        )
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "add vacuous test")

        proc = self.survey(repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        picks = {p["tool"]: p for p in payload.get("picks", [])}
        self.assertIn("adversarial", picks, f"adversarial missing from picks: {picks}")
        self.assertGreaterEqual(picks["adversarial"]["score"], 0.25)
        self.assertGreaterEqual(picks["adversarial"]["signals"].get("smell_count", 0), 1)

    def test_adversarial_ignores_summary_spoofing_and_stale_or_superseded_rows(self) -> None:
        import hashlib

        def blob9(p: Path) -> str:
            raw = p.read_bytes()
            return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()[:9]

        repo, _ = support.python_repo(self.tmp, name="advledger")
        vac_path = repo / "tests" / "test_vacuous.py"
        support.write(
            vac_path,
            "import time\n\ndef test_sleepy_vacuous():\n    time.sleep(0.2)\n    assert True\n",
        )
        ledger = repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md"
        app_h = blob9(repo / "src" / "app.py")

        # 1. Mentioning `tests/test_vacuous.py` in src/app.py's Summary column must NOT suppress adversarial
        ledger.write_text(
            support.ledger_text(
                support.row(
                    "src/app.py",
                    f"{app_h}:-",
                    status="COMPLETED",
                    pr="#1",
                    summary="audited alongside `tests/test_vacuous.py`",
                ),
            ),
            encoding="utf-8",
        )
        support.commit(repo, "spoofed summary row")
        p1 = json.loads(self.survey(repo).stdout)
        picks1 = {p["tool"]: p for p in p1.get("picks", [])}
        self.assertIn("adversarial", picks1, "Summary mention spoofed ledger filter!")
        self.assertGreaterEqual(picks1["adversarial"]["score"], 0.25)

        # 2. Recording a real SOUND row at matching blob hash suppresses the triaged file
        vac_h1 = blob9(vac_path)
        ledger.write_text(
            support.ledger_text(
                support.row("tests/test_vacuous.py", f"{vac_h1}:-", status="SOUND", date="2026-02-01"),
                support.row("src/app.py", f"{app_h}:-", status="SOUND", date="2026-02-01"),
            ),
            encoding="utf-8",
        )
        support.commit(repo, "record sound row")
        p2 = json.loads(self.survey(repo).stdout)
        picks2 = {p["tool"]: p for p in p2.get("picks", [])}
        self.assertEqual(picks2["adversarial"]["score"], 0.0)

        # 3. Editing tests/test_vacuous.py invalidates the old hash -> adversarial fires again
        support.write(
            vac_path,
            "import time\n\ndef test_sleepy_vacuous():\n    time.sleep(0.3)\n    assert True\n",
        )
        support.commit(repo, "modify vacuous test without re-auditing")
        p3 = json.loads(self.survey(repo).stdout)
        picks3 = {p["tool"]: p for p in p3.get("picks", [])}
        self.assertGreaterEqual(picks3["adversarial"]["score"], 0.25)
        self.assertFalse(p3.get("saturated"))

        # 4. Newer SUPERSEDED row overrides older SOUND row even at current hash
        vac_h2 = blob9(vac_path)
        ledger.write_text(
            support.ledger_text(
                support.row("tests/test_vacuous.py", f"{vac_h2}:-", status="SUPERSEDED", date="2026-02-03"),
                support.row("tests/test_vacuous.py", f"{vac_h2}:-", status="SOUND", date="2026-02-02"),
                support.row("src/app.py", f"{app_h}:-", status="SOUND", date="2026-02-01"),
            ),
            encoding="utf-8",
        )
        support.commit(repo, "supersede sound row")
        p4 = json.loads(self.survey(repo).stdout)
        picks4 = {p["tool"]: p for p in p4.get("picks", [])}
        self.assertGreaterEqual(picks4["adversarial"]["score"], 0.25)

    def test_evals_runner_cli_runs_with_stripped_docstrings_flag(self) -> None:
        proc = support.run(
            [support.PYTHON, "-OO", str(SKILL_ROOT / "evals" / "run.py"), "--list"],
            cwd=SKILL_ROOT,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
