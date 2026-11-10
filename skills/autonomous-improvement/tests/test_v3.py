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
            (SKILL_ROOT / "fleet-ops" / "references" / "05-unattended-operation.md")
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

    def _backlog_repo(
        self, status: str = "OPEN", finding: str = "impossible combo"
    ) -> Path:
        repo = self._clean_repo()
        support.write(
            repo / "docs" / "STATE_MACHINE_BACKLOG.md",
            "# State Machine Backlog\n\n"
            "| Id | Machine | Severity | Encoded At | Finding | Trigger | "
            "Failing Test | Status |\n"
            "|---|---|---|---|---|---|---|---|\n"
            f"| drag-1 | drag | high | `src/app.py:1` | {finding} | "
            f"drag while saving | `tests/test_app.py::test_f` | {status} |\n",
        )
        support.git(repo, "add", "-A")
        support.git(repo, "commit", "-qm", "backlog")
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

    def test_open_state_machine_backlog_selects_lens(self) -> None:
        repo = self._backlog_repo()
        proc = self.survey(repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIsNotNone(payload["chosen"])
        self.assertEqual(payload["chosen"]["tool"], "lens")
        self.assertEqual(payload["chosen"]["signals"]["open_state_machine_findings"], 1)

    def test_backlog_finding_with_pipe_still_counts(self) -> None:
        repo = self._backlog_repo(finding="isSaving | isEditing")
        proc = self.survey(repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIsNotNone(payload["chosen"])
        self.assertEqual(payload["chosen"]["tool"], "lens")
        self.assertEqual(payload["chosen"]["signals"]["open_state_machine_findings"], 1)

    def test_closed_state_machine_backlog_is_not_selected(self) -> None:
        repo = self._backlog_repo(status="FIXED #12")
        proc = self.survey(repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        if payload["chosen"] is not None:
            self.assertNotEqual(payload["chosen"]["tool"], "lens")

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


class RateLimitAndCircuitBreakerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = support.tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _stage_wake_repo(self, prs=None):
        repo, origin = support.init_repo(self.tmp, name="wake-repo", with_origin=True)
        support.install_scripts(repo)
        support.write_config(repo)
        json_path = self.tmp / "wake-prs.json"
        json_path.write_text(json.dumps(prs or []) + "\n")
        bin_dir = support.path_with_only(self.tmp, names=("git",))
        gh = support.write(
            bin_dir / "gh",
            "#!/bin/sh\nwhile IFS= read -r line; do printf '%s\\n' \"$line\"; done < \"$GH_JSON\"\n",
        )
        gh.chmod(0o755)
        state_dir = repo / ".autonomous" / "state"
        env = support.env_with_path(
            bin_dir,
            GH_JSON=str(json_path),
            ORCH_STATE_DIR=str(state_dir),
        )
        return repo, env, json_path, state_dir

    def test_moved_main_wakes_idle_fleet(self) -> None:
        repo, env, _json_path, _state_dir = self._stage_wake_repo(prs=[])
        # First wake: idle queue -> needs_brain is True, records digest
        first = support.script(repo, "fleet.py", "needs-brain", "--json", env=env)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue(json.loads(first.stdout)["needs_brain"])

        # Second wake: state unchanged -> quiet
        second = support.script(repo, "fleet.py", "needs-brain", "--json", env=env)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertFalse(json.loads(second.stdout)["needs_brain"])

        # Now main moves: new commit on repo
        support.write(repo / "new_file.txt", "new work landed on main\n")
        support.git(repo, "add", "new_file.txt")
        support.git(repo, "commit", "-qm", "feat: landed on main")

        # Third wake: queue is still idle, BUT main moved -> needs_brain must be True!
        third = support.script(repo, "fleet.py", "needs-brain", "--json", env=env)
        self.assertEqual(third.returncode, 0, third.stderr)
        payload = json.loads(third.stdout)
        self.assertTrue(payload["needs_brain"], "moved main must wake an idle fleet")

    def test_labelled_issue_wakes_idle_fleet(self) -> None:
        repo, _env, _json_path, _state_dir = self._stage_wake_repo(prs=[])
        bin_dir = support.path_with_only(self.tmp, names=("git",))
        issue_json = self.tmp / "issues.json"
        issue_json.write_text("[]\n")
        gh = support.write(
            bin_dir / "gh",
            "#!/bin/sh\n"
            'case "$1 $2" in\n'
            '  "pr list") echo "[]" ;;\n'
            f'  "issue list") while IFS= read -r l; do printf \'%s\\n\' "$l"; done < "{issue_json}" ;;\n'
            '  *) echo "[]" ;;\n'
            'esac\n',
        )
        gh.chmod(0o755)
        env = support.env_with_path(bin_dir, ORCH_STATE_DIR=str(repo / ".autonomous" / "state"))

        first = support.script(repo, "fleet.py", "needs-brain", "--json", env=env)
        self.assertTrue(json.loads(first.stdout)["needs_brain"])

        second = support.script(repo, "fleet.py", "needs-brain", "--json", env=env)
        self.assertFalse(json.loads(second.stdout)["needs_brain"])

        # Add an issue with autonomous-improvement label
        issue_json.write_text(
            json.dumps([
                {
                    "number": 42,
                    "title": "Fix flaky parser",
                    "labels": [{"name": "autonomous-improvement"}],
                    "updatedAt": "2026-11-10T12:00:00Z",
                }
            ])
            + "\n"
        )

        third = support.script(repo, "fleet.py", "needs-brain", "--json", env=env)
        payload = json.loads(third.stdout)
        self.assertTrue(payload["needs_brain"], "new labelled issue must wake fleet")

    def test_wake_cooldown_parks_429_model(self) -> None:
        repo, env, _json_path, state_dir = self._stage_wake_repo(prs=[])
        harness = support.write(
            self.tmp / "fake_harness.sh",
            "#!/bin/sh\necho 'HTTP 429: rate limit exceeded' >&2\nexit 1\n",
        )
        harness.chmod(0o755)
        res = support.script(
            repo,
            "fleet.py",
            "wake",
            "--harness-cmd",
            f"{harness} {{prompt_file}}",
            "--model",
            "test-provider/test-model",
            env=env,
        )
        self.assertNotEqual(res.returncode, 0)
        fail_file = state_dir / "model_failures"
        self.assertTrue(fail_file.exists())
        self.assertIn("test-provider/test-model", fail_file.read_text())

        marker = self.tmp / "harness_ran.txt"
        probe_harness = support.write(
            self.tmp / "probe_harness.sh",
            f"#!/bin/sh\ntouch {marker}\nexit 0\n",
        )
        probe_harness.chmod(0o755)
        res2 = support.script(
            repo,
            "fleet.py",
            "wake",
            "--harness-cmd",
            f"{probe_harness} {{prompt_file}}",
            "--model",
            "test-provider/test-model",
            env=env,
        )
        self.assertEqual(res2.returncode, 0)
        self.assertFalse(marker.exists(), "cooling model should not be invoked")

    def test_wake_outage_does_not_park_model(self) -> None:
        repo, env, _json_path, state_dir = self._stage_wake_repo(prs=[])
        harness = support.write(
            self.tmp / "outage_harness.sh",
            "#!/bin/sh\necho 'Error: 503 Endpoint is unavailable' >&2\nexit 1\n",
        )
        harness.chmod(0o755)
        support.script(
            repo,
            "fleet.py",
            "wake",
            "--harness-cmd",
            f"{harness} {{prompt_file}}",
            "--model",
            "test-provider/outage-model",
            env=env,
        )
        fail_file = state_dir / "model_failures"
        if fail_file.exists():
            self.assertNotIn("test-provider/outage-model", fail_file.read_text())

    def test_circuit_breaker_trips_after_three_failed_wakes_and_resets(self) -> None:
        repo, env, _json_path, state_dir = self._stage_wake_repo(prs=[])
        failing_harness = support.write(
            self.tmp / "fail_harness.sh",
            "#!/bin/sh\nexit 1\n",
        )
        failing_harness.chmod(0o755)
        for _ in range(3):
            support.script(
                repo,
                "fleet.py",
                "wake",
                "--harness-cmd",
                f"{failing_harness} {{prompt_file}}",
                env=env,
            )

        cb_file = state_dir / "CIRCUIT_BREAKER"
        self.assertTrue(cb_file.exists(), "circuit breaker must trip after 3 failed wakes")

        gov = support.script(repo, "fleet.py", "govern", "--json", env=env)
        self.assertEqual(gov.returncode, 1)
        self.assertIn("circuit breaker", gov.stdout.lower() + gov.stderr.lower())

        reset = support.script(repo, "fleet.py", "reset-circuit", env=env)
        self.assertEqual(reset.returncode, 0)
        self.assertFalse(cb_file.exists(), "reset-circuit must clear CIRCUIT_BREAKER")

    def test_honest_no_gap_does_not_trip_circuit_breaker(self) -> None:
        repo, env, _json_path, state_dir = self._stage_wake_repo(prs=[])
        for _ in range(3):
            support.script(repo, "fleet.py", "wake", env=env)

        cb_file = state_dir / "CIRCUIT_BREAKER"
        self.assertFalse(cb_file.exists(), "quiet wakes must not trip circuit breaker")



if __name__ == "__main__":
    unittest.main()
