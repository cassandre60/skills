from __future__ import annotations

import importlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from support import (
    ASSETS,
    PYTHON,
    SCRIPTS,
    SKILL_ROOT,
    commit,
    env_with_path,
    git,
    init_repo,
    install_scripts,
    ledger_text,
    load_example,
    path_with_only,
    push,
    python_repo,
    row,
    run,
    script,
    write,
    write_config,
)

sys.path.insert(0, str(SCRIPTS))
import common as common_module  # noqa: E402
import hashlib  # noqa: E402
import ledger as ledger_module  # noqa: E402
import mutants as mutants_module  # noqa: E402
import mutation_engine as engine_module  # noqa: E402


class RepoTestCase(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)


def _flat(text: str) -> str:
    """Collapse whitespace so an assertion survives a re-wrap."""
    return " ".join(text.split())


def _flat_lifecycle(text: str) -> str:
    """Collapse whitespace so an assertion survives a re-wrap."""
    return " ".join(text.split())


def _flat_git(text: str) -> str:
    """Collapse whitespace so an assertion survives a re-wrap."""
    return " ".join(text.split())


def _flat_ask(text: str) -> str:
    """Collapse whitespace so an assertion survives a re-wrap."""
    return " ".join(text.split())


def _flat_landing(text: str) -> str:
    """Collapse whitespace and drop markdown emphasis, so an assertion
    survives a re-wrap or a `**bold**` that moved."""
    return " ".join(text.replace("*", "").replace("`", "").split())


class StreamBChecklistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (SKILL_ROOT / "references" / "03-streams-and-triage.md").read_text(
            encoding="utf-8"
        )

    def test_the_harness_fidelity_question_exists(self) -> None:
        self.assertIn("measure what production measures", self.text)
        for detail in ("theme", "font"):
            self.assertIn(detail, self.text.lower())

    def test_the_swallowed_exception_question_exists(self) -> None:
        self.assertIn("framework errors", self.text)
        self.assertIn("swallow", self.text.lower())

    def test_the_fidelity_finding_shape_is_measured_not_asserted(self) -> None:
        """'Most of them are noise' is not a measurement."""
        self.assertIn("per-site", self.text)
        self.assertIn("in production", self.text)

    def test_the_report_before_failing_rule_is_stated(self) -> None:
        self.assertIn("report first", self.text.lower())


class GitRealityContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.moving = _flat_git(
            (SKILL_ROOT / "references" / "07-moving-main.md").read_text(encoding="utf-8")
        )
        self.protocol = _flat_git(
            (SKILL_ROOT / "references" / "04-workflow-and-roles.md").read_text(
                encoding="utf-8"
            )
        )
        self.unattended = _flat_git(
            (SKILL_ROOT / "references" / "05-unattended-operation.md").read_text(
                encoding="utf-8"
            )
        )

    def test_strict_protection_is_named(self) -> None:
        self.assertIn("strict branch protection", self.moving.lower())
        self.assertIn("--admin", self.moving)

    def test_the_resync_is_a_merge_not_a_rebase(self) -> None:
        self.assertIn("merge the base into the branch", self.moving.lower())
        self.assertIn("git merge origin/main", self.moving)
        self.assertIn("never by rebasing", self.protocol.lower())

    def test_branch_inheritance_is_named(self) -> None:
        self.assertIn("inherits that worktree's commits", self.protocol.lower())
        self.assertIn("git diff origin/main...HEAD --stat", self.protocol)

    def test_batched_deliveries_are_named(self) -> None:
        self.assertIn("Batch the fleet's own deliveries", self.protocol)
        self.assertIn("one commit per change", self.protocol)

    def test_the_owners_checkout_is_checked_at_cold_start(self) -> None:
        self.assertIn("orchestrator's own checkout", self.unattended.lower())
        self.assertIn("rev-parse --abbrev-ref HEAD", self.unattended)


class OwnerAskContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.protocol = _flat_ask(
            (SKILL_ROOT / "references" / "04-workflow-and-roles.md").read_text(
                encoding="utf-8"
            )
        )
        self.unattended = _flat_ask(
            (SKILL_ROOT / "references" / "05-unattended-operation.md").read_text(
                encoding="utf-8"
            )
        )

    def test_the_ask_states_three_plain_things(self) -> None:
        for phrase in (
            "what changes",
            "what it costs",
            "what happens if the owner declines",
        ):
            self.assertIn(phrase, self.protocol.lower(), phrase)

    def test_the_consent_record_has_its_four_fields(self) -> None:
        for field in ("class", "authority", "date", "withdrawal path"):
            self.assertIn(field, self.protocol.lower(), field)

    def test_consent_is_per_item(self) -> None:
        self.assertIn("consent is per item", self.protocol.lower())
        self.assertIn("standing consent", self.protocol.lower())

    def test_the_gh_fallbacks_are_documented(self) -> None:
        self.assertIn("gh api --method POST", self.unattended)
        self.assertIn("repos/<owner>/<repo>/issues/<n>/labels", self.unattended)

    def test_the_repo_override_rule_is_stated(self) -> None:
        self.assertIn("always pass", self.unattended.lower())
        self.assertIn("-r <owner>/<repo>", self.unattended.lower())
        self.assertIn("parent", self.unattended.lower())


class ConfigGuardTests(RepoTestCase):
    def make(self, with_origin: bool = False):
        repo, origin = python_repo(self.tmp, with_origin=with_origin)
        commit(repo)
        return repo, origin

    def test_empty_include_is_a_named_configuration_error(self) -> None:
        """An empty candidate list is 'not configured', not 'nothing to do'."""
        repo, _ = self.make()
        path = repo / "scripts" / "autonomous" / "config.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["candidates"]["A"]["include"] = []
        path.write_text(json.dumps(cfg), encoding="utf-8")
        proc = script(repo, "ledger.py", "next", "--stream", "A")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("not configured", proc.stderr)
        self.assertNotIn("No eligible targets remain", proc.stdout)

    def test_missing_config_is_named_too(self) -> None:
        """No config file at all: the message says which file to create."""
        repo, _ = self.make()
        (repo / "scripts" / "autonomous" / "config.json").unlink()
        proc = script(repo, "ledger.py", "next", "--stream", "A")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("no config file at", proc.stderr)

    def test_globs_that_match_nothing_are_reported_as_a_configuration_problem(self) -> None:
        """0 candidates matched != 0 eligible targets."""
        repo, _ = self.make()
        path = repo / "scripts" / "autonomous" / "config.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["candidates"]["A"]["include"] = ["nowhere/**/*.py"]
        path.write_text(json.dumps(cfg), encoding="utf-8")
        proc = script(repo, "ledger.py", "next", "--stream", "A")
        self.assertIn("No candidate path matched", proc.stdout)
        self.assertNotIn("No eligible targets remain", proc.stdout)

    def test_the_dart_example_config_resolves_the_companion(self) -> None:
        """The shipped example must map a source file to a test that exists."""
        repo, _ = self.make()
        # a repo laid out the way the Dart example describes
        write(repo / "lib" / "src" / "view" / "x.dart", "def f():\n    return 1\n")
        write(repo / "test" / "view" / "x_test.dart", "def test_x():\n    assert True\n")
        commit(repo)
        example = load_example("config.dart.json")
        write_config(repo, example)
        proc = script(repo, "ledger.py", "next", "--stream", "A", "--limit", "1")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("test/view/x_test.dart", proc.stdout)
        self.assertNotIn("No candidate path matched", proc.stdout)


class WorkerLifecycleContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.unattended = _flat(
            (SKILL_ROOT / "references" / "05-unattended-operation.md").read_text(
                encoding="utf-8"
            )
        )
        self.protocol = _flat(
            (SKILL_ROOT / "references" / "04-workflow-and-roles.md").read_text(
                encoding="utf-8"
            )
        )
        self.watchdog = (SKILL_ROOT / "assets" / "watchdog.sh").read_text(
            encoding="utf-8"
        )

    def test_three_failure_classes_are_named(self) -> None:
        self.assertIn("quota", self.unattended.lower())
        self.assertIn("outage", self.unattended.lower())
        self.assertIn("task failure", self.unattended.lower())

    def test_an_outage_does_not_rotate_or_park_a_pool(self) -> None:
        self.assertIn("do not rotate, do not park a pool", self.unattended.lower())
        self.assertIn("OUTAGE_RE", self.watchdog)
        # "overloaded" is an outage word, not a quota word
        self.assertNotIn("overloaded", _flat(self.watchdog).split("QUOTA_RE=")[1].split("'")[0])

    def test_a_dead_model_is_its_own_failure_class(self) -> None:
        # Verified 2026-10-23: `Model unavailable` matched neither the quota
        # nor the outage list, so the tick was logged "done" and the ladder
        # was never tried for seven hours.
        self.assertIn("dead model", self.unattended.lower())
        self.assertIn("DEAD_MODEL_RE", self.watchdog)

    def test_an_unclassified_non_zero_exit_still_rotates(self) -> None:
        # The classifier decides HOW to record a failure, never WHETHER it is
        # one: a new error string must not become a silent success.
        self.assertIn("exit status is the contract", self.unattended.lower())
        self.assertIn('if [ "$RC" -ne 0 ]', self.watchdog)

    def test_a_timeout_is_judged_by_the_heartbeat(self) -> None:
        # A preempted turn and a long turn look identical in the output; the
        # session's own heartbeat is the witness.
        self.assertIn("judged by the heartbeat", self.unattended.lower())
        self.assertIn('"$RC" -eq 124', self.watchdog)

    def test_exhaustion_leaves_an_out_of_band_alert(self) -> None:
        self.assertIn("alert.md", self.unattended.lower())
        self.assertIn("ALERT_FILE", self.watchdog)

    def test_the_ladder_is_owner_scoped(self) -> None:
        self.assertIn("ORCH_AUTHORIZED_PROVIDERS", self.unattended)
        self.assertIn("ORCH_AUTHORIZED_PROVIDERS", self.watchdog)
        self.assertIn("throwaway session", self.unattended.lower())

    def test_rotation_switches_the_session_model(self) -> None:
        self.assertIn("ORCH_SWITCH_SESSION_MODEL", self.unattended)
        self.assertIn("switch_session_model", self.watchdog)

    def test_the_install_is_checked_for_drift(self) -> None:
        self.assertIn("installing is a copy", self.unattended.lower())
        installer = (ASSETS / "install-watchdog.sh").read_text(encoding="utf-8")
        self.assertIn("--check", installer)

    def test_an_exhausted_tick_does_not_flood_the_session(self) -> None:
        # Each attempt delivers its message before the model call fails, so a
        # dead ladder at a 20-minute timer would queue four questions a tick.
        self.assertIn("must not flood the session", self.unattended.lower())
        self.assertIn("ORCH_RETRY_AFTER_EXHAUSTION", self.watchdog)

    def test_a_healthy_session_model_is_not_overridden(self) -> None:
        # The ladder is tried before the session default, so a blind switch
        # would move a session whose model is already a live ladder entry.
        self.assertIn(
            "never override a model that is already working",
            self.unattended.lower(),
        )
        self.assertIn("current_session_model", self.watchdog)

    def test_check_reports_a_unit_that_cannot_see_the_env_file(self):
        # A hand-written unit without EnvironmentFile= runs with every default
        # (an empty ladder included) even when the script bytes match the
        # asset. Verified 2026-10-23 on a fleet whose unit only set
        # ORCH_STATE_DIR: the merged rotation fix could never fire.
        if not shutil.which("bash") or not shutil.which("install"):
            self.skipTest("bash/install not available")
        home = Path(tempfile.mkdtemp(prefix="unitcheck-"))
        self.addCleanup(shutil.rmtree, home, True)
        install_dir = home / "install"
        state_dir = home / "state"
        unit_dir = home / ".config/systemd/user"
        install_dir.mkdir(parents=True)
        state_dir.mkdir(parents=True)
        unit_dir.mkdir(parents=True)
        for name in ("watchdog.sh", "snapshot.sh"):
            shutil.copy2(ASSETS / name, install_dir / name)
        unit = unit_dir / "orchestrator-watchdog.service"
        write(unit, "[Service]\nType=oneshot\nExecStart=%h/install/watchdog.sh\n")
        env = dict(os.environ)
        env.update(
            HOME=str(home),
            ORCH_INSTALL_DIR=str(install_dir),
            ORCH_STATE_DIR=str(state_dir),
        )
        env.pop("XDG_CONFIG_HOME", None)
        result = run(
            [str(ASSETS / "install-watchdog.sh"), "--check"], cwd=home, env=env
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("no EnvironmentFile=", result.stdout + result.stderr)
        write(
            unit,
            "[Service]\nEnvironmentFile=-%h/state/watchdog.env\n"
            "ExecStart=%h/install/watchdog.sh\n",
        )
        write(state_dir / "watchdog.env", "ORCH_MODEL_LADDER=\n")
        result = run(
            [str(ASSETS / "install-watchdog.sh"), "--check"], cwd=home, env=env
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_unit_must_load_the_env_file(self) -> None:
        # The env file is inert until the unit references it; a default
        # ORCH_MODEL_LADDER is empty, so rotation silently cannot fire.
        self.assertIn(
            "installed copy is not yet a running configuration",
            self.unattended.lower(),
        )
        installer = (ASSETS / "install-watchdog.sh").read_text(encoding="utf-8")
        self.assertIn("has no EnvironmentFile=", installer)

    def test_the_checkpoint_covers_new_files(self) -> None:
        self.assertIn("stage new files by name", self.unattended.lower())

    def test_a_dead_tabs_untracked_work_is_preserved(self) -> None:
        self.assertIn("dirty and untracked", self.unattended.lower())
        self.assertIn("before touching its worktree", self.unattended.lower())

    def test_the_dispatch_brief_carries_the_precheck(self) -> None:
        self.assertIn("ledger.py check --target", self.unattended)
        self.assertIn("correcting the brief's premise", self.unattended.lower())
        self.assertIn("keeps the dispatch brief short", self.protocol.lower())


class ValidateTests(RepoTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo, _ = python_repo(self.tmp)
        commit(self.repo)

    def _validate(self, *extra):
        return script(self.repo, "ledger.py", "validate", *extra)

    def test_decorated_status_makes_a_row_invisible(self) -> None:
        """A Status cell with extra text is skipped by the parser - report it."""
        ledger = self.repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md"
        write(
            ledger,
            ledger_text(
                "| 2026-01-01 | A (Mutation) | `src/app.py` | `abc123456` | "
                "COMPLETED (second round: 98 candidates) | #1 | did the work | trigger |",
                row("src/app.py", "abc123456"),
            ),
        )
        proc = self._validate()
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("invisible", proc.stdout)
        self.assertIn("COMPLETED (second round", proc.stdout)

    def test_compound_target_and_hash_cells_are_invisible(self) -> None:
        ledger = self.repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md"
        write(
            ledger,
            ledger_text(
                "| 2026-01-01 | A (Mutation) | `src/app.py`, `tests/test_app.py` | "
                "`abc123456`, `def789012` | COMPLETED | #1 | did the work | trigger |",
            ),
        )
        proc = self._validate()
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("more than one path", proc.stdout)
        self.assertIn("more than one hash", proc.stdout)

    def test_stale_hash_is_reported(self) -> None:
        """A row whose hash is not the blob at HEAD is a stale audit."""
        ledger = self.repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md"
        write(ledger, ledger_text(row("src/app.py", "000000000")))
        proc = self._validate()
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("stale", proc.stdout)

    def test_a_well_formed_ledger_validates_clean(self) -> None:
        ledger = self.repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md"
        source = script(self.repo, "ledger.py", "check", "--target", "src/app.py")
        self.assertNotEqual(source.returncode, 0)
        # record a proper row, then validate
        recorded = script(
            self.repo,
            "ledger.py",
            "record",
            "--stream",
            "A",
            "--target",
            "src/app.py",
            "--status",
            "SOUND",
            "--summary",
            "no gaps",
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        proc = self._validate()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("every row is visible", proc.stdout)

    def test_json_output_lists_the_problems(self) -> None:
        ledger = self.repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md"
        write(
            ledger,
            ledger_text(
                "| 2026-01-01 | A (Mutation) | `src/app.py` | `abc123456` | "
                "COMPLETED (with a parenthetical) | #1 | did the work | trigger |",
            ),
        )
        proc = self._validate("--json")
        self.assertNotEqual(proc.returncode, 0)
        report = json.loads(proc.stdout)
        self.assertEqual(report["rows_total"], 1)
        self.assertEqual(report["rows_visible"], 0)
        self.assertEqual(len(report["invisible"]), 1)

    def test_recorded_rows_are_visible(self) -> None:
        """A row the tool writes must be a row the parser can see."""
        recorded = script(
            self.repo,
            "ledger.py",
            "record",
            "--stream",
            "A",
            "--target",
            "src/app.py",
            "--status",
            "SOUND",
            "--summary",
            "no gaps",
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        proc = self._validate("--json")
        report = json.loads(proc.stdout)
        self.assertEqual(report["rows_total"], 1)
        self.assertEqual(report["rows_visible"], 1)
        self.assertEqual(report["invisible"], [])


class LedgerTests(RepoTestCase):
    def make(self, with_origin: bool = False):
        repo, origin = python_repo(self.tmp, with_origin=with_origin)
        commit(repo)
        return repo, origin

    def record(self, repo: Path, *args: str):
        return script(repo, "ledger.py", "record", *args)

    def test_reaudit_newest_wins(self):
        repo, _ = self.make()
        first = self.record(
            repo, "--stream", "A", "--target", "src/app.py",
            "--status", "SOUND", "--summary", "first",
        )
        self.assertEqual(first.returncode, 0, first.stderr)

        write(repo / "src" / "app.py", "def f(a, b):\n    return a != b\n")
        commit(repo, "change")
        second = self.record(
            repo, "--stream", "A", "--target", "src/app.py",
            "--status", "COMPLETED", "--pr", "#2", "--summary", "second",
        )
        self.assertEqual(second.returncode, 0, second.stderr)

        rows = ledger_module.parse_ledger(repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md")
        current = git(repo, "rev-parse", "HEAD:src/app.py").stdout.strip()[:9]
        authoritative = ledger_module.authoritative_rows(
            repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md"
        )["src/app.py"]
        self.assertTrue(authoritative.content_hash.startswith(current))
        self.assertEqual(authoritative.summary, "second")
        self.assertEqual(len(rows), 2)

        check = script(repo, "ledger.py", "check", "--target", "src/app.py")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        nxt = script(repo, "ledger.py", "next", "--stream", "A", "--json")
        data = json.loads(nxt.stdout)
        self.assertNotIn("src/app.py", [item["target"] for item in data["candidates"]])

    def test_pipe_roundtrip(self):
        repo, _ = self.make()
        summary = "swapped a || b to a && b: 1 gap | path \\ end"
        result = self.record(
            repo, "--stream", "A", "--target", "src/app.py",
            "--status", "SOUND", "--summary", summary,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md").read_text()
        data_rows = [line for line in text.splitlines() if "swapped" in line]
        self.assertEqual(len(data_rows), 1)
        cells = ledger_module.split_row(data_rows[0])
        self.assertEqual(len(cells), 8)
        self.assertEqual(cells[6], summary)

    def test_separator_variants(self):
        separators = [
            "|---|---|---|---|---|---|---|---|",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
            "| :--- | ---: | :---: | --- | --- | --- | --- | --- |",
        ]
        for index, separator in enumerate(separators):
            with self.subTest(separator=separator):
                repo, _ = python_repo(self.tmp, name=f"repo{index}")
                write(
                    repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md",
                    "# Ledger\n\n| Date | Stream | Target File / Module | Content Hash | "
                    "Status | PR / Issue | Summary / Outcome | Re-Audit Trigger |\n"
                    + separator + "\n",
                )
                commit(repo)
                result = self.record(
                    repo, "--stream", "A", "--target", "src/app.py",
                    "--status", "SOUND", "--summary", "ok",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("src/app.py", (repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md").read_text())

    def test_sound_without_pr_and_completed_requires_pr(self):
        repo, _ = self.make()
        sound = self.record(
            repo, "--stream", "A", "--target", "src/app.py",
            "--status", "SOUND", "--summary", "no gaps",
        )
        self.assertEqual(sound.returncode, 0, sound.stderr)
        completed = self.record(
            repo, "--stream", "A", "--target", "tests/test_app.py",
            "--status", "COMPLETED", "--summary", "fixed",
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("--pr is required", completed.stderr)

    def test_test_file_change_invalidates(self):
        repo, _ = self.make()
        result = self.record(
            repo, "--stream", "A", "--target", "src/app.py",
            "--status", "SOUND", "--summary", "ok",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        write(repo / "tests" / "test_app.py", "# weakened\n")
        commit(repo, "weaken test")
        nxt = script(repo, "ledger.py", "next", "--stream", "A", "--json")
        targets = [item["target"] for item in json.loads(nxt.stdout)["candidates"]]
        self.assertIn("src/app.py", targets)

    def test_legacy_hash_matches_source(self):
        repo, _ = self.make()
        source_hash = git(repo, "rev-parse", "HEAD:src/app.py").stdout.strip()[:9]
        write(
            repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md",
            ledger_text(row("src/app.py", source_hash, status="SOUND")),
        )
        commit(repo)
        check = script(repo, "ledger.py", "check", "--target", "src/app.py")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertIn("legacy", check.stdout)
        nxt = script(repo, "ledger.py", "next", "--stream", "A", "--json")
        self.assertNotIn("src/app.py", [item["target"] for item in json.loads(nxt.stdout)["candidates"]])

    def test_changed_source_invalidates_legacy_hash(self):
        repo, _ = self.make()
        source_hash = git(repo, "rev-parse", "HEAD:src/app.py").stdout.strip()[:9]
        write(
            repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md",
            ledger_text(row("src/app.py", source_hash, status="SOUND")),
        )
        commit(repo)
        write(repo / "src" / "app.py", "def f(a, b):\n    return a != b\n")
        commit(repo, "change")
        nxt = script(repo, "ledger.py", "next", "--stream", "A", "--json")
        self.assertEqual(nxt.returncode, 0, nxt.stderr)
        targets = [item["target"] for item in json.loads(nxt.stdout)["candidates"]]
        self.assertIn("src/app.py", targets)

    def test_next_stream_b_c_and_alias(self):
        repo, _ = self.make()
        stream_b = script(repo, "ledger.py", "next", "--stream", "B", "--json")
        self.assertEqual(stream_b.returncode, 0, stream_b.stderr)
        self.assertIn("tests/test_app.py", [item["target"] for item in json.loads(stream_b.stdout)["candidates"]])
        stream_a = script(repo, "ledger.py", "next", "--stream", "A", "--json")
        alias = script(repo, "ledger.py", "next", "--stream", "1", "--json")
        self.assertEqual(json.loads(stream_a.stdout)["candidates"], json.loads(alias.stdout)["candidates"])
        # C is available since issue #25; D is still a planned letter.
        planned = script(repo, "ledger.py", "next", "--stream", "D")
        self.assertEqual(planned.returncode, 2)
        self.assertIn("planned, not implemented", planned.stderr)

    def test_rejected_target_is_suppressed_until_content_changes(self):
        repo, _ = self.make()

        before = script(repo, "ledger.py", "next", "--stream", "A", "--json")
        self.assertEqual(before.returncode, 0, before.stderr)
        self.assertEqual(
            [item["target"] for item in json.loads(before.stdout)["candidates"]],
            ["src/app.py"],
        )

        recorded = self.record(
            repo,
            "--stream", "A",
            "--target", "src/app.py",
            "--status", "REJECTED",
            "--pr", "#9",
            "--summary", "owner closed the PR as out of scope",
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        self.assertIn("REJECTED", (repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md").read_text())

        suppressed = script(repo, "ledger.py", "next", "--stream", "A", "--json")
        self.assertEqual(suppressed.returncode, 0, suppressed.stderr)
        payload = json.loads(suppressed.stdout)
        self.assertEqual(payload["candidates"], [])
        skipped = payload["skipped"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["disposition"], "REJECTED")
        self.assertIn("owner closed the PR as out of scope", skipped[0]["skip"])

        # A content change invalidates the disposition, same as a stale audit.
        write(repo / "src" / "app.py", "def f(a, b):\n    return a != b\n")
        commit(repo, "change")
        reopened = script(repo, "ledger.py", "next", "--stream", "A", "--json")
        self.assertEqual(
            [item["target"] for item in json.loads(reopened.stdout)["candidates"]],
            ["src/app.py"],
        )

    def test_rejected_duplicate_requires_force(self):
        repo, _ = self.make()
        args = (
            "--stream", "A", "--target", "src/app.py",
            "--status", "WONTFIX", "--summary", "not worth it",
        )
        first = self.record(repo, *args)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second = self.record(repo, *args)
        self.assertEqual(second.returncode, 1, second.stdout + second.stderr)
        self.assertIn("already has a WONTFIX row", second.stderr)

    def test_sync_rejections_remembers_closed_unmerged_prs_only(self):
        repo, _ = self.make()
        payload = [
            {
                "number": 7,
                "title": "test: harden app",
                "state": "CLOSED",
                "mergedAt": None,
                "files": [{"path": "src/app.py"}],
                "labels": [],
            },
            {
                "number": 8,
                "title": "test: landed change",
                "state": "MERGED",
                "mergedAt": "2026-10-20T00:00:00Z",
                "files": [{"path": "src/app.py"}],
                "labels": [],
            },
        ]
        json_path = self.tmp / "closed.json"
        json_path.write_text(json.dumps(payload) + "\n")
        bin_dir = path_with_only(self.tmp, ("git",))
        gh = write(
            bin_dir / "gh",
            "#!/bin/sh\nwhile IFS= read -r line; do printf '%s\\n' \"$line\"; done < \"$GH_JSON\"\n",
        )
        gh.chmod(0o755)
        env = env_with_path(bin_dir, GH_JSON=str(json_path))

        synced = script(repo, "ledger.py", "sync-rejections", "--json", env=env)
        self.assertEqual(synced.returncode, 0, synced.stdout + synced.stderr)
        recorded = json.loads(synced.stdout)["recorded"]
        self.assertEqual([item["target"] for item in recorded], ["src/app.py"])
        self.assertEqual(recorded[0]["pr"], "#7")

        ledger_path = repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md"
        text = ledger_path.read_text()
        self.assertIn("REJECTED", text)
        self.assertIn("#7", text)
        self.assertNotIn("#8", text)

        # Idempotent: a second sync at the same content hash adds nothing.
        again = script(repo, "ledger.py", "sync-rejections", "--json", env=env)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(json.loads(again.stdout)["recorded"], [])
        self.assertEqual(ledger_path.read_text(), text)

        # And the remembered rejection actually hides the target.
        nxt = script(repo, "ledger.py", "next", "--stream", "A", "--json", env=env)
        self.assertEqual(json.loads(nxt.stdout)["candidates"], [])

    def test_next_without_gh(self):
        repo, _ = self.make()
        bin_dir = path_with_only(self.tmp, ("git",))
        result = script(
            repo, "ledger.py", "next", "--stream", "A",
            env=env_with_path(bin_dir),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gh", (result.stdout + result.stderr).lower())
        self.assertIn("warning", (result.stdout + result.stderr).lower())

    def test_require_gh_rejects_bad_payload(self):
        repo, _ = self.make()
        bin_dir = path_with_only(self.tmp, ("git",))
        gh = write(bin_dir / "gh", "#!/bin/sh\nprintf '{}\\n'\n")
        gh.chmod(0o755)
        result = script(
            repo, "ledger.py", "next", "--stream", "A", "--require-gh",
            env=env_with_path(bin_dir),
        )
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("unexpected payload", result.stderr)

    def test_pr_overlap_exact_path_and_branch_fallback(self):
        repo, _ = init_repo(self.tmp, with_origin=False)
        install_scripts(repo)
        cfg = load_example()
        cfg["candidates"]["A"] = {"include": ["lib/**/*.dart"], "exclude": [], "require_test": False}
        cfg["test_for_source"] = []
        write_config(repo, cfg)
        write(repo / "lib" / "a" / "chess_fsrs_scheduler.dart", "bool a(int x) => x == 1;\n")
        write(repo / "lib" / "b" / "scheduler.dart", "bool b(int x) => x == 1;\n")
        write(repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md", ledger_text())
        commit(repo)

        payload = [
            {
                "number": 123,
                "title": "test: harden chess_fsrs_scheduler",
                "headRefName": "test/chess-fsrs-scheduler",
                "files": [{"path": "lib/a/chess_fsrs_scheduler.dart"}],
            }
        ]
        json_path = self.tmp / "prs.json"
        json_path.write_text(json.dumps(payload) + "\n")
        bin_dir = path_with_only(self.tmp, ("git",))
        gh = write(
            bin_dir / "gh",
            "#!/bin/sh\nwhile IFS= read -r line; do printf '%s\\n' \"$line\"; done < \"$GH_JSON\"\n",
        )
        gh.chmod(0o755)
        env = env_with_path(bin_dir, GH_JSON=str(json_path))

        result = script(repo, "ledger.py", "next", "--stream", "A", "--json", "--limit", "5", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        targets = [item["target"] for item in json.loads(result.stdout)["candidates"]]
        self.assertEqual(targets, ["lib/b/scheduler.dart"])

        fallback = [{key: value for key, value in payload[0].items() if key != "files"}]
        json_path.write_text(json.dumps(fallback) + "\n")
        result = script(repo, "ledger.py", "next", "--stream", "A", "--json", "--limit", "5", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        targets = [item["target"] for item in json.loads(result.stdout)["candidates"]]
        self.assertEqual(targets, ["lib/b/scheduler.dart"])

    def test_subdir_invocation(self):
        repo, _ = self.make()
        root = script(repo, "ledger.py", "next", "--stream", "A", "--json")
        path = repo / "scripts" / "autonomous" / "ledger.py"
        subdir = run([PYTHON, str(path), "next", "--stream", "A", "--json"], cwd=repo / "src")
        self.assertEqual(subdir.returncode, 0, subdir.stderr)
        self.assertEqual(json.loads(root.stdout)["candidates"], json.loads(subdir.stdout)["candidates"])

        recorded = self.record(
            repo, "--stream", "A", "--target", "src/app.py",
            "--status", "SOUND", "--summary", "ok",
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        checked = run(
            [PYTHON, str(path), "check", "--target", "src/app.py"],
            cwd=repo / "src",
        )
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_union_merge_two_branches(self):
        repo, origin = python_repo(self.tmp, with_origin=True)
        write(repo / ".gitattributes", "docs/AUTONOMOUS_WORK_LEDGER.md merge=union\n")
        write(repo / "src" / "other.py", "def g(a, b):\n    return a != b\n")
        write(repo / "tests" / "test_other.py", "from src.other import g\n")
        commit(repo)
        push(repo)

        git(repo, "checkout", "-q", "-b", "one")
        first = self.record(repo, "--stream", "A", "--target", "src/app.py", "--status", "SOUND", "--summary", "row-one")
        self.assertEqual(first.returncode, 0, first.stderr)
        commit(repo, "one")

        git(repo, "checkout", "-q", "main")
        git(repo, "checkout", "-q", "-b", "two")
        second = self.record(repo, "--stream", "A", "--target", "src/other.py", "--status", "SOUND", "--summary", "row-two")
        self.assertEqual(second.returncode, 0, second.stderr)
        commit(repo, "two")

        git(repo, "checkout", "-q", "main")
        merge_one = git(repo, "merge", "--no-edit", "one")
        self.assertEqual(merge_one.returncode, 0, merge_one.stderr)
        merge_two = git(repo, "merge", "--no-edit", "two")
        self.assertEqual(merge_two.returncode, 0, merge_two.stderr)
        text = (repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md").read_text()
        self.assertIn("row-one", text)
        self.assertIn("row-two", text)
        self.assertNotIn("<<<<<<<", text)

    def test_record_rejects_duplicates_and_missing_targets(self):
        repo, _ = self.make()
        missing = self.record(
            repo, "--stream", "A", "--target", "src/nope.py",
            "--status", "SOUND", "--summary", "x",
        )
        self.assertEqual(missing.returncode, 1)
        first = self.record(
            repo, "--stream", "A", "--target", "src/app.py",
            "--status", "SOUND", "--summary", "first",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        duplicate = self.record(
            repo, "--stream", "A", "--target", "src/app.py",
            "--status", "SOUND", "--summary", "again",
        )
        self.assertEqual(duplicate.returncode, 1)
        forced = self.record(
            repo, "--stream", "A", "--target", "src/app.py",
            "--status", "SOUND", "--summary", "again", "--force",
        )
        self.assertEqual(forced.returncode, 0, forced.stderr)

    def test_template_example_is_ignored(self):
        repo, _ = self.make()
        import shutil

        shutil.copy(ASSETS / "AUTONOMOUS_WORK_LEDGER.template.md", repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md")
        rows = ledger_module.parse_ledger(repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md")
        self.assertEqual([item.status for item in rows], ["EXAMPLE"])
        self.assertEqual(ledger_module.authoritative_rows(repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md"), {})
        nxt = script(repo, "ledger.py", "next", "--stream", "A", "--json")
        self.assertIn("src/app.py", [item["target"] for item in json.loads(nxt.stdout)["candidates"]])

    def test_record_dirty_warns_and_strict_fails(self):
        repo, _ = self.make()
        write(repo / "src" / "app.py", "def f(a, b):\n    return a == b\n# dirty\n")
        warned = self.record(
            repo, "--stream", "A", "--target", "src/app.py",
            "--status", "SOUND", "--summary", "dirty",
        )
        self.assertEqual(warned.returncode, 0, warned.stderr)
        self.assertIn("working tree differs", warned.stderr)
        strict = self.record(
            repo, "--stream", "A", "--target", "src/app.py",
            "--status", "SOUND", "--summary", "strict", "--strict",
        )
        self.assertEqual(strict.returncode, 1)
        self.assertIn("--strict", strict.stderr)

    def test_check_not_audited(self):
        repo, _ = self.make()
        result = script(repo, "ledger.py", "check", "--target", "src/app.py")
        self.assertEqual(result.returncode, 1)


class RiskRankingTests(RepoTestCase):
    """Issue #15: discovery order is risk, not alphabet."""

    def _repo(self) -> Path:
        repo, _ = python_repo(self.tmp)
        write(repo / "src" / "a_stable.py", "def a(x):\n    return x\n")
        write(repo / "src" / "z_hot.py", "def z(x):\n    return x\n")
        write(repo / "tests" / "test_a_stable.py", "def test_a():\n    assert True\n")
        write(repo / "tests" / "test_z_hot.py", "def test_z():\n    assert True\n")
        commit(repo, "base")
        for i in range(5):
            write(
                repo / "src" / "z_hot.py",
                "def z(x):\n    return x\n" + "# touch\n" * (i + 1),
            )
            commit(repo, f"churn z_hot {i}")
        return repo

    def _next(self, repo: Path, *extra: str, env=None):
        result = script(
            repo, "ledger.py", "next", "--stream", "A", "--json", "--limit", "5",
            *extra, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout), result

    def test_risk_rank_prefers_hot_file_over_alphabet(self):
        repo = self._repo()
        bin_dir = path_with_only(self.tmp, ("git",))
        env = env_with_path(bin_dir)
        lex, _ = self._next(repo, env=env)
        self.assertEqual(lex["candidates"][0]["target"], "src/a_stable.py")
        risk, first = self._next(repo, "--rank", "risk", env=env)
        top = risk["candidates"][0]
        self.assertEqual(top["target"], "src/z_hot.py")
        self.assertTrue(
            any("touched-in-" in why for why in top["rank"]["why"]), top["rank"]
        )
        # Deterministic for a fixed repo state.
        _, second = self._next(repo, "--rank", "risk", env=env)
        self.assertEqual(first.stdout, second.stdout)

    def test_issue_intake_outranks_higher_score(self):
        repo = self._repo()
        prs = self.tmp / "prs.json"
        prs.write_text("[]\n")
        issues = self.tmp / "issues.json"
        issues.write_text(json.dumps([
            {"number": 88, "title": "crash in src/a_stable.py on startup", "body": ""}
        ]) + "\n")
        bin_dir = path_with_only(self.tmp, ("git",))
        gh = write(
            bin_dir / "gh",
            "#!/bin/sh\n"
            'case "$1 $2" in\n'
            f'  "pr list") while IFS= read -r l; do printf \'%s\\n\' "$l"; done < {prs} ;;\n'
            f'  "issue list") while IFS= read -r l; do printf \'%s\\n\' "$l"; done < {issues} ;;\n'
            "  *) echo unexpected gh args >&2; exit 1 ;;\n"
            "esac\n",
        )
        gh.chmod(0o755)
        data, _ = self._next(repo, "--rank", "risk", env=env_with_path(bin_dir))
        top = data["candidates"][0]
        self.assertEqual(top["target"], "src/a_stable.py")
        self.assertIn("mentioned-in-issue-#88", top["rank"]["why"])


class MutantLedgerTests(RepoTestCase):
    """Per-mutant memory: survivors stay visible until killed or triaged."""

    SOURCE = "def f(a, b):\n    x = a + b\n    y = a - b\n    return x == y\n"

    def make(self):
        repo, _ = python_repo(self.tmp)
        write(repo / "src" / "app.py", self.SOURCE)
        write(
            repo / "docs" / "AUTONOMOUS_MUTANTS.md",
            "# Autonomous Mutant Ledger\n\n"
            "| Date | Mutant ID | Source Hash | Disposition | PR / Issue | Reason |\n"
            "|---|---|---|---|---|---|\n",
        )
        commit(repo)
        return repo

    def survivors(self):
        return [
            {
                "file": "src/app.py",
                "line": 2,
                "column": 8,
                "original": "    x = a + b",
                "mutated": "    x = a - b",
                "description": "arithmetic operator + -> -",
            },
            {
                "file": "src/app.py",
                "line": 3,
                "column": 8,
                "original": "    y = a - b",
                "mutated": "    y = a + b",
                "description": "arithmetic operator - -> +",
            },
            {
                "file": "src/app.py",
                "line": 4,
                "column": 11,
                "original": "    return x == y",
                "mutated": "    return x != y",
                "description": "comparison == -> !=",
            },
        ]

    def report(self, repo):
        payload = {
            "source": "src/app.py",
            "language": "python",
            "test_cmd": "python3 -m pytest",
            "survived": self.survivors(),
        }
        path = repo / "run.json"
        path.write_text(json.dumps(payload) + "\n")
        return path

    def ids(self):
        return [
            mutants_module.mutant_id(item["file"], item["line"], item["mutated"])
            for item in self.survivors()
        ]

    def ingest(self, repo, *args):
        return script(repo, "mutants.py", "ingest", "--report", "run.json", *args)

    def record(self, repo, mutant_id, disposition, *args):
        return script(
            repo,
            "mutants.py",
            "record",
            "--id",
            mutant_id,
            "--disposition",
            disposition,
            "--reason",
            "verified while triaging",
            *args,
        )

    def test_ingest_is_idempotent_and_missing_ledger_is_ignored(self):
        repo = self.make()
        self.report(repo)

        first = self.ingest(repo, "--json")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        payload = json.loads(first.stdout)
        self.assertEqual([item["id"] for item in payload["opened"]], self.ids())
        self.assertEqual(payload["already_open"], 0)

        again = self.ingest(repo, "--json")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        payload = json.loads(again.stdout)
        self.assertEqual(payload["opened"], [])
        self.assertEqual(payload["already_open"], 3)

        rows = mutants_module.parse_mutants(repo / "docs" / "AUTONOMOUS_MUTANTS.md")
        self.assertEqual([row.disposition for row in rows], ["OPEN", "OPEN", "OPEN"])

        # A repo that never adopts the mutant ledger keeps exactly the old
        # behavior: no file, no open mutants, no error.
        (repo / "docs" / "AUTONOMOUS_MUTANTS.md").unlink()
        nxt = script(repo, "ledger.py", "next", "--stream", "A", "--json")
        self.assertEqual(nxt.returncode, 0, nxt.stderr)

    def test_open_survives_stale_hash_and_terminal_triage_is_remembered(self):
        repo = self.make()
        self.report(repo)
        first, second, third = self.ids()
        self.assertEqual(self.ingest(repo).returncode, 0)

        triaged = self.record(repo, second, "EQUIVALENT")
        self.assertEqual(triaged.returncode, 0, triaged.stdout + triaged.stderr)

        opened = script(repo, "mutants.py", "open", "--json")
        self.assertEqual(opened.returncode, 0, opened.stderr)
        payload = json.loads(opened.stdout)
        self.assertEqual(payload["total"], 2)
        self.assertEqual(
            [item["id"] for item in payload["files"][0]["open"]], [first, third]
        )
        self.assertEqual(script(repo, "mutants.py", "check", "--id", second).returncode, 0)

        # Change line 1 only: the file hash moves, the mutant lines do not.
        write(repo / "src" / "app.py", self.SOURCE.replace("def f(", "def g(", 1))
        commit(repo, "unrelated change")

        rerun = self.ingest(repo, "--json")
        self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
        payload = json.loads(rerun.stdout)
        self.assertEqual(payload["already_triaged"], 1)
        self.assertEqual([item["id"] for item in payload["opened"]], [first, third])

        # Kill the rest and the file has no active survivors left.
        for mutant_id in (first, third):
            killed = self.record(repo, mutant_id, "KILLED-BY", "--pr", "#42")
            self.assertEqual(killed.returncode, 0, killed.stdout + killed.stderr)
        opened = script(repo, "mutants.py", "open", "--json")
        self.assertEqual(json.loads(opened.stdout)["total"], 0)
        for mutant_id in self.ids():
            self.assertEqual(script(repo, "mutants.py", "check", "--id", mutant_id).returncode, 0)

    def test_next_resurfaces_a_partially_audited_file(self):
        repo = self.make()
        self.report(repo)
        self.assertEqual(self.ingest(repo).returncode, 0)

        # Fix one of the three survivors in this PR, then record the file audit.
        first, second, third = self.ids()
        killed = self.record(repo, first, "KILLED-BY", "--pr", "#41")
        self.assertEqual(killed.returncode, 0, killed.stdout + killed.stderr)
        audited = script(
            repo, "ledger.py", "record", "--stream", "A", "--target", "src/app.py",
            "--status", "COMPLETED", "--pr", "#41", "--summary", "fixed one survivor",
        )
        self.assertEqual(audited.returncode, 0, audited.stdout + audited.stderr)

        nxt = script(repo, "ledger.py", "next", "--stream", "A", "--json")
        self.assertEqual(nxt.returncode, 0, nxt.stderr)
        candidates = json.loads(nxt.stdout)["candidates"]
        self.assertEqual([item["target"] for item in candidates], ["src/app.py"])
        self.assertEqual(candidates[0]["audit_state"], "partial")
        self.assertEqual(candidates[0]["open_mutants"], 2)

        checked = script(repo, "ledger.py", "check", "--target", "src/app.py")
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn("open mutant(s)", checked.stdout)

        for mutant_id in (second, third):
            triaged = self.record(repo, mutant_id, "NOISE")
            self.assertEqual(triaged.returncode, 0, triaged.stdout + triaged.stderr)

        nxt = script(repo, "ledger.py", "next", "--stream", "A", "--json")
        payload = json.loads(nxt.stdout)
        self.assertEqual(payload["candidates"], [])
        skipped = [item for item in payload["skipped"] if item["target"] == "src/app.py"]
        self.assertEqual(skipped[0]["skip"], "already audited at current content hash")

    def test_record_validates_id_and_requires_evidence(self):
        repo = self.make()
        bogus = script(
            repo, "mutants.py", "record",
            "--id", "not-a-mutant", "--disposition", "NOISE", "--reason", "nope",
        )
        self.assertEqual(bogus.returncode, 2)
        self.assertIn("mutant id must look like", bogus.stderr)

        missing_reason = script(
            repo, "mutants.py", "record",
            "--id", self.ids()[0], "--disposition", "NOISE",
        )
        self.assertEqual(missing_reason.returncode, 2)
        self.assertIn("--reason", missing_reason.stderr)


class OrchestratorTests(RepoTestCase):
    def make_origin_repo(self):
        repo, origin = python_repo(self.tmp, with_origin=True)
        commit(repo)
        push(repo)
        return repo, origin

    def create(self, repo: Path, slug: str = "task", extra=()):
        return script(repo, "orchestrator.py", "create-worktree", "--slug", slug, *extra)

    def cleanup(self, repo: Path, slug: str = "task", extra=()):
        return script(repo, "orchestrator.py", "cleanup-worktree", "--slug", slug, *extra)

    def test_origin_untouched_on_success_and_failure(self):
        repo, origin = self.make_origin_repo()
        before = git(repo, "remote", "get-url", "origin").stdout.strip()
        created = self.create(repo, "origin-ok")
        self.assertEqual(created.returncode, 0, created.stderr)
        self.assertEqual(git(repo, "remote", "get-url", "origin").stdout.strip(), before)
        self.assertEqual(self.cleanup(repo, "origin-ok").returncode, 0)

        failed = self.create(repo, "origin-fail", ("--base", "does-not-exist"))
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(git(repo, "remote", "get-url", "origin").stdout.strip(), before)

    def test_no_project_strings(self):
        repo, _ = self.make_origin_repo()
        result = self.create(repo, "generic")
        self.assertEqual(result.returncode, 0, result.stderr)
        combined = (result.stdout + result.stderr).lower()
        for forbidden in ("chesssrs", "cassandre", "fvm", "build_runner"):
            self.assertNotIn(forbidden, combined)
        self.cleanup(repo, "generic")

    def test_slug_validation(self):
        repo, _ = self.make_origin_repo()
        for slug in ("a;rm -rf /", "Has Space", ""):
            with self.subTest(slug=slug):
                result = self.create(repo, slug)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_filename_with_space_passed_as_one_argv(self):
        repo, _ = self.make_origin_repo()
        write(repo / "src" / "my file.py", "def f(a, b):\n    return a == b\n")
        args_file = self.tmp / "analyze-args.json"
        stub = write(
            self.tmp / "analyze_stub.py",
            "import json, pathlib, sys\n"
            f"pathlib.Path({str(args_file)!r}).write_text(json.dumps(sys.argv[1:]))\n",
        )
        cfg = load_example()
        cfg["analyze_cmd"] = [PYTHON, str(stub)]
        cfg["analyze_extensions"] = [".py"]
        cfg["format_check_cmd"] = None
        cfg["gate_cmd"] = None
        write_config(repo, cfg)
        commit(repo)
        push(repo)

        created = self.create(repo, "space-file")
        self.assertEqual(created.returncode, 0, created.stderr)
        worktree = repo.parent / f"{repo.name}-space-file"
        write(worktree / "src" / "my file.py", "# changed\n", )
        commit(worktree, "change file with space")
        verified = script(
            repo, "orchestrator.py", "verify-gate", "--worktree", str(worktree)
        )
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        args = json.loads(args_file.read_text())
        self.assertEqual(args, ["src/my file.py"])
        self.cleanup(repo, "space-file", ("--force",))

    def test_cleanup_refuses_dirty_unpushed_unregistered(self):
        repo, _ = self.make_origin_repo()
        self.assertEqual(self.create(repo, "dirty").returncode, 0)
        worktree = repo.parent / f"{repo.name}-dirty"
        write(worktree / "junk.txt", "x\n")
        refused = self.cleanup(repo, "dirty")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("uncommitted", refused.stderr)
        forced = self.cleanup(repo, "dirty", ("--force",))
        self.assertEqual(forced.returncode, 0, forced.stderr)

        self.assertEqual(self.create(repo, "unpushed").returncode, 0)
        worktree = repo.parent / f"{repo.name}-unpushed"
        write(worktree / "change.txt", "x\n")
        commit(worktree, "local only")
        refused = self.cleanup(repo, "unpushed")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("unpushed", refused.stderr)
        self.assertEqual(self.cleanup(repo, "unpushed", ("--force",)).returncode, 0)

        manual = repo.parent / f"{repo.name}-manual"
        git(repo, "worktree", "add", str(manual), "-b", "manual")
        unregistered = self.cleanup(repo, "manual")
        self.assertNotEqual(unregistered.returncode, 0)
        self.assertIn("not created by this tool", unregistered.stderr)
        git(repo, "worktree", "remove", "--force", str(manual))
        git(repo, "branch", "-D", "manual")

    def test_clean_pushed_worktree_cleans_up(self):
        repo, _ = self.make_origin_repo()
        self.assertEqual(self.create(repo, "clean", ("--type", "test")).returncode, 0)
        worktree = repo.parent / f"{repo.name}-clean"
        write(worktree / "change.txt", "x\n")
        commit(worktree, "pushed")
        pushed = git(worktree, "push", "-u", "origin", "test/clean")
        self.assertEqual(pushed.returncode, 0, pushed.stderr)
        result = self.cleanup(repo, "clean", ("--delete-branch",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(worktree.exists())
        branch = git(repo, "rev-parse", "--verify", "refs/heads/test/clean")
        self.assertNotEqual(branch.returncode, 0)

    def test_verify_gate_after_commit(self):
        repo, _ = self.make_origin_repo()
        args_file = self.tmp / "gate-args.json"
        stub = write(
            self.tmp / "gate_stub.py",
            "import json, pathlib, sys\n"
            f"pathlib.Path({str(args_file)!r}).write_text(json.dumps(sys.argv[1:]))\n",
        )
        cfg = load_example()
        cfg["analyze_cmd"] = [PYTHON, str(stub)]
        cfg["format_check_cmd"] = None
        cfg["gate_cmd"] = None
        write_config(repo, cfg)
        commit(repo)
        push(repo)

        self.assertEqual(self.create(repo, "gate").returncode, 0)
        worktree = repo.parent / f"{repo.name}-gate"
        write(worktree / "src" / "app.py", "def f(a, b):\n    return a < b\n")
        commit(worktree, "committed change")
        result = script(repo, "orchestrator.py", "verify-gate", "--worktree", str(worktree))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(args_file.read_text()), ["src/app.py"])
        self.cleanup(repo, "gate", ("--force",))

    def test_verify_gate_rejects_sidecars(self):
        repo, _ = self.make_origin_repo()
        cfg = load_example()
        cfg["format_check_cmd"] = None
        cfg["analyze_cmd"] = None
        cfg["gate_cmd"] = None
        write_config(repo, cfg)
        write(repo / ".gitignore", "*.mutbak\n*.mutlock\n")
        commit(repo)
        push(repo)
        self.assertEqual(self.create(repo, "sidecar").returncode, 0)
        worktree = repo.parent / f"{repo.name}-sidecar"
        write(worktree / "src" / "app.py.mutbak", "backup\n")
        result = script(repo, "orchestrator.py", "verify-gate", "--worktree", str(worktree))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sidecar", result.stderr)
        self.cleanup(repo, "sidecar", ("--force",))

    def test_verify_gate_skips_unconfigured_steps(self):
        repo, _ = self.make_origin_repo()
        cfg = load_example()
        cfg["format_check_cmd"] = None
        cfg["analyze_cmd"] = None
        cfg["gate_cmd"] = None
        write_config(repo, cfg)
        commit(repo)
        push(repo)
        self.assertEqual(self.create(repo, "skip").returncode, 0)
        worktree = repo.parent / f"{repo.name}-skip"
        result = script(repo, "orchestrator.py", "verify-gate", "--worktree", str(worktree))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[skip] format check", result.stdout)
        self.cleanup(repo, "skip")


class MutationEngineTests(RepoTestCase):
    def test_python_mutants(self):
        source = write(
            self.tmp / "sample.py",
            "def f(a, b):\n"
            "    if a is not None and b:\n"
            "        return True\n"
            "    return a in b or not a\n",
        )
        mutations = engine_module.generate_mutations(str(source))
        descriptions = " ".join(item.description for item in mutations)
        self.assertGreaterEqual(len(mutations), 5)
        self.assertIn("is not None", descriptions)
        self.assertIn("logical AND", descriptions)
        self.assertIn("True", descriptions)
        self.assertIn("membership", descriptions)
        self.assertIn("logical OR", descriptions)

    def test_compile_classifier(self):
        python_pack = engine_module.load_pack("python", "x.py")
        dart_pack = engine_module.load_pack("dart", "x.dart")
        ts_pack = engine_module.load_pack("typescript", "x.ts")
        go_pack = engine_module.load_pack("go", "x.go")

        test_failure = (
            "00:01 +0 -1: t [E]\n"
            "  Expected: <3>\n"
            "    Actual: <4>\n"
            "  Unhandled error: Bad state"
        )
        assertion_failure = "FAILED t.py::test_x - AssertionError\nlast error: connection refused"
        for pack in (python_pack, dart_pack, ts_pack, go_pack):
            self.assertFalse(engine_module.is_compile_failure(test_failure, pack))
            self.assertFalse(engine_module.is_compile_failure(assertion_failure, pack))

        self.assertTrue(engine_module.is_compile_failure("  SyntaxError: invalid syntax", python_pack))
        self.assertTrue(
            engine_module.is_compile_failure("lib/x.dart:3:5: Error: Expected ';'", dart_pack)
        )
        self.assertTrue(
            engine_module.is_compile_failure("src/x.ts(1,1): error TS2322: bad type", ts_pack)
        )
        self.assertTrue(engine_module.is_compile_failure("./main.go:3:1: undefined: x", go_pack))

    def test_null_equality_single_mutant(self):
        source = write(self.tmp / "n.dart", "if (x == null) return;\n")
        mutations = engine_module.generate_mutations(str(source))
        null_mutants = [item for item in mutations if "!= null" in item.mutated_text]
        self.assertEqual(len(null_mutants), 1)

    def test_no_space_equality_mutates(self):
        source = write(self.tmp / "no_space.py", "if x==-1:\n    pass\n")
        mutations = engine_module.generate_mutations(str(source))
        self.assertTrue(any("x != -1" in item.mutated_text for item in mutations))

    def test_generics_not_mutated(self):
        source = write(self.tmp / "g.dart", "List<int> xs = [];\nMap<String, List<int>> m = {};\n")
        self.assertEqual(engine_module.generate_mutations(str(source)), [])

    def test_trailing_comment_and_multiline_string_guarded(self):
        source = write(
            self.tmp / "c.py",
            'x = a == b  # a == b\ny = """a == b\nc == d"""\nz = "a == b"\n',
        )
        mutations = engine_module.generate_mutations(str(source))
        self.assertEqual([item.line_no for item in mutations], [1])
        self.assertIn("# a == b", mutations[0].mutated_text)
        self.assertIn("a != b", mutations[0].mutated_text)

    def test_negative_literal_mutates(self):
        source = write(self.tmp / "neg.py", "if x == -1:\n    pass\n")
        mutations = engine_module.generate_mutations(str(source))
        self.assertTrue(any("x != -1" in item.mutated_text for item in mutations))

    def test_diff_base_limits_lines(self):
        repo, _ = python_repo(self.tmp)
        source_text = "".join(f"v{i} = a == b\n" for i in range(1, 13))
        write(repo / "src" / "app.py", source_text)
        commit(repo, "base")
        lines = source_text.splitlines()
        lines[4] = "v5 = a != b"
        lines[8] = "v9 = a != b"
        write(repo / "src" / "app.py", "\n".join(lines) + "\n")
        commit(repo, "change two lines")
        output = self.tmp / "diff.json"
        result = script(
            repo, "mutation_engine.py",
            "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} -c pass",
            "--diff-base", "HEAD~1",
            "--max-mutants", "50",
            "--output-json", str(output),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(output.read_text())
        evaluated = {item["line"] for item in data["survived"] + data["killed"] + data["timeout"] + data["uncompilable"]}
        self.assertEqual(evaluated, {5, 9})

    def test_seeded_sampling_reaches_tail(self):
        lines = ["x = a == b\n"] * 300
        source = write(self.tmp / "long.py", "".join(lines))
        mutations = engine_module.generate_mutations(str(source))
        selected = engine_module.select_mutations(mutations, 10, 0)
        self.assertEqual(len(selected), 10)
        self.assertTrue(any(item.line_no > 200 for item in selected))
        self.assertEqual(
            [item.line_no for item in selected],
            [item.line_no for item in engine_module.select_mutations(mutations, 10, 0)],
        )

    def test_shards_partition_mutants(self):
        repo, _ = python_repo(self.tmp)
        write(repo / "src" / "app.py", "def f(a, b):\n    return a == b\n" * 4)
        commit(repo)
        all_run = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} -c pass", "--max-mutants", "50",
            "--output-json", str(self.tmp / "all.json"),
        )
        self.assertEqual(all_run.returncode, 0, all_run.stderr)
        all_mutants = json.loads((self.tmp / "all.json").read_text())["survived"]
        shards = []
        for index in (1, 2):
            result = script(
                repo, "mutation_engine.py", "--source", "src/app.py",
                "--test-cmd", f"{PYTHON} -c pass", "--max-mutants", "50",
                "--shard", f"{index}/2",
                "--output-json", str(self.tmp / f"shard{index}.json"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            shards.append(json.loads((self.tmp / f"shard{index}.json").read_text())["survived"])
        keys = [{ (item["line"], item["column"]) for item in shard } for shard in shards]
        self.assertFalse(keys[0] & keys[1])
        self.assertEqual(keys[0] | keys[1], { (item["line"], item["column"]) for item in all_mutants })

    def test_timeout_state_and_missing_executable(self):
        repo, _ = python_repo(self.tmp)
        write(repo / "src" / "app.py", "def f(a, b):\n    return a == b\n")
        commit(repo)
        (repo / "orig.txt").write_text((repo / "src" / "app.py").read_text())
        stub = write(
            repo / "slow.py",
            "import pathlib, sys, time\n"
            "orig = pathlib.Path('orig.txt').read_text()\n"
            "actual = pathlib.Path('src/app.py').read_text()\n"
            "if actual != orig:\n"
            "    for _ in range(300):\n"
            "        if pathlib.Path('stop').exists():\n"
            "            sys.exit(0)\n"
            "        time.sleep(0.1)\n"
            "sys.exit(0)\n",
        )
        output = self.tmp / "timeout.json"
        result = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} {stub}", "--max-mutants", "1",
            "--timeout", "0.3", "--output-json", str(output),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(output.read_text())
        self.assertEqual(data["counts"]["timeout"], 1)
        self.assertEqual(data["counts"]["survived"], 0)

        missing = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", "definitely-not-a-real-tool-xyz", "--max-mutants", "1",
        )
        self.assertEqual(missing.returncode, 3)

    def test_exit_codes(self):
        repo, _ = python_repo(self.tmp)
        commit(repo)
        completed = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} -c pass", "--max-mutants", "10",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        failed = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} -c pass", "--max-mutants", "10",
            "--fail-on-survivors",
        )
        self.assertEqual(failed.returncode, 1)
        unknown = write(self.tmp / "x.txt", "x == y\n")
        usage = script(
            repo, "mutation_engine.py", "--source", str(unknown),
            "--test-cmd", f"{PYTHON} -c pass",
        )
        self.assertEqual(usage.returncode, 2)
        self.assertIn("unsupported source extension", usage.stderr)

    def test_sigterm_restores_source(self):
        repo, _ = python_repo(self.tmp)
        write(repo / "src" / "app.py", "def f(a, b):\n    return a == b\n")
        commit(repo)
        original = (repo / "src" / "app.py").read_bytes()
        (repo / "orig.txt").write_text(original.decode())
        write(
            repo / "slow.py",
            "import pathlib, sys, time\n"
            "orig = pathlib.Path('orig.txt').read_text()\n"
            "actual = pathlib.Path('src/app.py').read_text()\n"
            "if actual != orig:\n"
            "    for _ in range(300):\n"
            "        if pathlib.Path('stop').exists():\n"
            "            sys.exit(0)\n"
            "        time.sleep(0.1)\n"
            "sys.exit(0)\n",
        )
        engine = repo / "scripts" / "autonomous" / "mutation_engine.py"
        process = subprocess.Popen(
            [PYTHON, str(engine), "--source", "src/app.py",
             "--test-cmd", f"{PYTHON} slow.py", "--max-mutants", "1"],
            cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        backup = repo / "src" / "app.py.mutbak"
        for _ in range(100):
            if backup.exists():
                break
            time.sleep(0.05)
        self.assertTrue(backup.exists())
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=15)
        self.assertEqual((repo / "src" / "app.py").read_bytes(), original)
        self.assertFalse(backup.exists())
        self.assertFalse((repo / "src" / "app.py.mutlock").exists())

    def test_sigkill_recovery(self):
        repo, _ = python_repo(self.tmp)
        write(repo / "src" / "app.py", "def f(a, b):\n    return a == b\n")
        commit(repo)
        original = (repo / "src" / "app.py").read_bytes()
        (repo / "orig.txt").write_text(original.decode())
        write(
            repo / "slow.py",
            "import pathlib, sys, time\n"
            "orig = pathlib.Path('orig.txt').read_text()\n"
            "actual = pathlib.Path('src/app.py').read_text()\n"
            "if actual != orig:\n"
            "    for _ in range(300):\n"
            "        if pathlib.Path('stop').exists():\n"
            "            sys.exit(0)\n"
            "        time.sleep(0.1)\n"
            "sys.exit(0)\n",
        )
        engine = repo / "scripts" / "autonomous" / "mutation_engine.py"
        process = subprocess.Popen(
            [PYTHON, str(engine), "--source", "src/app.py",
             "--test-cmd", f"{PYTHON} slow.py", "--max-mutants", "1"],
            cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        backup = repo / "src" / "app.py.mutbak"
        for _ in range(100):
            if backup.exists():
                break
            time.sleep(0.05)
        self.assertTrue(backup.exists())
        process.kill()
        process.communicate()
        (repo / "stop").write_text("stop")
        self.assertNotEqual((repo / "src" / "app.py").read_bytes(), original)
        self.assertTrue(backup.exists())

        recovered = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} -c pass", "--max-mutants", "0",
        )
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertIn("restored", recovered.stderr)
        self.assertEqual((repo / "src" / "app.py").read_bytes(), original)
        self.assertFalse(backup.exists())
        self.assertFalse((repo / "src" / "app.py.mutlock").exists())

    def test_live_lock_aborts(self):
        repo, _ = python_repo(self.tmp)
        commit(repo)
        backup = repo / "src" / "app.py.mutbak"
        lock = repo / "src" / "app.py.mutlock"
        backup.write_bytes((repo / "src" / "app.py").read_bytes())
        lock.write_text(str(os.getpid()))
        result = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} -c pass", "--max-mutants", "0",
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("another mutation run owns", result.stderr)
        backup.unlink()
        lock.unlink()

    def test_dirty_source_refused(self):
        repo, _ = python_repo(self.tmp)
        commit(repo)
        write(repo / "src" / "app.py", "# dirty\n")
        refused = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} -c pass", "--max-mutants", "0",
        )
        self.assertEqual(refused.returncode, 1)
        self.assertIn("uncommitted changes", refused.stderr)
        allowed = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} -c pass", "--max-mutants", "0", "--allow-dirty",
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)


    def test_same_size_mutant_not_masked_by_bytecode_cache(self):
        # Regression: `==` -> `!=` keeps size and, within one second, the
        # mtime a .pyc validates against; the engine must not execute stale
        # bytecode and report the mutant as a false survivor.
        repo, _ = python_repo(self.tmp)
        commit(repo)
        output = self.tmp / "pycache.json"
        result = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd",
            f'{PYTHON} -c "from src.app import f; assert f(1, 1) is True; assert f(1, 2) is False"',
            "--max-mutants", "10", "--output-json", str(output),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(output.read_text())
        self.assertGreaterEqual(data["counts"]["killed"], 1)
        self.assertFalse(
            any("!=" in item["mutated"] for item in data["survived"]),
            data["survived"],
        )

    def test_baseline_relative_timeout_beats_fixed(self):
        repo, _ = python_repo(self.tmp)
        commit(repo)
        (repo / "orig.txt").write_text((repo / "src" / "app.py").read_text())
        stub = write(
            repo / "slowkill.py",
            "import pathlib, sys, time\n"
            "if pathlib.Path('src/app.py').read_text() != pathlib.Path('orig.txt').read_text():\n"
            "    time.sleep(1.0)\n"
            "    sys.exit(1)\n"
            "sys.exit(0)\n",
        )
        fixed_out = self.tmp / "fixed.json"
        fixed = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} {stub}", "--max-mutants", "1",
            "--timeout", "0.3", "--confirm-survivors", "0", "--no-cache",
            "--output-json", str(fixed_out),
        )
        self.assertEqual(fixed.returncode, 0, fixed.stderr)
        self.assertEqual(json.loads(fixed_out.read_text())["counts"]["timeout"], 1)

        rel_out = self.tmp / "relative.json"
        relative = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} {stub}", "--max-mutants", "1",
            "--timeout-floor", "2", "--confirm-survivors", "0", "--no-cache",
            "--output-json", str(rel_out),
        )
        self.assertEqual(relative.returncode, 0, relative.stderr)
        data = json.loads(rel_out.read_text())
        self.assertEqual(data["counts"]["timeout"], 0)
        self.assertEqual(data["counts"]["killed"], 1)
        self.assertGreaterEqual(data["timeout_effective"], 2)

    def test_flaky_survivor_is_not_confirmed(self):
        repo, _ = python_repo(self.tmp)
        commit(repo)
        counter = repo / "counter.txt"
        stub = write(
            repo / "flake.py",
            "import pathlib, sys\n"
            f"path = pathlib.Path({str(counter)!r})\n"
            "n = int(path.read_text()) if path.exists() else 0\n"
            "path.write_text(str(n + 1))\n"
            "sys.exit(1 if n % 3 == 2 else 0)\n",
        )
        out = self.tmp / "flake.json"
        result = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} {stub}", "--max-mutants", "1",
            "--no-cache", "--output-json", str(out),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(out.read_text())
        self.assertEqual(data["counts"]["survived"], 0)
        self.assertEqual(data["counts"]["unverified"], 1)

        counter.write_text("0")
        out0 = self.tmp / "flake0.json"
        result0 = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} {stub}", "--max-mutants", "1",
            "--confirm-survivors", "0", "--no-cache",
            "--output-json", str(out0),
        )
        self.assertEqual(result0.returncode, 0, result0.stderr)
        self.assertEqual(json.loads(out0.read_text())["counts"]["survived"], 1)

    def test_verdict_cache_reuses_second_run(self):
        repo, _ = python_repo(self.tmp)
        commit(repo)
        (repo / "orig.txt").write_text((repo / "src" / "app.py").read_text())
        calls = repo / "calls.txt"
        stub = write(
            repo / "counting.py",
            "import pathlib, sys\n"
            f"path = pathlib.Path({str(calls)!r})\n"
            "path.write_text((path.read_text() if path.exists() else '') + 'x\\n')\n"
            "sys.exit(1 if pathlib.Path('src/app.py').read_text() != pathlib.Path('orig.txt').read_text() else 0)\n",
        )
        args = (
            "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} {stub}", "--max-mutants", "3",
            "--confirm-survivors", "0",
        )
        out1 = self.tmp / "cache1.json"
        first = script(repo, *args, "--output-json", str(out1))
        self.assertEqual(first.returncode, 0, first.stderr)
        data1 = json.loads(out1.read_text())
        self.assertEqual(data1["cache_hits"], 0)
        self.assertGreater(data1["counts"]["killed"], 0)
        calls1 = len(calls.read_text().splitlines())

        out2 = self.tmp / "cache2.json"
        second = script(repo, *args, "--output-json", str(out2))
        self.assertEqual(second.returncode, 0, second.stderr)
        data2 = json.loads(out2.read_text())
        self.assertEqual(data2["cache_hits"], data1["counts"]["total"])
        self.assertEqual(data2["counts"]["killed"], data1["counts"]["killed"])
        # Only the baseline ran again; every verdict came from the cache.
        self.assertEqual(len(calls.read_text().splitlines()), calls1 + 1)

    def test_backend_auto_falls_back_to_regex(self):
        repo, _ = python_repo(self.tmp)
        commit(repo)
        out = self.tmp / "auto.json"
        result = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} -c pass", "--max-mutants", "1",
            "--backend", "auto", "--confirm-survivors", "0", "--no-cache",
            "--output-json", str(out),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sregex", result.stdout)
        self.assertEqual(json.loads(out.read_text())["backend"], "sregex")

    def test_stryker_backend_normalizes_report(self):
        repo, _ = python_repo(self.tmp)
        write(repo / "src" / "app.ts", "function eq(a, b) {\n  return a === b;\n}\n")
        commit(repo)
        report = {
            "files": {
                "src/app.ts": {
                    "language": "typescript",
                    "mutants": [
                        {
                            "id": "0", "mutatorName": "EqualityOperator",
                            "replacement": "!==",
                            "location": {"start": {"line": 2, "column": 12},
                                         "end": {"line": 2, "column": 15}},
                            "status": "Survived",
                        },
                        {
                            "id": "1", "mutatorName": "BooleanLiteral",
                            "replacement": "false",
                            "location": {"start": {"line": 2, "column": 3},
                                         "end": {"line": 2, "column": 18}},
                            "status": "Killed",
                        },
                    ],
                }
            }
        }
        report_path = repo / "mutation.json"
        report_path.write_text(json.dumps(report))
        out = self.tmp / "stryker.json"
        result = script(
            repo, "mutation_engine.py", "--source", "src/app.ts",
            "--test-cmd", f"{PYTHON} -c pass",
            "--backend", "stryker", "--backend-report", str(report_path),
            "--output-json", str(out),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(out.read_text())
        self.assertEqual(data["backend"], "stryker")
        self.assertEqual(data["counts"]["killed"], 1)
        self.assertEqual(data["counts"]["survived"], 1)
        self.assertEqual(data["survived"][0]["mutated"], "  return a !== b;")
        self.assertEqual(data["survived"][0]["line"], 2)

class TestWeakeningTests(RepoTestCase):
    def test_removed_assertion_and_added_skip(self):
        repo, _ = init_repo(self.tmp)
        install_scripts(repo)
        write_config(repo)
        write(repo / "tests" / "test_app.py", "def test_x():\n    assert 1 == 1\n")
        commit(repo, "base")
        git(repo, "checkout", "-q", "-b", "weaken")
        write(repo / "tests" / "test_app.py", "def test_x():\n    pass\n")
        commit(repo, "weaken")
        result = script(repo, "test_weakening_check.py", "--base", "main")
        self.assertEqual(result.returncode, 1)
        self.assertIn("removed-assertion", result.stdout)

        git(repo, "checkout", "-q", "main")
        git(repo, "checkout", "-q", "-b", "skip")
        write(repo / "tests" / "test_app.py", "import pytest\n\n@pytest.mark.skip\ndef test_x():\n    assert 1 == 1\n")
        commit(repo, "skip")
        result = script(repo, "test_weakening_check.py", "--base", "main")
        self.assertEqual(result.returncode, 1)
        self.assertIn("added-weakening-marker", result.stdout)

    def test_clean_diff_passes(self):
        repo, _ = init_repo(self.tmp)
        install_scripts(repo)
        write_config(repo)
        write(repo / "tests" / "test_app.py", "def test_x():\n    assert 1 == 1\n")
        commit(repo, "base")
        git(repo, "checkout", "-q", "-b", "clean")
        write(repo / "tests" / "test_app.py", "def test_x():\n    assert 2 == 2\n")
        commit(repo, "stronger")
        result = script(repo, "test_weakening_check.py", "--base", "main")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(
    all(shutil.which(tool) for tool in ("bash", "flock", "timeout", "stat", "date")),
    "watchdog shell dependencies are not installed",
)


class EvidenceTests(RepoTestCase):
    """Issue #14: the verify-before/after contract as an artifact, not prose."""

    TEST_CMD = f'{PYTHON} -c "from src.app import f; assert f(1, 1) is True; assert f(1, 2) is False"'

    def _repo(self) -> Path:
        # The evidence flow pins its origin; the fixture provides the
        # production shape (a pushed origin/main) instead of degrading to
        # HEAD silently (issue #14).
        repo, _ = python_repo(self.tmp, with_origin=True)
        commit(repo, "base")
        push(repo)
        return repo

    def _engine_report(self, repo: Path, mutated: str) -> Path:
        report = {
            "source": "src/app.py",
            "survived": [
                {
                    "file": "src/app.py",
                    "line": 2,
                    "column": 11,
                    "original": "    return a == b",
                    "mutated": mutated,
                    "description": "fixture mutant",
                }
            ],
        }
        path = repo / "engine.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def test_mutant_evidence_artifact_records_three_runs(self):
        repo = self._repo()
        report = self._engine_report(repo, "    return a != b")
        result = script(
            repo, "evidence.py", "mutant",
            "--engine-json", str(report), "--index", "0",
            "--test-cmd", self.TEST_CMD,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        artifact = json.loads((repo / ".autonomous" / "evidence.json").read_text())
        self.assertEqual(artifact["schema"], 1)
        self.assertEqual(artifact["kind"], "mutant")
        self.assertEqual(artifact["verdict"], "killed")
        self.assertNotEqual(artifact["runs"]["red"]["exit"], 0)
        self.assertEqual(artifact["runs"]["green"]["exit"], 0)
        self.assertEqual(artifact["runs"]["pass_to_pass"]["exit"], 0)
        # Issue #14: each run records what actually ran, not just what came out.
        import shlex as _shlex

        expected_argv = _shlex.split(self.TEST_CMD)
        for run_name in ("red", "green", "pass_to_pass"):
            self.assertEqual(artifact["runs"][run_name]["argv"], expected_argv)
        # The fixture engine report carries no seed; the artifact mirrors it
        # (a generated mutant's seed rides with the artifact instead).
        self.assertNotIn("seed", artifact)
        self.assertEqual(artifact["mutant"]["mutated"], "    return a != b")
        # The mutant must never survive the evidence run itself.
        self.assertEqual(
            (repo / "src" / "app.py").read_text(), "def f(a, b):\n    return a == b\n"
        )
        self.assertFalse((repo / "src" / "app.py.mutbak").exists())

    def test_engine_seed_is_pinned_into_the_artifact(self):
        # A generated mutant is reproducible only through its seed; the
        # artifact must carry the spec's seed verbatim (issue #14).
        import hashlib as _hashlib

        repo = self._repo()
        report = {
            "source": "src/app.py",
            "seed": 424242,
            "survived": [
                {
                    "file": "src/app.py",
                    "line": 2,
                    "column": 11,
                    "original": "    return a == b",
                    "mutated": "    return a != b",
                    "description": "fixture mutant",
                }
            ],
        }
        path = repo / "engine.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        result = script(
            repo, "evidence.py", "mutant",
            "--engine-json", str(path), "--index", "0",
            "--test-cmd", self.TEST_CMD,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        artifact = json.loads((repo / ".autonomous" / "evidence.json").read_text())
        self.assertEqual(artifact["seed"], 424242)
        self.assertEqual(
            artifact["mutant"]["source_sha256"],
            _hashlib.sha256((repo / "src" / "app.py").read_bytes()).hexdigest(),
        )

    def test_base_sha_hard_fails_when_base_ref_does_not_resolve(self):
        # Issue #14: a base ref that will not resolve used to fall back to
        # HEAD, making the ancestor check trivially true. Hard-fail instead:
        # evidence whose origin cannot be pinned must never be produced.
        repo = self._repo()
        report = self._engine_report(repo, "    return a != b")
        result = script(
            repo, "evidence.py", "mutant",
            "--engine-json", str(report), "--index", "0",
            "--test-cmd", self.TEST_CMD,
            "--base", "refs/heads/no-such-base-ref",
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("cannot resolve", result.stderr)
        self.assertFalse((repo / ".autonomous" / "evidence.json").exists())

    def test_mutant_not_killed_exits_nonzero(self):
        repo = self._repo()
        report = self._engine_report(repo, "    return not (a != b)")
        result = script(
            repo, "evidence.py", "mutant",
            "--engine-json", str(report), "--index", "0",
            "--test-cmd", self.TEST_CMD,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        artifact = json.loads((repo / ".autonomous" / "evidence.json").read_text())
        self.assertEqual(artifact["verdict"], "not-killed")
        self.assertEqual(artifact["runs"]["red"]["exit"], 0)

    def test_check_rejects_artifact_after_source_changes(self):
        repo = self._repo()
        report = self._engine_report(repo, "    return a != b")
        made = script(
            repo, "evidence.py", "mutant",
            "--engine-json", str(report), "--index", "0",
            "--test-cmd", self.TEST_CMD,
        )
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        ok = script(repo, "evidence.py", "check")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        write(repo / "src" / "app.py", "def f(a, b):\n    return bool(a == b)\n")
        stale = script(repo, "evidence.py", "check")
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("source changed", stale.stdout + stale.stderr)

    def test_defect_evidence_requires_red_on_base(self):
        repo, _ = python_repo(self.tmp)
        write(repo / "src" / "app.py", "def f(a, b):\n    return False\n")
        commit(repo, "buggy base")
        write(repo / "src" / "app.py", "def f(a, b):\n    return a == b\n")
        result = script(
            repo, "evidence.py", "defect",
            "--base", "HEAD",
            "--test-file", "tests/test_app.py",
            "--test-cmd", self.TEST_CMD,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        artifact = json.loads((repo / ".autonomous" / "evidence.json").read_text())
        self.assertEqual(artifact["kind"], "defect")
        self.assertEqual(artifact["verdict"], "reproduced-and-fixed")
        self.assertNotEqual(artifact["runs"]["red"]["exit"], 0)
        self.assertEqual(artifact["runs"]["green"]["exit"], 0)
        # The throwaway base worktree must be gone afterwards.
        listed = git(repo, "worktree", "list", "--porcelain")
        self.assertEqual(listed.stdout.count("worktree "), 1)

    def test_verify_gate_fails_on_weakening_edit(self):
        repo, _ = python_repo(self.tmp, with_origin=True)
        cfg = load_example()
        cfg["format_check_cmd"] = None
        cfg["analyze_cmd"] = None
        cfg["gate_cmd"] = None
        write_config(repo, cfg)
        commit(repo)
        push(repo)
        created = script(repo, "orchestrator.py", "create-worktree", "--slug", "weak")
        self.assertEqual(created.returncode, 0, created.stderr)
        worktree = repo.parent / f"{repo.name}-weak"
        weakened = "from src.app import f\n\ndef test_f():\n    pass\n"
        write(
            worktree / "tests" / "test_app.py",
            weakened,
        )
        commit(worktree, "weaken the test")
        # A valid-looking artifact must pre-exist in the worktree so the gate
        # can record the weakening hits it finds into it (issue #14): the
        # print/raise path leaves no durable trace otherwise.
        import hashlib as _hashlib

        mutation_dir = worktree / ".autonomous"
        mutation_dir.mkdir()
        head_sha = run(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip()
        artifact = {
            "schema": 1,
            "kind": "mutant",
            "generated_at": "2026-10-23T00:00:00+00:00",
            "base_ref": "HEAD",
            "base_sha": head_sha,
            "head_sha": head_sha,
            "test_cmd": "python3 -m test_of_nothing",
            "pass_to_pass_cmd": "python3 -m test_of_nothing",
            "mutant": {
                "file": "tests/test_app.py",
                "line": 3,
                "original": "def test_f():\n    assert False\n",
                "mutated": "def test_f():\n    pass\n",
                "description": "fixture mutant",
                "source_sha256": _hashlib.sha256(weakened.encode()).hexdigest(),
            },
            "runs": {
                "red": {"exit": 1, "timed_out": False, "duration_s": 0.1, "summary": "red"},
                "green": {"exit": 0, "timed_out": False, "duration_s": 0.1, "summary": "green"},
                "pass_to_pass": {"exit": 0, "timed_out": False, "duration_s": 0.1, "summary": "p2p"},
            },
            "verdict": "killed",
        }
        artifact_path = mutation_dir / "evidence.json"
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

        result = script(repo, "orchestrator.py", "verify-gate", "--worktree", str(worktree))
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("weakening", result.stdout + result.stderr)
        # The hits must outlive the gate: recorded into the artifact, keyed by
        # the gate, not just printed into a log nobody archives.
        recorded = json.loads(artifact_path.read_text())
        hits = recorded["verify_gate"]["weakening_hits"]
        self.assertTrue(
            any(h.get("file") == "tests/test_app.py" for h in hits), hits
        )
        self.assertTrue(
            all("kind" in h for h in hits), hits
        )
        self.assertEqual(recorded["verify_gate"]["weakening_mode"], "fail")
        self.assertIn("recorded", result.stdout + result.stderr)
        script(repo, "orchestrator.py", "cleanup-worktree", "--slug", "weak", "--force")


@unittest.skipUnless(
    all(shutil.which(tool) for tool in ("bash", "flock", "timeout", "stat", "date")),
    "watchdog shell dependencies are not installed",
)


class WatchdogTests(RepoTestCase):
    def test_fresh_heartbeat_skips_and_stale_runs_nudge(self):
        state = self.tmp / "state"
        state.mkdir()
        (state / "opencode_session_id").write_text("ses_123\n")
        heartbeat = state / "last_beat"
        heartbeat.write_text("")
        marker = self.tmp / "marker.txt"
        env = dict(os.environ)
        env.update(
            ORCH_STATE_DIR=str(state),
            ORCH_RESUME_CMD='printf "%s" "$1" > "$ORCH_MARKER"',
            ORCH_MARKER=str(marker),
            ORCH_HB_MAX_AGE="2700",
        )
        script_path = ASSETS / "watchdog.sh"
        fresh = run([str(script_path)], cwd=self.tmp, env=env)
        self.assertEqual(fresh.returncode, 0)
        self.assertFalse(marker.exists())

        old = time.time() - 3 * 2700
        os.utime(heartbeat, (old, old))
        stale = run([str(script_path)], cwd=self.tmp, env=env)
        self.assertEqual(stale.returncode, 0)
        self.assertTrue(marker.exists())
        self.assertEqual(marker.read_text(), "ses_123")

    def test_installer_completes_and_wires_env_file(self):
        # Regression: the installer used to define a shell function named
        # install(), shadowing /usr/bin/install, so its own `install -m`
        # calls recursed until bash died (exit 139). Stage a full install
        # with stubbed systemctl & friends and assert it completes and the
        # unit reads its ORCH_* configuration from watchdog.env.
        if not shutil.which("bash") or not shutil.which("install"):
            self.skipTest("bash/install not available")
        bin_dir = self.tmp / "bin"
        home = self.tmp / "home"
        bin_dir.mkdir()
        home.mkdir()
        for tool in ("systemctl", "systemd-analyze"):
            stub = write(bin_dir / tool, "#!/bin/sh\nexit 0\n")
            stub.chmod(0o755)
        loginctl = write(bin_dir / "loginctl", "#!/bin/sh\necho no\n")
        loginctl.chmod(0o755)
        env = dict(os.environ)
        env.update(HOME=str(home), PATH=f"{bin_dir}:{os.environ['PATH']}")
        env.pop("XDG_CONFIG_HOME", None)
        # The installer reads ORCH_STATE_DIR from the environment. A machine
        # that runs the watchdog (a fleet host) exports it, and the test then
        # writes the env file into the REAL state dir instead of its temp
        # home - a failure that depends on the machine, not on the code.
        env.pop("ORCH_STATE_DIR", None)
        result = run([str(ASSETS / "install-watchdog.sh")], cwd=self.tmp, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        unit = home / ".config/systemd/user/orchestrator-watchdog.service"
        self.assertTrue(unit.exists())
        self.assertIn("EnvironmentFile=", unit.read_text())
        self.assertIn("watchdog.env", unit.read_text())
        env_file = home / ".local/share/orchestrator/watchdog.env"
        self.assertTrue(env_file.exists())
        self.assertIn("ORCH_MODEL_LADDER", env_file.read_text())
        self.assertTrue((home / ".local/share/orchestrator/watchdog.sh").exists())
        snap_unit = home / ".config/systemd/user/orchestrator-snapshot.service"
        self.assertTrue(snap_unit.exists())
        self.assertTrue((home / ".local/share/orchestrator/snapshot.sh").exists())
        wake_unit = home / ".config/systemd/user/orchestrator-wake.service"
        self.assertTrue(wake_unit.exists())
        self.assertIn("wake.sh", wake_unit.read_text())
        self.assertTrue((home / ".local/share/orchestrator/wake.sh").exists())

    def test_no_session_id_is_a_noop(self):
        state = self.tmp / "state"
        state.mkdir()
        marker = self.tmp / "marker.txt"
        env = dict(os.environ)
        env.update(
            ORCH_STATE_DIR=str(state),
            ORCH_RESUME_CMD='printf touched > "$ORCH_MARKER"',
            ORCH_MARKER=str(marker),
        )
        result = run([str(ASSETS / "watchdog.sh")], cwd=self.tmp, env=env)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(marker.exists())


class StreamRegistryTests(unittest.TestCase):
    def test_available_streams_normalize(self):
        self.assertEqual(common_module.normalize_stream("A"), "A")
        self.assertEqual(common_module.normalize_stream("2"), "B")
        self.assertEqual(common_module.normalize_stream("b (test audit)"), "B")

    def test_planned_streams_rejected_by_name(self):
        # The catalog (references/08) is wider than the tooling. Every
        # non-available letter must refuse with its full name, so a session
        # cannot drift into un-tooled work under a planned letter.
        for stream, info in common_module.STREAMS.items():
            # Gated letters (L) pass normalize on purpose: their caller-side
            # kill switch owns the refusal.
            if info["status"] != "planned":
                continue
            with self.assertRaises(common_module.UsageError) as ctx:
                common_module.normalize_stream(stream)
            self.assertIn("planned, not implemented", str(ctx.exception))
            self.assertIn(info["label"], str(ctx.exception))

    def test_registry_matches_reference_catalog(self):
        catalog = (
            SKILL_ROOT / "references" / "08-recursive-self-improvement.md"
        ).read_text().lower()
        for info in common_module.STREAMS.values():
            name = info["label"].split(" (", 1)[1].rstrip(")").lower()
            # Registry labels are canonical ("Security & Supply Chain");
            # the catalog may use a shortened form, so the first distinctive
            # word (>3 chars) is enough to catch registry/catalog drift.
            words = [w.strip(":/()") for w in name.replace("/", " ").split()]
            distinctive = next(word for word in words if len(word) > 3)
            self.assertIn(distinctive, catalog)


class FleetTests(RepoTestCase):
    def make(self, with_origin: bool = False):
        repo, origin = python_repo(self.tmp, with_origin=with_origin)
        commit(repo)
        return repo, origin

    def test_pr_state_evaluates_checks_and_catches_older_sha(self):
        repo, _ = self.make()
        payload = [
            {
                "number": 1,
                "title": "Stream A: test PR 1",
                "headRefName": "task/pr-1",
                "headRefOid": "commit_sha_1",
                "checks_sha": "commit_sha_1",
                "statusCheckRollup": [
                    {"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}
                ],
            },
            {
                "number": 2,
                "title": "Stream A: test PR 2",
                "headRefName": "task/pr-2",
                "headRefOid": "commit_sha_2_new",
                "checks_sha": "commit_sha_2_old",
                "statusCheckRollup": [
                    {"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}
                ],
            },
            {
                "number": 3,
                "title": "Stream A: test PR 3",
                "headRefName": "task/pr-3",
                "headRefOid": "commit_sha_3",
                "checks_sha": "commit_sha_3",
                "statusCheckRollup": [
                    {"name": "unit-tests", "status": "COMPLETED", "conclusion": "FAILURE"}
                ],
            },
            {
                "number": 4,
                "title": "Stream A: test PR 4",
                "headRefName": "task/pr-4",
                "headRefOid": "commit_sha_4",
                "checks_sha": "commit_sha_4",
                "statusCheckRollup": [
                    {
                        "name": "referee-protected-gate",
                        "status": "COMPLETED",
                        "conclusion": "FAILURE",
                    }
                ],
            },
        ]
        json_path = self.tmp / "prs.json"
        json_path.write_text(json.dumps(payload) + "\n")
        bin_dir = path_with_only(self.tmp, ("git",))
        gh = write(
            bin_dir / "gh",
            "#!/bin/sh\nwhile IFS= read -r line; do printf '%s\\n' \"$line\"; done < \"$GH_JSON\"\n",
        )
        gh.chmod(0o755)
        env = env_with_path(bin_dir, GH_JSON=str(json_path))

        result = script(repo, "fleet.py", "pr-state", "--json", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        by_pr = {item["pr"]: item for item in data}

        self.assertEqual(by_pr[1]["checks"], "green")
        self.assertEqual(by_pr[1]["head_sha"], "commit_sha_1")

        # Law 1 / Issue #11: ONLY green run is for older sha -> MUST BE PENDING, NEVER GREEN
        self.assertEqual(by_pr[2]["checks"], "pending")
        self.assertEqual(by_pr[2]["head_sha"], "commit_sha_2_new")
        self.assertEqual(by_pr[2]["checks_sha"], "commit_sha_2_old")

        self.assertEqual(by_pr[3]["checks"], "red")
        self.assertEqual(by_pr[3]["red_kind"], "in-diff")

        self.assertEqual(by_pr[4]["checks"], "red")
        self.assertEqual(by_pr[4]["red_kind"], "referee")

    def test_snapshot_records_dirty_worktree_without_gh(self):
        repo, origin = init_repo(self.tmp, name="main-repo", with_origin=True)
        install_scripts(repo)
        write_config(repo)
        commit(repo, "init")
        push(repo)

        result_wt = script(
            repo,
            "orchestrator.py",
            "create-worktree",
            "--slug",
            "task-alpha",
            "--type",
            "test",
        )
        self.assertEqual(result_wt.returncode, 0, result_wt.stderr)
        wt_path = self.tmp / "main-repo-task-alpha"
        self.assertTrue(wt_path.exists())

        write(wt_path / "dirty.txt", "untracked dirty content\n")

        bin_dir = path_with_only(self.tmp, ("git",))
        env = env_with_path(bin_dir)

        state_dir = self.tmp / "state"
        result_snap = script(
            repo, "fleet.py", "snapshot", "--state-dir", str(state_dir), env=env
        )
        self.assertEqual(result_snap.returncode, 0, result_snap.stderr)
        self.assertIn("gh", (result_snap.stdout + result_snap.stderr).lower())

        snap_file = state_dir / "snapshot.json"
        self.assertTrue(snap_file.exists())
        snap_data = json.loads(snap_file.read_text())

        self.assertIn("main_sha", snap_data)
        self.assertEqual(snap_data["open_prs"], [])
        wts = snap_data["worktrees"]
        self.assertEqual(len(wts), 1)
        self.assertEqual(wts[0]["slug"], "task-alpha")
        self.assertTrue(wts[0]["is_dirty"])
        self.assertGreaterEqual(wts[0]["dirty_count"], 1)

    def test_govern_enforces_wip_cap_and_kill_switch(self):
        repo, _ = self.make()
        bin_dir = path_with_only(self.tmp, ("git",))
        gh = write(
            bin_dir / "gh",
            "#!/bin/sh\nwhile IFS= read -r line; do printf '%s\\n' \"$line\"; done < \"$GH_JSON\"\n",
        )
        gh.chmod(0o755)

        # 5 open PRs with max_open_prs: 5 -> blocked
        five_prs = [
            {"number": i, "headRefName": f"pr-{i}", "headRefOid": f"sha-{i}"}
            for i in range(1, 6)
        ]
        json_path = self.tmp / "prs_gov.json"
        json_path.write_text(json.dumps(five_prs) + "\n")
        env = env_with_path(bin_dir, GH_JSON=str(json_path))

        res_blocked = script(repo, "fleet.py", "govern", "--json", env=env)
        self.assertEqual(res_blocked.returncode, 1)
        data_blocked = json.loads(res_blocked.stdout)
        self.assertFalse(data_blocked["dispatch"])
        self.assertIn("5 >= 5", data_blocked["reason"])

        # 4 open PRs (one merged) -> allowed
        four_prs = five_prs[:4]
        json_path.write_text(json.dumps(four_prs) + "\n")
        res_allowed = script(repo, "fleet.py", "govern", "--json", env=env)
        self.assertEqual(res_allowed.returncode, 0)
        data_allowed = json.loads(res_allowed.stdout)
        self.assertTrue(data_allowed["dispatch"])

        # Kill switch present -> blocked even with 0 PRs
        write(repo / ".autonomous" / "STOP", "halt\n")
        res_kill = script(repo, "fleet.py", "govern", "--json", env=env)
        self.assertEqual(res_kill.returncode, 1)
        data_kill = json.loads(res_kill.stdout)
        self.assertFalse(data_kill["dispatch"])
        self.assertIn("kill switch", data_kill["reason"].lower())

    def test_govern_enforces_acceptance_floor(self):
        # Issue #13: min_acceptance_rate_14d parsed by cmd_govern must actually
        # gate dispatch. The stub serves one JSON payload to both governor
        # fetches (open-cap list and 14d acceptance list), so entries stay
        # capped below max_open_prs (5) and max_new_prs_per_day (6) to isolate
        # the floor as the only blocker.
        repo, _ = self.make()
        bin_dir = path_with_only(self.tmp, ("git",))
        gh = write(
            bin_dir / "gh",
            "#!/bin/sh\nwhile IFS= read -r line; do printf '%s\\n' \"$line\"; done < \"$GH_JSON\"\n",
        )
        gh.chmod(0o755)
        json_path = self.tmp / "prs_floor.json"
        env = env_with_path(bin_dir, GH_JSON=str(json_path))

        def fleet_payload(n_open: int, n_merged: int, n_closed: int) -> list:
            entries = [
                {"number": i, "headRefName": f"pr-{i}", "headRefOid": f"sha-{i}"}
                for i in range(1, n_open + 1)
            ]
            for _ in range(n_merged):
                entries.append(
                    {
                        "number": 100,
                        "state": "MERGED",
                        "createdAt": "2026-10-21T01:00:00Z",
                        "mergedAt": "2026-10-21T02:00:00Z",
                    }
                )
            for _ in range(n_closed):
                entries.append(
                    {
                        "number": 200,
                        "state": "CLOSED",
                        "createdAt": "2026-10-21T01:00:00Z",
                        "mergedAt": None,
                    }
                )
            return entries

        # 1 merged + 2 closed-unmerged in-window -> rate 0.33 < floor 0.5 -> blocked
        json_path.write_text(json.dumps(fleet_payload(1, 1, 2)) + "\n")
        res_blocked = script(repo, "fleet.py", "govern", "--json", env=env)
        self.assertEqual(res_blocked.returncode, 1)
        data_blocked = json.loads(res_blocked.stdout)
        self.assertFalse(data_blocked["dispatch"])
        self.assertIn("acceptance", data_blocked["reason"])
        self.assertIn("14d", data_blocked["reason"])

        # 2 merged + 1 closed-unmerged -> rate 0.67 >= floor -> dispatch flows again
        json_path.write_text(json.dumps(fleet_payload(1, 2, 1)) + "\n")
        res_allowed = script(repo, "fleet.py", "govern", "--json", env=env)
        self.assertEqual(res_allowed.returncode, 0)
        self.assertTrue(json.loads(res_allowed.stdout)["dispatch"])

        # Fresh fleet: only PRs settled outside the 14d window -> no signal, dispatch stays open
        fresh = [
            {"number": 1, "headRefName": "pr-1", "headRefOid": "sha-1"},
            {
                "number": 2,
                "state": "MERGED",
                "createdAt": "2026-08-15T00:00:00Z",
                "mergedAt": "2026-08-16T00:00:00Z",
            },
        ]
        json_path.write_text(json.dumps(fresh) + "\n")
        res_fresh = script(repo, "fleet.py", "govern", "--json", env=env)
        self.assertEqual(res_fresh.returncode, 0)
        self.assertTrue(json.loads(res_fresh.stdout)["dispatch"])

    def test_stats_computes_acceptance_rate(self):
        repo, _ = self.make()
        bin_dir = path_with_only(self.tmp, ("git",))
        gh = write(
            bin_dir / "gh",
            "#!/bin/sh\nwhile IFS= read -r line; do printf '%s\\n' \"$line\"; done < \"$GH_JSON\"\n",
        )
        gh.chmod(0o755)

        prs_stats = [
            {
                "number": 1,
                "state": "MERGED",
                "mergedAt": "2026-10-21T01:00:00Z",
                "title": "Stream A: pr1",
                "body": "Model: TestModel-A",
            },
            {
                "number": 2,
                "state": "MERGED",
                "mergedAt": "2026-10-21T02:00:00Z",
                "title": "Stream A: pr2",
                "body": "Model: TestModel-A",
            },
            {
                "number": 3,
                "state": "MERGED",
                "mergedAt": "2026-10-21T03:00:00Z",
                "title": "Stream B: pr3",
                "body": "Model: TestModel-B",
            },
            {
                "number": 4,
                "state": "MERGED",
                "mergedAt": "2026-10-21T04:00:00Z",
                "title": "Stream B: pr4",
                "body": "Model: TestModel-B",
            },
            {
                "number": 5,
                "state": "CLOSED",
                "mergedAt": None,
                "closedAt": "2026-10-21T05:00:00Z",
                "title": "Stream A: pr5",
                "body": "Model: TestModel-A",
            },
        ]
        json_path = self.tmp / "prs_stats.json"
        json_path.write_text(json.dumps(prs_stats) + "\n")
        env = env_with_path(bin_dir, GH_JSON=str(json_path))

        res = script(repo, "fleet.py", "stats", "--days", "0", "--json", env=env)
        self.assertEqual(res.returncode, 0, res.stderr)
        stats = json.loads(res.stdout)
        self.assertEqual(stats["prs_opened"], 5)
        self.assertEqual(stats["prs_merged"], 4)
        self.assertEqual(stats["prs_closed_unmerged"], 1)
        self.assertEqual(stats["acceptance_rate"], 0.8)
        self.assertIn("A (Mutation)", stats["by_stream"])
        self.assertIn("TestModel-A", stats["by_model"])


class FleetClaimPublishTests(RepoTestCase):
    """Issue #17: the claim and the transport are code, not prose."""

    def _gh_stub(self, state: Path) -> Dict[str, str]:
        bin_dir = path_with_only(self.tmp, ("git",))
        stub = write(
            bin_dir / "gh",
            f"#!{PYTHON}\n"
            "import json, os, sys\n"
            "state_path = os.environ['GH_STATE']\n"
            "state = json.loads(open(state_path).read()) if os.path.exists(state_path) else {'prs': [], 'issues': [], 'calls': []}\n"
            "args = sys.argv[1:]\n"
            "state['calls'].append(args)\n"
            "def save():\n"
            "    open(state_path, 'w').write(json.dumps(state))\n"
            "def opt(name):\n"
            "    return args[args.index(name) + 1] if name in args else None\n"
            "def body_of():\n"
            "    f = opt('--body-file')\n"
            "    return open(f).read() if f else (opt('--body') or '')\n"
            "cmd = tuple(args[:2])\n"
            "if cmd == ('label', 'create'):\n"
            "    save()\n"
            "elif cmd == ('pr', 'list'):\n"
            "    want = opt('--state') or 'open'\n"
            "    print(json.dumps([p for p in state['prs'] if want == 'all' or p.get('state', 'OPEN').lower() == want]))\n"
            "    save()\n"
            "elif cmd == ('pr', 'create'):\n"
            "    n = 100 + len(state['prs'])\n"
            "    state['prs'].append({'number': n, 'title': opt('--title') or '', 'body': body_of(), 'headRefName': opt('--head') or '', 'files': [], 'state': 'OPEN', 'draft': '--draft' in args})\n"
            "    save()\n"
            "    print(f'https://example.test/pull/{n}')\n"
            "elif cmd == ('pr', 'edit'):\n"
            "    n = int(args[2])\n"
            "    pr = next(p for p in state['prs'] if p['number'] == n)\n"
            "    pr['body'] = body_of()\n"
            "    if opt('--title'): pr['title'] = opt('--title')\n"
            "    save()\n"
            "elif cmd == ('pr', 'ready'):\n"
            "    n = int(args[2])\n"
            "    next(p for p in state['prs'] if p['number'] == n)['draft'] = False\n"
            "    save()\n"
            "elif cmd == ('issue', 'list'):\n"
            "    print(json.dumps(state['issues']))\n"
            "    save()\n"
            "elif cmd == ('issue', 'create'):\n"
            "    n = 200 + len(state['issues'])\n"
            "    state['issues'].append({'number': n, 'title': opt('--title') or '', 'body': body_of()})\n"
            "    save()\n"
            "    print(f'https://example.test/issues/{n}')\n"
            "else:\n"
            "    print(f'unexpected gh args: {args}', file=sys.stderr)\n"
            "    save()\n"
            "    sys.exit(1)\n",
        )
        stub.chmod(0o755)
        state.write_text(json.dumps({"prs": [], "issues": [], "calls": []}))
        return env_with_path(bin_dir, GH_STATE=str(state))

    def _repo(self) -> Path:
        repo, _ = python_repo(self.tmp, with_origin=True)
        commit(repo)
        push(repo)
        return repo

    def _state(self, path: Path) -> Dict[str, Any]:
        return json.loads(path.read_text())

    def test_claim_opens_one_draft_pr_and_refuses_claimed_target(self):
        repo = self._repo()
        state = self.tmp / "gh-state.json"
        env = self._gh_stub(state)
        result = script(
            repo, "fleet.py", "claim", "--target", "src/app.py",
            "--stream", "A", "--json", env=env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(data["target"], "src/app.py")
        current = self._state(state)
        creates = [c for c in current["calls"] if c[:2] == ["pr", "create"]]
        self.assertEqual(len(creates), 1)
        self.assertIn("--draft", creates[0])
        pr = current["prs"][0]
        self.assertTrue(pr["draft"])
        self.assertIn("src/app.py", pr["body"])
        self.assertIn(data["composite"], pr["body"])
        self.assertTrue(Path(data["worktree"]).exists())
        remote = git(repo, "ls-remote", "origin", f"refs/heads/{data['branch']}")
        self.assertIn(data["branch"], remote.stdout)

        # A target already in an open PR's files is refused, no new draft.
        seeded = self._state(state)
        seeded["prs"].append({
            "number": 7, "title": "other", "headRefName": "test/other",
            "files": [{"path": "src/app.py"}], "state": "OPEN", "draft": True,
            "body": "",
        })
        state.write_text(json.dumps(seeded))
        refused = script(
            repo, "fleet.py", "claim", "--target", "src/app.py",
            "--stream", "A", "--slug", "second", env=env,
        )
        self.assertNotEqual(refused.returncode, 0)
        creates = [c for c in self._state(state)["calls"] if c[:2] == ["pr", "create"]]
        self.assertEqual(len(creates), 1)
        script(repo, "orchestrator.py", "cleanup-worktree", "--slug", data["slug"], "--force")

    def test_publish_issue_mode_is_idempotent(self):
        repo = self._repo()
        state = self.tmp / "gh-state.json"
        env = self._gh_stub(state)
        report = repo / "engine.json"
        report.write_text(json.dumps({
            "source": "src/app.py",
            "survived": [{
                "file": "src/app.py", "line": 2, "column": 11,
                "original": "    return a == b", "mutated": "    return a != b",
                "description": "equality flip",
            }],
        }))
        args = ("fleet.py", "publish", "--issues-from", str(report), "--json")
        first = script(repo, *args, env=env)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second = script(repo, *args, env=env)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        current = self._state(state)
        self.assertEqual(len(current["issues"]), 1)
        self.assertIn("Mutant-Key:", current["issues"][0]["body"])
        outcomes = json.loads(second.stdout.strip().splitlines()[-1])
        self.assertEqual(outcomes[0]["action"], "existing")
        self.assertEqual(outcomes[0]["number"], current["issues"][0]["number"])

    def test_publish_pr_mode_renders_evidence_and_flips_ready(self):
        repo = self._repo()
        state = self.tmp / "gh-state.json"
        env = self._gh_stub(state)
        seeded = self._state(state)
        seeded["prs"].append({
            "number": 7, "title": "WIP", "headRefName": "test/a-app",
            "files": [], "state": "OPEN", "draft": True, "body": "stub",
        })
        state.write_text(json.dumps(seeded))
        import hashlib

        artifact = {
            "schema": 1, "kind": "mutant", "verdict": "killed",
            "base_ref": "origin/main",
            "base_sha": git(repo, "rev-parse", "HEAD").stdout.strip(),
            "test_cmd": "pytest -q", "pass_to_pass_cmd": "pytest -q",
            "mutant": {
                "file": "src/app.py", "line": 2,
                "original": "    return a == b", "mutated": "    return a != b",
                "description": "equality flip",
                "source_sha256": hashlib.sha256(
                    (repo / "src" / "app.py").read_bytes()
                ).hexdigest(),
            },
            "runs": {
                "red": {"exit": 1, "summary": "1 failed"},
                "green": {"exit": 0, "summary": "1 passed"},
                "pass_to_pass": {"exit": 0, "summary": "1 passed"},
            },
        }
        write(repo / ".autonomous" / "evidence.json", json.dumps(artifact))
        result = script(
            repo, "fleet.py", "publish", "--pr", "7",
            "--title", "test: kill equality mutant in app.py", env=env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        pr = self._state(state)["prs"][0]
        self.assertFalse(pr["draft"])
        self.assertEqual(pr["title"], "test: kill equality mutant in app.py")
        self.assertIn("killed", pr["body"])
        self.assertIn(artifact["base_sha"], pr["body"])

        # Stale evidence must never flip a draft ready.
        seeded = self._state(state)
        seeded["prs"][0]["draft"] = True
        state.write_text(json.dumps(seeded))
        write(repo / "src" / "app.py", "def f(a, b):\n    return bool(a == b)\n")
        refused = script(repo, "fleet.py", "publish", "--pr", "7", env=env)
        self.assertNotEqual(refused.returncode, 0)
        self.assertTrue(self._state(state)["prs"][0]["draft"])

    def test_claim_cleans_up_worktree_when_push_fails(self):
        repo, origin = python_repo(self.tmp, name="claim-push-fail", with_origin=True)
        commit(repo)
        push(repo)
        hook = origin / "hooks" / "pre-receive"
        write(hook, "#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        state = self.tmp / "gh-state.json"
        env = self._gh_stub(state)
        result = script(
            repo, "fleet.py", "claim", "--target", "src/app.py",
            "--stream", "A", "--json", env=env,
        )
        self.assertNotEqual(result.returncode, 0)
        calls = self._state(state)["calls"]
        self.assertEqual([c for c in calls if c[:2] == ["pr", "create"]], [])
        self.assertFalse((self.tmp / "claim-push-fail-a-app").exists())
        registry = repo / ".git" / "autonomous-worktrees.json"
        if registry.exists():
            self.assertNotIn("a-app", registry.read_text())
        self.assertEqual(git(repo, "branch", "--list", "*a-app*").stdout.strip(), "")

    def test_publish_issue_mode_links_claim_pr(self):
        repo = self._repo()
        state = self.tmp / "gh-state.json"
        env = self._gh_stub(state)
        seeded = self._state(state)
        seeded["prs"].append({
            "number": 7, "title": "WIP: A (Mutation) claim src/app.py",
            "headRefName": "test/a-app", "files": [], "state": "OPEN",
            "draft": True,
            "body": "## Claim (WIP)\n\nTarget: `src/app.py` · Stream: A (Mutation) · "
                    "Composite: `abc:def`\n",
        })
        state.write_text(json.dumps(seeded))
        report = repo / "engine.json"
        report.write_text(json.dumps({
            "source": "src/app.py",
            "survived": [{
                "file": "src/app.py", "line": 2, "column": 11,
                "original": "    return a == b", "mutated": "    return a != b",
                "description": "equality flip",
            }],
        }))
        result = script(
            repo, "fleet.py", "publish", "--issues-from", str(report), "--json", env=env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        current = self._state(state)
        self.assertEqual(len(current["issues"]), 1)
        self.assertIn("#7", current["issues"][0]["body"])
        outcomes = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(outcomes[0].get("linked_pr"), 7)

    def test_publish_pr_mode_references_survivor_issue(self):
        repo = self._repo()
        state = self.tmp / "gh-state.json"
        env = self._gh_stub(state)
        seeded = self._state(state)
        seeded["prs"].append({
            "number": 7, "title": "WIP", "headRefName": "test/a-app",
            "files": [], "state": "OPEN", "draft": True, "body": "stub",
        })
        state.write_text(json.dumps(seeded))
        report = repo / "engine.json"
        report.write_text(json.dumps({
            "source": "src/app.py",
            "survived": [{
                "file": "src/app.py", "line": 2, "column": 11,
                "original": "    return a == b", "mutated": "    return a != b",
                "description": "equality flip",
            }],
        }))
        filed = script(repo, "fleet.py", "publish", "--issues-from", str(report),
                       "--json", env=env)
        self.assertEqual(filed.returncode, 0, filed.stdout + filed.stderr)
        issue_number = self._state(state)["issues"][0]["number"]

        import hashlib

        artifact = {
            "schema": 1, "kind": "mutant", "verdict": "killed",
            "base_ref": "origin/main",
            "base_sha": git(repo, "rev-parse", "HEAD").stdout.strip(),
            "test_cmd": "pytest -q", "pass_to_pass_cmd": "pytest -q",
            "mutant": {
                "file": "src/app.py", "line": 2,
                "original": "    return a == b", "mutated": "    return a != b",
                "description": "equality flip",
                "source_sha256": hashlib.sha256(
                    (repo / "src" / "app.py").read_bytes()
                ).hexdigest(),
            },
            "runs": {
                "red": {"exit": 1, "summary": "1 failed"},
                "green": {"exit": 0, "summary": "1 passed"},
                "pass_to_pass": {"exit": 0, "summary": "1 passed"},
            },
        }
        write(repo / ".autonomous" / "evidence.json", json.dumps(artifact))
        result = script(
            repo, "fleet.py", "publish", "--pr", "7",
            "--title", "test: kill equality mutant in app.py", env=env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        pr = self._state(state)["prs"][0]
        self.assertFalse(pr["draft"])
        self.assertIn(f"#{issue_number}", pr["body"])


class StreamBScanTests(RepoTestCase):
    """Issue #17: Stream B gets a 0-token shortlist, not a full file read."""

    def test_finds_seeded_smells_in_fixture(self):
        repo, _ = python_repo(self.tmp)
        fixture = SKILL_ROOT / "evals" / "fixtures" / "vacuous-tests" / "tests" / "test_import.py"
        result = script(repo, "stream_b_scan.py", str(fixture), "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        findings = json.loads(result.stdout)
        by_kind = {(f["kind"], f["line"]) for f in findings}
        self.assertIn(("no-assertion", 6), by_kind)
        self.assertIn(("sleeps", 14), by_kind)
        self.assertIn(("vacuous-assert", 15), by_kind)

    def test_clean_test_file_has_no_findings(self):
        repo, _ = python_repo(self.tmp)
        clean = write(
            repo / "tests" / "test_clean.py",
            "from src.app import f\n\n\ndef test_f():\n"
            "    assert f(1, 1) is True\n    assert f(1, 2) is False\n",
        )
        result = script(repo, "stream_b_scan.py", str(clean), "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), [])


class CandidateOrderingTests(unittest.TestCase):
    def test_priority_paths_rank_in_config_order(self):
        # Later priority_paths entries are lower priority than earlier ones:
        # the order the owner wrote them in is the queue order. Files outside
        # every priority path come last. (Previously the sort key collapsed
        # all priority matches to one bucket and re-sorted alphabetically.)
        # "src/zeta" outranks "src/alpha" only via the config order;
        # alphabetical order would put alpha first, so this fails unless
        # the priority index itself is part of the sort key.
        paths = [
            "src/z_last.py",
            "src/alpha/billing.py",
            "src/zeta/auth.py",
            "lib/legacy.py",
        ]
        ordered = common_module._sorted_by_priority(
            paths, ["src/zeta", "src/alpha"]
        )
        self.assertEqual(
            ordered,
            [
                "src/zeta/auth.py",
                "src/alpha/billing.py",
                "lib/legacy.py",
                "src/z_last.py",
            ],
        )


class DocumentationInvariantTests(unittest.TestCase):
    def test_skill_is_short(self):
        lines = (SKILL_ROOT / "SKILL.md").read_text().splitlines()
        self.assertLessEqual(len(lines), 200, f"SKILL.md has {len(lines)} lines")

    def test_skill_routes_a_toolbox_not_a_formula(self):
        text = (SKILL_ROOT / "SKILL.md").read_text()
        flat = _flat(text)
        # The invocation bug this skill had: opening the file made the agent
        # jump straight into mutation testing. The router must present the
        # toolbox, adapt the plan, and ask the owner instead.
        self.assertNotIn("Two streams are active", flat)
        self.assertIn("toolbox of passes", flat)
        self.assertIn("ask the owner what to improve", flat)
        self.assertIn("Single session (default)", flat)
        self.assertIn("Fleet mode (opt-in)", flat)
        # Every tool row in the table must name its module or its lens home.
        for module in ("docs_truth.py", "mutation_engine.py", "stream_b_scan.py",
                       "repair.py", "flakes.py", "perf_gate.py", "advisory.py",
                       "simplify_gate.py", "fuzz_shrink.py", "meta.py"):
            self.assertIn(module, text, module)
        self.assertIn("references/10-toolbox.md", text)

    def test_no_removed_terms(self):
        files = [SKILL_ROOT / "SKILL.md", SKILL_ROOT / "README.md"]
        files += sorted((SKILL_ROOT / "references").glob("*.md"))
        for path in files:
            text = path.read_text()
            self.assertNotIn("IN_PROGRESS", text, str(path))
            self.assertNotIn("opencode-watchdog", text, str(path))
        for path in sorted((SKILL_ROOT / "scripts" / "autonomous").glob("*.py")):
            text = path.read_text()
            self.assertNotIn("shell=True", text, str(path))
            self.assertNotIn("chesssrs", text.lower(), str(path))
            self.assertNotIn("cassandre", text.lower(), str(path))
            self.assertNotIn("fvm", text.lower(), str(path))

    def test_no_duplicate_top_level_scripts(self):
        for name in ("fleet.py", "ledger.py", "mutation_engine.py", "orchestrator.py"):
            self.assertFalse((SKILL_ROOT / "scripts" / name).exists(), name)

    def test_referenced_files_exist(self):
        for relative in (
            "references/01-mutation-engine.md",
            "references/02-ledger-and-dedup.md",
            "references/03-streams-and-triage.md",
            "references/04-workflow-and-roles.md",
            "references/05-unattended-operation.md",
            "references/07-moving-main.md",
            "references/08-recursive-self-improvement.md",
            "references/09-session-history-review.md",
            "references/10-toolbox.md",
            "references/harness-adapters.md",
            "references/gates-and-vocabulary.md",
            "references/stack-adapters.md",
            "references/examples.md",
            "assets/watchdog.sh",
            "assets/snapshot.sh",
            "assets/orchestrator-watchdog.service",
            "assets/orchestrator-watchdog.timer",
            "assets/orchestrator-snapshot.service",
            "assets/orchestrator-snapshot.timer",
            "assets/wake.sh",
            "assets/orchestrator-wake.service",
            "assets/orchestrator-wake.timer",
            "assets/install-watchdog.sh",
            "assets/gitattributes.fragment.txt",
            "assets/gitignore.fragment.txt",
            "scripts/autonomous/fleet.py",
            "SPEC.md",
        ):
            self.assertTrue((SKILL_ROOT / relative).exists(), relative)

    def test_skill_references_its_spec(self):
        text = (SKILL_ROOT / "SKILL.md").read_text()
        self.assertIn("`SPEC.md`", text)


class SpecContractTests(unittest.TestCase):
    """SPEC.md is the authoritative rule -> enforcement -> proof map.

    Every row must name at least one enforcement point (a real file) and at
    least one proof (a named test that would fail if the rule broke). A rule
    with no enforcement or no failing test is exactly the failure mode the
    spec-audit pass exists to catch — the skill's own spec must not have it.
    """

    def test_spec_rows_name_real_enforcement_and_proofs(self):
        import re

        text = (SKILL_ROOT / "SPEC.md").read_text()
        rows = []
        for line in text.splitlines():
            if not line.startswith("| `R-"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) >= 3:
                rows.append((cells[0], cells[1], cells[2]))
        self.assertGreaterEqual(len(rows), 10, "SPEC.md lost its rule rows")
        repo_root = SKILL_ROOT.parents[1]
        tests_blob = "\n".join(
            p.read_text() for p in sorted((SKILL_ROOT / "tests").glob("test_*.py"))
        )
        for rule, enforcement, proof in rows:
            # A cell names a surface either in backticks (skill-relative) or
            # as plain text (repo-level paths — check_skills.py resolves
            # backticked paths against the skill, so repo paths stay plain).
            def tokens(cell: str) -> list:
                found = re.findall(r"`([^`]+)`", cell)
                found += re.findall(r"(?<![\w./`])((?:scripts|\.github)/[\w./-]+)", cell)
                return found

            enforcement_tokens = tokens(enforcement)
            proof_tokens = tokens(proof)
            self.assertTrue(
                enforcement_tokens, f"{rule}: no enforcement point named"
            )
            for token in enforcement_tokens:
                self.assertTrue(
                    (SKILL_ROOT / token).exists() or (repo_root / token).exists(),
                    f"{rule}: enforcement target does not exist: {token}",
                )
            self.assertTrue(proof_tokens, f"{rule}: no proof named")
            for token in proof_tokens:
                if "/" in token or token.endswith(
                    (".py", ".sh", ".yml", ".json", ".md", ".txt")
                ):
                    self.assertTrue(
                        (SKILL_ROOT / token).exists() or (repo_root / token).exists(),
                        f"{rule}: proof file does not exist: {token}",
                    )
                else:
                    self.assertIn(token, tests_blob, f"{rule}: proof test not found: {token}")


class MutationMeasurementContractTests(unittest.TestCase):
    """The measurement contract: the engine's blind shapes, the two flags that
    silently change a number, and the oracle set the kill criterion names.

    Red before this change: none of it was written down, so a round could
    report a clean kill rate while a third of the real gaps sat untouched.
    """

    def setUp(self) -> None:
        self.engine = _flat(
            (SKILL_ROOT / "references" / "01-mutation-engine.md").read_text(encoding="utf-8")
        )
        self.protocol = _flat(
            (SKILL_ROOT / "references" / "04-workflow-and-roles.md").read_text(
                encoding="utf-8"
            )
        )

    def test_the_blind_shapes_are_named(self) -> None:
        for shape in (
            "a plain field copy",
            "a dropped argument",
            "a swapped branch",
            "a changed literal",
            "a different separator",
        ):
            self.assertIn(shape, self.engine, shape)

    def test_a_hand_curated_supplement_is_required(self) -> None:
        self.assertIn("hand-curated supplement", self.engine)
        self.assertIn("kill criterion", self.engine)

    def test_the_timeout_flag_is_a_reporting_hazard(self) -> None:
        self.assertIn("counted as a kill", self.engine)
        self.assertIn("timeout-counts-as excluded", self.engine)

    def test_the_sampling_flag_is_a_reporting_hazard(self) -> None:
        self.assertIn("samples", self.engine)
        self.assertIn("max-mutants", self.engine)

    def test_the_protocol_names_every_oracle_file(self) -> None:
        self.assertIn("every oracle file", self.protocol.lower())
        # the phrase lives in a shell comment, so assert on the part after it
        self.assertIn("a second oracle may already kill it", self.protocol)


class LandingSeveralOwnPRsTests(unittest.TestCase):
    """The recipe for landing several of the orchestrator's own PRs that touch
    the same file.

    Red before this change: the recipe existed nowhere, and the session it
    came from produced a conflict resolution that did not even parse.
    """

    def setUp(self) -> None:
        self.protocol = _flat_landing(
            (SKILL_ROOT / "references" / "04-workflow-and-roles.md").read_text(
                encoding="utf-8"
            )
        )

    def test_the_rule_is_stated_in_its_general_form(self) -> None:
        self.assertIn("preserve the file's structure", self.protocol.lower())
        self.assertIn("in any repo", self.protocol.lower())

    def test_the_rebuild_rule_is_stated(self) -> None:
        self.assertIn("by rebuilding the file", self.protocol.lower())
        self.assertIn("rather than to keep both", self.protocol.lower())

    def test_the_trailing_construct_rule_is_stated(self) -> None:
        self.assertIn("single trailing construct exactly once", self.protocol.lower())

    def test_the_line_oriented_exception_is_stated(self) -> None:
        self.assertIn("concatenation is", self.protocol.lower())

    def test_the_verification_step_is_stated(self) -> None:
        self.assertIn("verify the merged tree before pushing", self.protocol.lower())

    def test_the_conflict_marker_check_is_stated(self) -> None:
        self.assertIn("git grep", self.protocol)
        self.assertIn("conflict markers", self.protocol.lower())


class EnvironmentIndependenceTests(unittest.TestCase):
    """The suite must give the same result on every host.

    Red before this change: the installer test inherited ORCH_STATE_DIR from
    the runner's environment, wrote into the real state directory on a fleet
    host, and failed there while passing in CI.
    """

    def setUp(self) -> None:
        self.streams = _flat_landing(
            (SKILL_ROOT / "references" / "03-streams-and-triage.md").read_text(
                encoding="utf-8"
            )
        )
        self.unattended = _flat_landing(
            (SKILL_ROOT / "references" / "05-unattended-operation.md").read_text(
                encoding="utf-8"
            )
        )

    def test_the_stream_b_question_exists(self) -> None:
        self.assertIn("depend on the machine's environment", self.streams.lower())

    def test_the_controlled_environment_rule_is_stated(self) -> None:
        self.assertIn("controlled environment", self.streams.lower())
        self.assertIn("cleared explicitly", self.streams.lower())

    def test_the_host_evidence_rule_is_stated(self) -> None:
        self.assertIn("evidence about the host", self.streams.lower())

    def test_the_fleet_suite_rule_is_stated(self) -> None:
        self.assertIn("same result everywhere", self.unattended.lower())
        self.assertIn("temporary tree", self.unattended.lower())


class SkillImprovementCadenceTests(unittest.TestCase):
    """The cadence: idle capacity improves the skill, from the fleet's own
    session history — and the method that turns a session's history into a
    proposal.

    Red before this change: nothing in the skill said when to look at the
    fleet's own history, what a proposal has to carry, or what the cadence
    does not authorise. The one session that produced a long list of rule
    misfires found them by hand, at the end — and missed three it had
    already written down and addressed to the skill.
    """

    def setUp(self) -> None:
        self.unattended = _flat_landing(
            (SKILL_ROOT / "references" / "05-unattended-operation.md").read_text(
                encoding="utf-8"
            )
        )
        self.protocol = _flat_landing(
            (SKILL_ROOT / "references" / "04-workflow-and-roles.md").read_text(
                encoding="utf-8"
            )
        )
        self.review = _flat_landing(
            (SKILL_ROOT / "references" / "09-session-history-review.md").read_text(
                encoding="utf-8"
            )
        )
        self.skill = _flat_landing((SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8"))

    def test_the_cadence_is_stated(self) -> None:
        self.assertIn("spare capacity improves the skill", self.unattended.lower())
        self.assertIn("10% of the remaining time", self.unattended)
        self.assertIn("at least once a week", self.unattended)

    def test_the_cadence_is_bound_to_an_empty_queue(self) -> None:
        self.assertIn("nothing meaningful left", self.unattended)
        self.assertIn("spare capacity", self.unattended.lower())

    def test_the_cadence_authorises_no_merge(self) -> None:
        # the rule must not be readable as permission to land something
        self.assertIn("does not authorise a merge", self.unattended.lower())
        self.assertIn("never produces a merge", self.protocol.lower())

    def test_the_cadence_excludes_the_referee(self) -> None:
        for surface in ("five laws", "stream contract", "eval sets", "tier floors"):
            self.assertIn(surface, self.unattended.lower(), surface)
        self.assertIn("merge policy", self.unattended.lower())

    def test_the_cadence_is_not_the_skill_rounds_on_itself(self) -> None:
        self.assertIn(
            "running this skill's rounds against this skill's own code",
            self.review.lower(),
        )
        self.assertIn("not a second stream l", self.review.lower())

    def test_the_evidence_surface_is_named(self) -> None:
        for source in (
            "journal",
            "compaction-proof state file",
            "nudge transcripts",
            "watchdog log",
            "snapshot accumulator",
            "ledger",
            "pr and issue timeline",
        ):
            self.assertIn(source, self.review.lower(), source)

    def test_a_proposal_carries_evidence_scope_and_the_rule(self) -> None:
        self.assertIn("evidence", self.review.lower())
        self.assertIn("scope", self.review.lower())
        self.assertIn("the rule it would have prevented", self.review.lower())

    def test_the_already_covered_case_is_required(self) -> None:
        # "the rule existed and was not followed" is a different finding from
        # "no rule existed", and the review has to be able to say which.
        self.assertIn("already covered?", self.review.lower())
        self.assertIn("the rule existed and was not followed", self.review.lower())

    def test_a_referee_change_is_an_issue_not_a_pr(self) -> None:
        self.assertIn("never a pr the fleet can merge", self.review.lower())

    def test_the_orchestrator_duty_is_in_the_protocol(self) -> None:
        self.assertIn("spends idle capacity on the skill itself", self.protocol.lower())
        self.assertIn("references/09-session-history-review.md", self.protocol)

    def test_the_summary_reaches_the_cadence(self) -> None:
        # SKILL.md is the injected summary: a duty that lives only in a
        # reference file is a duty that never fires.
        self.assertIn(
            "idle capacity turns the fleet's own history into skill proposals",
            self.skill.lower(),
        )


class FleetStateDurabilityTests(unittest.TestCase):
    """The disk, the journal write, and where the state directory lives.

    Red before this change: the fleet's journal carried a lesson addressed
    to the skill — a worktree is ~400 MB, twenty of them fill a disk, and
    cleanup is part of the round — and it never reached the skill. The same
    session lost two journal appends that reported success, and the skill's
    rule covered only failed *reads*.
    """

    def setUp(self) -> None:
        self.unattended = _flat_landing(
            (SKILL_ROOT / "references" / "05-unattended-operation.md").read_text(
                encoding="utf-8"
            )
        )

    def test_the_disk_is_part_of_the_machine_budget(self) -> None:
        self.assertIn("the disk is part of the machine budget", self.unattended.lower())
        self.assertIn("budget the worktrees", self.unattended.lower())

    def test_the_cleanup_is_part_of_the_round(self) -> None:
        self.assertIn("clean up as part of the round", self.unattended.lower())

    def test_the_journal_write_is_atomic_and_verified(self) -> None:
        self.assertIn("write the entry to a temp file", self.unattended.lower())
        self.assertIn("read the tail back", self.unattended.lower())

    def test_the_state_dir_is_outside_the_harness_tree(self) -> None:
        self.assertIn("outside any tree the harness manages", self.unattended.lower())

class StreamSimplifyTests(RepoTestCase):
    """Stream J (#25): finder candidates only; mutation-score oracle decides."""

    def fixture(self) -> Path:
        return SKILL_ROOT / "evals" / "fixtures" / "dead-code-python"

    def test_finder_flags_unreachable_and_zero_fanin(self):
        repo, _ = python_repo(self.tmp)
        fixture = self.fixture()
        result = script(
            repo,
            "simplify_gate.py",
            "finder",
            str(fixture / "src" / "dead.py"),
            "--scan-root",
            str(fixture),
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        findings = json.loads(result.stdout)
        by_kind = {(item["kind"], item["line"]) for item in findings}
        self.assertIn(("unreachable", 5), by_kind)
        self.assertIn(("zero-fanin-private", 8), by_kind)

    def test_finder_clean_and_dynamic_files_have_no_candidates(self):
        repo, _ = python_repo(self.tmp)
        fixture = self.fixture()
        for filename in ("live.py", "registry.py"):
            result = script(
                repo,
                "simplify_gate.py",
                "finder",
                str(fixture / "src" / filename),
                "--scan-root",
                str(fixture),
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout), [], filename)

    def _report(self, killed: int, survived: int) -> dict:
        killed_entries = [
            {
                "line": 2,
                "original": "    return a == b",
                "mutated": "    return a != b",
                "description": "invert equality",
            }
        ] * killed
        survived_entries = [
            {
                "line": 5,
                "original": "    return value + 1",
                "mutated": "    return value - 1",
                "description": "invert addition",
            }
        ] * survived
        total = killed + survived
        return {
            "source": "src/app.py",
            "test_cmd": "python -m unittest tests.test_app",
            "counts": {
                "total": total,
                "killed": killed,
                "survived": survived,
                "timeout": 0,
                "uncompilable": 0,
            },
            "kill_rate": round(killed / total, 4) if total else 0.0,
            "timeout_counts_as": "survived",
            "mutants": {"killed": killed_entries, "survived": survived_entries},
        }

    def test_compare_refuses_lower_mutation_score(self):
        repo, _ = python_repo(self.tmp)
        before = write(
            repo / "before.json", json.dumps(self._report(killed=4, survived=1))
        )
        after = write(repo / "after.json", json.dumps(self._report(killed=2, survived=3)))
        result = script(
            repo,
            "simplify_gate.py",
            "compare",
            "--before-json",
            str(before),
            "--after-json",
            str(after),
            "--json",
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["verdict"], "refused")
        self.assertTrue(any("score" in reason for reason in payload["reasons"]))

    def test_finder_coverage_marks_missing_and_suppresses_executed_body(self):
        repo, _ = python_repo(self.tmp)
        fixture = self.fixture()
        target = fixture / "src" / "dead.py"

        def run_with_coverage(executed: list[int], missing: list[int]):
            coverage = write(
                repo / "coverage.json",
                json.dumps(
                    {
                        "files": {
                            str(target): {
                                "executed_lines": executed,
                                "missing_lines": missing,
                            }
                        }
                    }
                ),
            )
            result = script(
                repo,
                "simplify_gate.py",
                "finder",
                str(target),
                "--scan-root",
                str(fixture),
                "--coverage-json",
                str(coverage),
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)

        missing = run_with_coverage(executed=[1, 2, 3, 4, 8], missing=[5, 9])
        helper = next(item for item in missing if item["kind"] == "zero-fanin-private")
        self.assertIs(helper["zero_coverage"], True)

        executed = run_with_coverage(executed=[1, 2, 3, 4, 8, 9], missing=[5])
        self.assertNotIn(
            "zero-fanin-private", {item["kind"] for item in executed}
        )

    def test_ledger_stream_j_selects_configured_source(self):
        repo, _ = python_repo(self.tmp)
        commit(repo)
        result = script(repo, "ledger.py", "next", "--stream", "J", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "src/app.py",
            [item["target"] for item in json.loads(result.stdout)["candidates"]],
        )

    def test_stream_for_target_recognizes_j_only_config(self):
        cfg = {
            "candidates": {
                "A": {"include": [], "exclude": []},
                "B": {"include": [], "exclude": []},
                "J": {"include": ["src/**/*.py"], "exclude": []},
            }
        }
        self.assertEqual(ledger_module._stream_for_target(cfg, "src/app.py"), "J")

    def test_oracle_accepts_clean_dead_deletion(self):
        repo, _ = python_repo(self.tmp)
        write(
            repo / "src" / "app.py",
            "def f(a, b):\n"
            "    return a == b\n"
            "    return False\n"
            "\n\n"
            "def _unused_helper(value):\n"
            "    return value + 1\n",
        )
        commit(repo, "seed dead code")
        write(repo / "src" / "app.py", "def f(a, b):\n    return a == b\n")
        test_cmd = (
            f'{PYTHON} -c "from src.app import f; '
            'assert f(1, 1) is True and f(1, 2) is False"'
        )
        result = script(
            repo,
            "simplify_gate.py",
            "oracle",
            "--source",
            "src/app.py",
            "--base",
            "HEAD",
            "--test-cmd",
            test_cmd,
            "--max-mutants",
            "50",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["verdict"], "accepted")
        self.assertGreaterEqual(
            payload["after"]["kill_rate"], payload["before"]["kill_rate"]
        )
        self.assertEqual(payload["deleted_test_files"], [])

    def test_oracle_refuses_deleting_the_only_test_file(self):
        repo, _ = python_repo(self.tmp)
        write(
            repo / "src" / "app.py",
            "def f(a, b):\n"
            "    return a == b\n"
            "    return False\n",
        )
        commit(repo, "seed dead code with test")

        write(repo / "src" / "app.py", "def f(a, b):\n    return a == b\n")
        (repo / "tests" / "test_app.py").unlink()
        test_cmd = (
            f'{PYTHON} -c "from src.app import f; '
            'assert f(1, 1) is True and f(1, 2) is False"'
        )
        result = script(
            repo,
            "simplify_gate.py",
            "oracle",
            "--source",
            "src/app.py",
            "--base",
            "HEAD",
            "--test-cmd",
            test_cmd,
            "--max-mutants",
            "50",
            "--json",
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["verdict"], "refused")
        self.assertEqual(payload["deleted_test_files"], ["tests/test_app.py"])


if __name__ == "__main__":
    unittest.main()



class VerdictReliabilityTests(RepoTestCase):
    """Issue #16: baseline-relative timeouts, survivor confirmation, verdict cache."""

    def make_repo(self):
        repo, _ = python_repo(self.tmp)
        write(repo / "src" / "app.py", "def f(a, b):\n    return a == b\n")
        commit(repo)
        return repo

    def test_baseline_relative_timeout_computation(self):
        # Unit: max(5x baseline, 20s) when no explicit --timeout was given;
        # an explicit flag is preserved verbatim ("flags preserved" in #16).
        self.assertEqual(engine_module._effective_timeout(13.0, None), 65.0)
        self.assertEqual(engine_module._effective_timeout(1.0, None), 20.0)
        self.assertEqual(engine_module._effective_timeout(0.2, None), 20.0)
        self.assertEqual(engine_module._effective_timeout(13.0, 0.3), 0.3)

    def test_baseline_relative_timeout_recorded_in_json(self):
        repo = self.make_repo()
        output = self.tmp / "t.json"
        result = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} -c pass", "--max-mutants", "2",
            "--output-json", str(output),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(output.read_text())
        self.assertEqual(data["timeout_mode"], "baseline-relative")
        # Room: local baselines are seconds; the 20s floor is the effective default.
        self.assertEqual(data["timeout_effective"], 20.0)
        self.assertEqual(data["counts"]["timeout"], 0)

    def test_flaky_suite_does_not_yield_confirmed_survivor(self):
        # Deterministic stand-in for "passes/fails alternating" (#16 item 3):
        # call 1 (baseline, clean) passes; later invocations on MUTATED source
        # pass too (fabricating a survivor); later invocations on CLEAN source
        # fail — the suite cannot be trusted on clean source, so the survivor
        # verdict proves nothing and must not be reported as work.
        repo = self.make_repo()
        (repo / "orig.txt").write_text((repo / "src" / "app.py").read_text())
        stub = write(
            repo / "flake.py",
            "import pathlib, sys\n"
            "calls = pathlib.Path('calls.txt')\n"
            "count = int(calls.read_text() or '0') + 1 if calls.exists() else 1\n"
            "calls.write_text(str(count))\n"
            "actual = pathlib.Path('src/app.py').read_text()\n"
            "if count == 1:\n"
            "    sys.exit(0)\n"
            "if actual == pathlib.Path('orig.txt').read_text():\n"
            "    sys.exit(1)\n"
            "sys.exit(0)\n",
        )
        output = self.tmp / "flake.json"
        result = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} {stub}", "--max-mutants", "1",
            "--output-json", str(output),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(output.read_text())
        self.assertEqual(data["counts"]["survived"], 0)
        self.assertEqual(data["counts"]["unverified"], 1)
        self.assertEqual(data["counts"]["killed"], 0)
        self.assertEqual(len(data["unverified"]), 1)

    def test_confirm_survivors_default_positive_path(self):
        # Healthy suite: mutant run passes AND clean-source confirm passes ->
        # the survivor is confirmed and reported as work. Default N=1.
        repo = self.make_repo()
        output = self.tmp / "confirm.json"
        result = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} -c pass", "--max-mutants", "2",
            "--output-json", str(output),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(output.read_text())
        self.assertGreaterEqual(data["counts"]["survived"], 1)
        self.assertEqual(data["counts"]["unverified"], 0)
        self.assertEqual(data["confirm_survivors"], 1)

    def test_confirm_survivors_zero_restores_legacy_behavior(self):
        # --confirm-survivors 0: voided confirmation; a survivor stands on the
        # first pass even when clean source cannot reproduce a pass (legacy).
        repo = self.make_repo()
        (repo / "orig.txt").write_text((repo / "src" / "app.py").read_text())
        stub = write(
            repo / "flake0.py",
            "import pathlib, sys\n"
            "calls = pathlib.Path('calls0.txt')\n"
            "count = int(calls.read_text() or '0') + 1 if calls.exists() else 1\n"
            "calls.write_text(str(count))\n"
            "actual = pathlib.Path('src/app.py').read_text()\n"
            "if count == 1:\n"
            "    sys.exit(0)\n"
            "if actual == pathlib.Path('orig.txt').read_text():\n"
            "    sys.exit(1)\n"
            "sys.exit(0)\n",
        )
        output = self.tmp / "confirm0.json"
        result = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} {stub}", "--max-mutants", "1",
            "--confirm-survivors", "0",
            "--output-json", str(output),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(output.read_text())
        self.assertEqual(data["counts"]["survived"], 1)

    def test_verdict_cache_hits_and_bypass(self):
        repo = self.make_repo()
        output_a = self.tmp / "cache1.json"
        output_b = self.tmp / "cache2.json"
        output_c = self.tmp / "cache3.json"
        argv = [
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} -c pass", "--max-mutants", "3",
        ]
        first = script(*argv, "--output-json", str(output_a))
        self.assertEqual(first.returncode, 0, first.stderr)
        data_a = json.loads(output_a.read_text())
        self.assertEqual(data_a["cache_hits"], 0)
        self.assertTrue((repo / ".autonomous" / "mutant-cache.json").exists())

        second = script(*argv, "--output-json", str(output_b))
        self.assertEqual(second.returncode, 0, second.stderr)
        data_b = json.loads(output_b.read_text())
        # Same repo state, same test command -> every verdict is a cache hit,
        # and the reported verdict tables match the earlier run exactly
        # ("assert on reported counts, not wall time" per #16).
        self.assertEqual(data_b["cache_hits"], data_a["counts"]["total"])
        self.assertEqual(data_b["counts"], data_a["counts"])

        third = script(*argv, "--no-cache", "--output-json", str(output_c))
        self.assertEqual(third.returncode, 0, third.stderr)
        data_c = json.loads(output_c.read_text())
        self.assertEqual(data_c["cache_hits"], 0)
        self.assertEqual(data_c["counts"], data_a["counts"])

    def test_backend_auto_notes_fallback(self):
        repo = self.make_repo()
        output = self.tmp / "backend.json"
        auto = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} -c pass", "--max-mutants", "2",
            "--backend", "auto", "--output-json", str(output),
        )
        self.assertEqual(auto.returncode, 0, auto.stderr)
        self.assertIn("sregex", auto.stdout)
        data = json.loads(output.read_text())
        self.assertEqual(data["backend"], "sregex")

        explicit = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} -c pass", "--max-mutants", "2",
            "--backend", "sregex",
        )
        self.assertEqual(explicit.returncode, 0, explicit.stderr)

        # The stryker adapter ships in the merged tree, so selecting it is no
        # longer a usage error; an unconfigured run fails on its missing
        # report instead (env error, exit 3).
        unconfigured = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} -c pass", "--backend", "stryker",
        )
        self.assertEqual(unconfigured.returncode, 3, unconfigured.stderr)
        self.assertIn(
            "stryker report not found", unconfigured.stderr + unconfigured.stdout
        )


@unittest.skipUnless(
    all(shutil.which(tool) for tool in ("bash", "flock", "timeout", "stat", "date")),
    "watchdog shell dependencies are not installed",
)
class EvalHarnessTests(unittest.TestCase):
    """evals/run.py: deterministic halves plus the red-proof they can fail.

    The harness checks this repo's fixtures in temp copies; these tests are
    its own CI contract: green on the current fixture set, red under a
    deliberately impossible expectation (a harness that cannot fail proves
    nothing), and setup commands that actually run from a git clone (the
    empty-dir `cp -r <skill>/scripts/autonomous ...` trap fixed in evals.json
    v2's mkdir -p lines).
    """

    def _harness(self, *flags: str) -> subprocess.CompletedProcess:
        import os
        import tempfile as _tempfile

        script_path = SKILL_ROOT / "evals" / "run.py"
        with _tempfile.TemporaryDirectory() as tmp_cwd:
            proc = subprocess.run(
                [str(PYTHON), str(script_path), *flags],
                capture_output=True,
                text=True,
                cwd=tmp_cwd,
                env=dict(os.environ),
            )
        return proc

    def test_deterministic_case_green_and_recorded(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            name = Path(tmp) / "results.json"
            res = self._harness(
                "--deterministic",
                "--case", "E7-3-dedup-second-run",
                "--record-file", str(name),
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            payload = json.loads(name.read_text())
            case = next(c for c in payload["cases"] if c["id"] == "E7-3-dedup-second-run")
            self.assertEqual(case["status"], "pass", case.get("failures"))
            self.assertTrue(all(c["status"] == "pass" for c in case["checks"]))

    def test_negative_control_is_red(self):
        res = self._harness("--negative-control", "--case", "E7-8-protected-row")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("RED-PROOF", res.stdout)

    def test_agent_only_case_still_runs_setup(self):
        res = self._harness("--deterministic", "--case", "E7-9-cold-start")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("agent-only", res.stdout)

    def test_deterministic_runs_without_git_identity(self):
        # CI/containers may have no git identity at all; the harness pins a
        # throwaway one for its throwaway fixture repos instead of failing
        # every setup line with "Author identity unknown".
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as bare_home:
            env = dict(os.environ)
            env["HOME"] = bare_home
            env["GIT_CONFIG_GLOBAL"] = "/dev/null"
            env["GIT_CONFIG_SYSTEM"] = "/dev/null"
            proc = subprocess.run(
                [
                    str(PYTHON),
                    str(SKILL_ROOT / "evals" / "run.py"),
                    "--deterministic",
                    "--case", "E7-3-dedup-second-run",
                ],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("1 passed", proc.stdout)


class FlakeStreamTests(RepoTestCase):
    """Stream F (issue #21): CI-history finder + N-run oracle + fixture.

    The finder is 0-token math (static flake-idiom scan plus mixed-outcome
    history mining) and can only EMIT candidates; the N-run oracle is the
    verdict machine (0/N demanded after a fix; "passed twice" is not an
    oracle). The fixture ships a deterministic pseudo-flake: a counter file
    seeds the failure for exactly 3 of every 10 runs (~30%, no wall clock),
    so CI itself stays stable.
    """

    FIXTURE = SKILL_ROOT / "evals" / "fixtures" / "flake-python"

    def finder_run(self, *args: str) -> subprocess.CompletedProcess:
        return script(SKILL_ROOT, "flakes.py", *args)

    def oracle_run(self, test_script: Path, runs: int, *extra: str) -> subprocess.CompletedProcess:
        return script(
            SKILL_ROOT, "flakes.py", "oracle", "--test-script", str(test_script),
            "--runs", str(runs), "--json", *extra,
        )

    def copy_fixture(self) -> Path:
        repo = self.tmp / "repo"
        shutil.copytree(self.FIXTURE, repo)
        return repo

    def test_finder_mines_mixed_outcome_history(self):
        repo = self.copy_fixture()
        res = self.finder_run("finder", "--history", str(repo / "history.json"), "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        payload = json.loads(res.stdout)
        candidates = {c["test"]: c for c in payload["candidates"] if c["kind"] == "history"}
        self.assertIn("tests/test_counter_flake.py", candidates)
        flaky = candidates["tests/test_counter_flake.py"]
        self.assertEqual(set(flaky["outcomes"]), {"pass", "fail"})
        self.assertIn("same_sha", flaky)
        self.assertNotIn("tests/test_counter_fixed.py", candidates)

    def test_finder_zero_false_positives_on_stable_history(self):
        stable = self.tmp / "history-stable.json"
        runs = {"schema": "stream-f/1", "runs": [
            {"sha": f"v{i}", "job": "unit", "labels": {
                "tests/test_counter_fixed.py": "pass"}} for i in range(6)
        ]}
        stable.write_text(json.dumps(runs))
        res = self.finder_run("finder", "--history", str(stable), "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout)["candidates"], [])

    def test_finder_static_scan_flags_flake_idioms_only(self):
        tests_dir = self.tmp / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_waiter.py").write_text(
            "import time\n\ndef test_waiter():\n    time.sleep(2)  # hope it loads\n"
            "    assert True\n"
        )
        (tests_dir / "test_math.py").write_text(
            "def test_math():\n    assert 1 + 1 == 2\n"
        )
        res = self.finder_run("finder", "--paths", str(tests_dir), "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        candidates = json.loads(res.stdout)["candidates"]
        flagged = {c["test"] for c in candidates if c["kind"] == "static"}
        self.assertIn("tests/test_waiter.py", flagged)
        self.assertNotIn("tests/test_math.py", flagged)
        hit_exact = [
            c for c in candidates
            if c["kind"] == "static" and c["test"] == "tests/test_waiter.py"
        ][0]
        self.assertIn("time.sleep", hit_exact["evidence"]["hits"][0])

    def test_oracle_reads_the_pseudo_flake(self):
        repo = self.copy_fixture()
        proc = self.oracle_run(repo / "tests" / "test_counter_flake.py", 10)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        verdict = json.loads(proc.stdout)
        self.assertEqual(verdict["verdict"], "flaky")
        self.assertEqual(verdict["modes"]["isolated"]["counts"]["pass"], 7)
        self.assertEqual(verdict["modes"]["isolated"]["counts"]["fail"], 3)
        self.assertEqual(verdict["modes"]["isolated"]["flake_rate"], 0.3)

    def test_oracle_demands_zero_after_fix(self):
        repo = self.copy_fixture()
        proc = self.oracle_run(repo / "tests" / "test_counter_fixed.py", 20)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        verdict = json.loads(proc.stdout)
        self.assertEqual(verdict["verdict"], "stable")
        self.assertEqual(verdict["modes"]["isolated"]["counts"]["fail"], 0)

    def test_oracle_names_order_dependence(self):
        work = self.tmp / "order"
        (work / "state").mkdir(parents=True)
        # Test fails only when peers ran first (marker file left behind);
        # both the test and the peers command anchor on the oracle's cwd,
        # so the matrix must be invoked with cwd=work (run() below, not
        # support.script(), which pins cwd to the skill root).
        (work / "test_order.py").write_text(
            "import pathlib, sys\n"
            "if pathlib.Path('state/peer-marker').exists():\n"
            "    print('ran after peers'); sys.exit(1)\n"
            "print('clean run')\n"
        )
        peers_cmd = (
            f"{shutil.which('python3')} -c "
            "\"import pathlib; pathlib.Path('state/peer-marker').write_text('x')\""
        )
        proc = run(
            [PYTHON, str(SKILL_ROOT / "scripts" / "autonomous" / "flakes.py"),
             "oracle", "--test-script", str(work / "test_order.py"),
             "--runs", "5", "--json", "--peers-cmd", peers_cmd],
            cwd=work,
        )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        verdict = json.loads(proc.stdout)
        self.assertEqual(verdict["verdict"], "flaky")
        self.assertEqual(verdict["cause_class"], "order-dependence")
        self.assertEqual(verdict["modes"]["isolated"]["counts"]["fail"], 0)
        self.assertGreater(verdict["modes"]["after_peers"]["counts"]["fail"], 0)

    def test_stream_f_unlocked(self):
        res = script(SKILL_ROOT, "ledger.py", "next", "--stream", "F")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("F", common_module.STREAMS)
        self.assertEqual(common_module.STREAMS["F"]["status"], "available")


class DocsTruthStreamTests(unittest.TestCase):
    """Stream I (issue #24): docs & spec truthfulness finder.

    The finder is deterministic and can only EMIT candidates (never edit —
    zero-diff is asserted); the direction call (fix docs vs file a defect
    issue) stays with the owner/agent via git history, and --scaffold-issue
    prints the artifact that carries both side's evidence. The oracle is the
    rerun: findings must flip to a clean pass after a docs-only fix.
    """

    FIXTURE = SKILL_ROOT / "evals" / "fixtures" / "docs-drift-python"

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def check(self, cwd: Path, *args: str) -> subprocess.CompletedProcess:
        return run(
            [PYTHON, str(SKILL_ROOT / "scripts" / "autonomous" / "docs_truth.py"), "check", *args],
            cwd=cwd,
        )

    def copy_fixture(self) -> Path:
        repo = self.tmp / "repo"
        shutil.copytree(self.FIXTURE, repo)
        return repo

    def tree_digest(self, root: Path) -> str:
        import hashlib

        parts = []
        for path in sorted(root.rglob("*")):
            rel = str(path.relative_to(root))
            if ".git/" in rel:
                continue
            data = path.read_bytes() if path.is_file() else b"<dir>"
            parts.append(f"{rel}:{hashlib.sha256(data).hexdigest()}")
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()

    def test_symbol_drift_flagged_with_file_and_line(self):
        repo = self.copy_fixture()
        res = self.check(repo, "--json")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        payload = json.loads(res.stdout)
        drift = [c for c in payload["candidates"] if c["kind"] == "symbol-drift"]
        self.assertEqual(len(drift), 1, json.dumps(payload["candidates"])[:400])
        self.assertEqual(drift[0]["file"], "README.md")
        self.assertEqual(drift[0]["name"], "load_table")
        self.assertIn("src/config.py", drift[0]["evidence"]["module_file"])

    def test_fixed_docs_pass_clean(self):
        repo = self.copy_fixture()
        readme = repo / "README.md"
        readme.write_text(readme.read_text().replace("load_table", "load_config"))
        res = self.check(repo, "--json")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(json.loads(res.stdout)["candidates"], [])

    def test_scaffold_issue_artifact_and_zero_diff(self):
        repo = self.copy_fixture()
        before = self.tree_digest(repo)
        res = self.check(repo, "--json", "--scaffold-issue")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        body = res.stderr
        self.assertIn("Stream I candidate issue", body)
        self.assertIn("do not silently", body)
        self.assertIn("README.md", body)
        self.assertEqual(self.tree_digest(repo), before, "the finder must never edit the repo")

    def test_missing_link_flagged_only_when_target_missing(self):
        work = self.tmp / "links"
        (work / "docs").mkdir(parents=True)
        (work / "src").mkdir()
        (work / "src" / "app.py").write_text("def run():\n    return 1\n")
        (work / "docs" / "guide.md").write_text(
            "See [the spec](/SPEC.md) or [deep](../missing/deep.md).\n"  # noqa
        )
        (work / "docs" / "other.md").write_text("")
        # one good one bad, resolved against the doc's own directory
        (work / "README.md").write_text(
            "good: [other](docs/other.md)\nbad: [ghost](docs/ghost.md)\n"
        )
        res = self.check(work, "--json")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        payload = json.loads(res.stdout)
        links = {c["evidence"]["target"]: c for c in payload["candidates"] if c["kind"] == "missing-link"}
        self.assertIn("docs/ghost.md", links)
        self.assertNotIn("docs/other.md", links)
        self.assertNotIn("../missing/deep.md", links or {})

    def test_spec_trace_runs_both_directions_when_spec_present(self):
        work = self.tmp / "spec"
        work.mkdir()
        (work / "SPEC.md").write_text(
            "# SPEC trace\n\n- G05 the referee rule\n- G06 the ledger append rule\n"
        )
        tests_dir = work / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_referee.py").write_text(
            "# covers G05 only\n"
            "def test_referee():\n"
            "    # G05 citation\n"
            "    assert True\n\n"
            "def test_orphan():\n"
            "    # cites Z99 which no spec line defines\n"
            "    assert True\n"
        )
        res = self.check(work, "--spec", str(work / "SPEC.md"),
                         "--paths", str(tests_dir),
                         "--source", str(work / "src"),
                         "--json")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        payload = json.loads(res.stdout)
        spec_findings = {c["name"]: c for c in payload["candidates"] if c["kind"].startswith("spec-")}
        self.assertIn("G06", spec_findings)  # defined in spec, cited by no test
        self.assertIn("Z99", spec_findings)  # cited in tests, defined no spec id
        self.assertNotIn("G05", spec_findings)

    def test_runnable_example_failure_flagged(self):
        work = self.tmp / "runnable"
        (work / "src").mkdir(parents=True)
        (work / "src" / "app.py").write_text("def add(a, b):\n    return a + b\n")
        (work / "README.md").write_text(
            "# Example\n\n"
            "```python  # runnable\n"
            "from src.app import add\n"
            "assert add(1, 2) == 4\n"
            "```\n"
        )
        res = self.check(work, "--json")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        payload = json.loads(res.stdout)
        kinds = {c["kind"] for c in payload["candidates"]}
        self.assertIn("example-failure", kinds)

    def test_dogfood_clean_on_this_repo(self):
        repo_root = SKILL_ROOT.parent.parent
        res = self.check(
            repo_root,
            "--json",
            "--paths", "README.md", "docs",
            "--source", "skills/autonomous-improvement/scripts/autonomous",
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(json.loads(res.stdout)["candidates"], [])


@unittest.skipUnless(
    all(shutil.which(tool) for tool in ("bash", "flock", "timeout", "stat", "date")),
    "watchdog shell dependencies are not installed",
)
class RepairStreamTests(RepoTestCase):
    """Stream E (issue #22): defect-repair intake + red/green oracle.

    The intake is 0-token: bug report files must NAME expected vs actual
    behavior; a report that cannot become a failing test is bounced back
    with the missing facts and the tool leaves the repo untouched.
    verify-red is the un-fakeable anchor: the repro test must be red with
    NOTHING outside tests/ changed vs the base ref; verify-green demands
    the repro green plus at least one minimal fix file, optionally running
    the mutation engine's --diff-base spot-check on the touched lines.
    """

    FIXTURE = SKILL_ROOT / "evals" / "fixtures" / "defect-python"

    REPRO_TEST = [
        "import sys\n",
        "sys.path.insert(0, '')\n",
        "from src.calculator import median\n",
        "assert median([1, 2, 3, 4]) == 2.5, median([1, 2, 3, 4])\n",
        "assert median([7]) == 7\n",
        "print('ok')\n",
    ]

    def repair_run(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return run(
            [PYTHON, str(SKILL_ROOT / "scripts" / "autonomous" / "repair.py"), *args],
            cwd=repo,
        )

    def copy_fixture(self) -> Path:
        repo = self.tmp / "repo"
        shutil.copytree(self.FIXTURE, repo)
        return repo

    def seeded_repo(self) -> Path:
        repo = self.copy_fixture()
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "base: seeded median bug + bug reports")
        # The repro test is written AFTER the base commit so verify-red's
        # tests-only diff actually has the test file in it.
        (repo / "tests").mkdir(exist_ok=True)
        (repo / "tests" / "test_repro_median.py").write_text("".join(self.REPRO_TEST))
        return repo

    def tree_digest(self, repo: Path) -> str:
        import hashlib

        parts = []
        for path in sorted(repo.rglob("*")):
            rel = str(path.relative_to(repo))
            if ".git/" in rel or ".git" == rel:
                continue
            data = path.read_bytes() if path.is_file() else b"<dir>"
            parts.append(f"{rel}:{hashlib.sha256(data).hexdigest()}")
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()

    def test_intake_names_expected_and_actual(self):
        repo = self.copy_fixture()
        res = self.repair_run(repo, "intake", "--reports", "docs/bugreports", "--json")
        # rc 1 is fine here: the fixture also carries the unreproducible
        # report (tested separately); parsing is what this test asserts.
        self.assertIn(res.returncode, (0, 1), res.stdout + res.stderr)
        payload = json.loads(res.stdout)
        kinded = {c["kind"]: c for c in payload["candidates"]}
        self.assertIn("candidate", kinded)
        self.assertEqual(kinded["candidate"]["expected"], "median([1, 2, 3, 4]) == 2.5")
        self.assertIn("median([1, 2, 3, 4]) == 3", kinded["candidate"]["actual"])

    def test_intake_bounces_missing_facts_zero_diff(self):
        repo = self.copy_fixture()
        before = self.tree_digest(repo)
        res = self.repair_run(repo, "intake", "--reports", "docs/bugreports", "--json")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        payload = json.loads(res.stdout)
        bounce = next(c for c in payload["candidates"] if c["kind"] == "bounce")
        self.assertEqual(set(bounce["missing"]), {"expected", "actual"})
        self.assertIn("zero code changes were made", res.stderr.replace("\n", " "))
        self.assertEqual(self.tree_digest(repo), before, "intake must not write anything")

    def test_verify_red_refuses_non_test_changes(self):
        repo = self.seeded_repo()
        (repo / "src" / "calculator.py").write_text("def median(values):\n    raise SystemExit\n")
        res = self.repair_run(
            repo, "verify-red", "--test-script", "tests/test_repro_median.py",
            "--evidence", "evidence.json", "--base-ref", "HEAD",
        )
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("non-test", res.stderr)
        self.assertFalse((repo / "evidence.json").exists())

    def test_verify_red_records_base_and_red(self):
        repo = self.seeded_repo()
        res = self.repair_run(
            repo, "verify-red", "--test-script", "tests/test_repro_median.py",
            "--evidence", "evidence.json", "--base-ref", "HEAD",
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        evidence = json.loads((repo / "evidence.json").read_text())
        self.assertEqual(evidence["red"]["verified"], True)
        self.assertNotEqual(evidence["base"]["sha"], "")
        self.assertEqual(evidence["changed_files"], ["tests/test_repro_median.py"])

    def test_verify_green_requires_fix_and_records_spot_check(self):
        repo = self.seeded_repo()
        # Real Breaker→Builder flow: red recorded with the test as the ONLY
        # change, then the minimal fix lands, then green verifies.
        res = self.repair_run(
            repo, "verify-red", "--test-script", "tests/test_repro_median.py",
            "--evidence", "evidence.json", "--base-ref", "HEAD",
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        (repo / "src" / "calculator.py").write_text(
            "def median(values):\n"
            "    values = sorted(values)\n"
            "    n = len(values)\n"
            "    middle = values[n // 2]\n"
            "    if n % 2:\n"
            "        return middle\n"
            "    return (values[n // 2 - 1] + middle) / 2\n"
        )
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "fix: median averages the middle pair for even n")
        res = self.repair_run(
            repo, "verify-green", "--test-script", "tests/test_repro_median.py",
            "--evidence", "evidence.json", "--spot-check", "2",
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        evidence = json.loads((repo / "evidence.json").read_text())
        self.assertEqual(evidence["green"]["verified"], True)
        self.assertIn("src/calculator.py", evidence["fix_files"])
        self.assertEqual(evidence["spot_check"]["survivors"], 0)
        self.assertEqual(evidence["spot_check"]["file"], "src/calculator.py")

    def test_verify_green_refuses_fixless_or_tampered_base(self):
        # A green repro with zero source diff is only possible if the evidence
        # base is missing/tampered (e.g. repointed at a commit that already
        # contained the fix) — the tool must refuse, not wave it through.
        repo = self.seeded_repo()
        res = self.repair_run(
            repo, "verify-red", "--test-script", "tests/test_repro_median.py",
            "--evidence", "evidence.json", "--base-ref", "HEAD",
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        evidence = json.loads((repo / "evidence.json").read_text())
        evidence.pop("base")
        (repo / "evidence.json").write_text(json.dumps(evidence, indent=2))
        res = self.repair_run(
            repo, "verify-green", "--test-script", "tests/test_repro_median.py",
            "--evidence", "evidence.json",
        )
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("no base.sha", res.stderr)

    def test_stream_e_unlocked(self):
        repo = self.copy_fixture()
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")
        install_scripts(repo)
        write_config(repo, {"candidates": {"E": {"include": ["src/**/*.py"]}}})
        res = script(repo, "ledger.py", "next", "--stream", "E", "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(common_module.STREAMS["E"]["status"], "available")


class SecurityStreamTests(RepoTestCase):
    """Streams H+K (issue #23): advisory finders + oracle-first bumps.

    The secret scan must redact by construction: the raw secret string may
    not appear in ANY emitted artifact (stdout/stderr/evidence). The dep
    audit reads a stub advisory JSON; the bump is oracle-first - a green
    bump yields evidence on a SCRATCH copy of the tree, a red bump yields
    an issue artifact and no PR. Identical candidates dedup through a
    state file under .autonomous/ so the second run is a no-op.
    """

    FIXTURE = SKILL_ROOT / "evals" / "fixtures" / "security-python"

    def adv_run(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return run(
            [PYTHON, str(SKILL_ROOT / "scripts" / "autonomous" / "advisory.py"), *args],
            cwd=repo,
        )

    def copy_fixture(self) -> Path:
        repo = self.tmp / "repo"
        shutil.copytree(self.FIXTURE, repo)
        return repo

    def all_output(self, res: subprocess.CompletedProcess) -> str:
        return res.stdout + res.stderr

    def test_secret_scan_flags_and_never_prints_the_secret(self):
        repo = self.copy_fixture()
        before = {
            p: p.read_bytes()
            for p in repo.rglob("*") if p.is_file()
        }
        res = self.adv_run(repo, "secrets", "--json")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        payload = json.loads(res.stdout)
        secrets = [c for c in payload["candidates"] if c["kind"] == "secret"]
        self.assertTrue(secrets, payload["candidates"])
        flagged = secrets[0]
        self.assertEqual(flagged["file"], "config/settings.py")
        # Redaction is by construction: the raw scalar is never an artifact.
        blob = self.all_output(res)
        self.assertNotIn("sk-live-d16b60f5", blob)
        self.assertNotIn("sk-live-", blob)
        self.assertIn("[redacted", json.dumps(payload))
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data, f"{path} was edited")

    def test_dep_audit_flags_only_vulnerable_stubs(self):
        repo = self.copy_fixture()
        res = self.adv_run(
            repo, "deps", "--advisory", "docs/advisory-stub.json",
            "--manifest", "requirements.txt", "--json",
        )
        self.assertEqual(res.returncode, 1, self.all_output(res))
        payload = json.loads(res.stdout)
        flagged = [c for c in payload["candidates"] if c["kind"] == "dep-vuln"]
        names = {c["name"] for c in flagged}
        self.assertIn("vulnerable-lib", names)
        self.assertNotIn("stable-lib", names)
        vuln = next(c for c in flagged if c["name"] == "vulnerable-lib")
        self.assertEqual(vuln["evidence"]["advisory_id"], "FAKE-2026-1")
        self.assertEqual(vuln["evidence"]["version"], "2.1.1")

    def test_dep_run_twice_dedups_to_noop(self):
        repo = self.copy_fixture()
        first = self.adv_run(
            repo, "deps", "--advisory", "docs/advisory-stub.json",
            "--manifest", "requirements.txt", "--json",
            "--state", ".autonomous/advisory-seen.json",
        )
        self.assertEqual(first.returncode, 1, self.all_output(first))
        seen = repo / ".autonomous" / "advisory-seen.json"
        self.assertTrue(seen.is_file())
        hashes_first = set(json.loads(seen.read_text())["seen"])
        self.assertGreaterEqual(len(hashes_first), 1)
        second = self.adv_run(
            repo, "deps", "--advisory", "docs/advisory-stub.json",
            "--manifest", "requirements.txt", "--json",
            "--state", ".autonomous/advisory-seen.json",
        )
        self.assertEqual(second.returncode, 0, self.all_output(second))
        self.assertEqual(json.loads(second.stdout)["candidates"], [])
        self.assertIn("deduped", self.all_output(second))

    def test_bump_green_records_evidence_and_does_not_edit_repo(self):
        repo = self.copy_fixture()
        original = (repo / "requirements.txt").read_text()
        res = self.adv_run(
            repo, "bump", "--json",
            "--dep", "vulnerable-lib", "--to", "2.2.0",
            "--manifest", "requirements.txt",
            "--test-cmd", f"{PYTHON} tests/test_pin.py --expect 2.2.0",
            "--changelog", "CHANGELOG.md",
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        payload = json.loads(res.stdout)
        self.assertEqual(payload["status"], "green")
        self.assertEqual(payload["from"], "2.1.1")
        self.assertEqual(payload["to"], "2.2.0")
        self.assertIn("risk note", payload["evidence"]["changelog"])
        self.assertEqual((repo / "requirements.txt").read_text(), original,
                         "the real tree must stay untouched; the bump ran on a copy")

    def test_bump_red_becomes_issue_artifact_without_pr(self):
        repo = self.copy_fixture()
        original = (repo / "requirements.txt").read_text()
        res = self.adv_run(
            repo, "bump", "--json",
            "--dep", "vulnerable-lib", "--to", "3.0.0",
            "--manifest", "requirements.txt",
            "--test-cmd", f"{PYTHON} tests/test_pin.py --expect 2.2.0",
            "--changelog", "CHANGELOG.md",
        )
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
        blob = res.stdout + res.stderr
        self.assertIn("## Breakage", blob, "red bumps must produce the issue draft")
        self.assertIn("pip install", blob)  # the suggested repro for the owner
        self.assertIn("3.0.0", blob)
        self.assertEqual((repo / "requirements.txt").read_text(), original)
        self.assertEqual(json.loads(res.stdout)["status"], "red")

    def test_streams_h_and_k_available(self):
        repo = self.copy_fixture()
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")
        self.assertEqual(common_module.STREAMS["H"]["status"], "available")
        self.assertEqual(common_module.STREAMS["K"]["status"], "available")
        install_scripts(repo)
        write_config(repo, {"candidates": {"H": {"include": ["src/**/*.py"]}, "K": {"include": ["src/**/*.py"]}}})
        for stream in ("H", "K"):
            res = script(repo, "ledger.py", "next", "--stream", stream, "--json")
            self.assertEqual(res.returncode, 0, res.stderr)


@unittest.skipUnless(
    all(shutil.which(tool) for tool in ("bash", "flock", "timeout", "stat", "date")),
    "watchdog shell dependencies are not installed",
)
class FuzzSeedTests(RepoTestCase):
    """Stream C (issue #25): recorded-seed fuzz + deterministic shrink.

    The harness (repo-supplied) checks a property; the tool runs it over
    the recorded corpus seeds, shrinks a failing input deterministically,
    and writes the counterexample to the retained corpus. Replay is the
    red->green oracle: the shrunk counterexample must fail before the fix
    and pass after.
    """

    FIXTURE = SKILL_ROOT / "evals" / "fixtures" / "fuzz-python"

    def fuzz_run(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return run(
            [PYTHON, str(SKILL_ROOT / "scripts" / "autonomous" / "fuzz_shrink.py"), *args],
            cwd=repo,
        )

    def copy_fixture(self) -> Path:
        repo = self.tmp / "repo"
        shutil.copytree(self.FIXTURE, repo)
        return repo

    def test_run_finds_and_shrinks_to_same_counterexample(self):
        repo = self.copy_fixture()
        first = self.fuzz_run(
            repo, "run", "--harness", "tests/property.py",
            "--corpus", "corpus/seeds.txt", "--json",
        )
        self.assertEqual(first.returncode, 1, first.stdout + first.stderr)
        payload = json.loads(first.stdout)
        ce = payload["counterexample"]
        # The retention name is content-addressed: CE-<sha8 of the input>.
        sha8 = hashlib.sha256(ce["input"].encode()).hexdigest()[:8]
        self.assertEqual(ce["file"], f"corpus/regressions/CE-{sha8}.txt")
        self.assertLessEqual(len(ce["input"]), 12, ce)
        regression = repo / ce["file"]
        self.assertEqual(regression.read_text().strip(), ce["input"])
        # determinism: same corpus, same harness, same seed record → the
        # same shrunk counterexample, byte for byte.
        second = self.fuzz_run(
            repo, "run", "--harness", "tests/property.py",
            "--corpus", "corpus/seeds.txt", "--json",
        )
        second_payload = json.loads(second.stdout)
        self.assertEqual(second_payload["counterexample"]["input"], ce["input"])

    def test_replay_is_red_before_fix_and_green_after(self):
        repo = self.copy_fixture()
        discovered = self.fuzz_run(
            repo, "run", "--harness", "tests/property.py",
            "--corpus", "corpus/seeds.txt", "--json",
        )
        ce_file = json.loads(discovered.stdout)["counterexample"]["file"]
        red = self.fuzz_run(
            repo, "replay", "--harness", "tests/property.py",
            "--counterexample", ce_file, "--json",
        )
        self.assertEqual(red.returncode, 1, red.stdout + red.stderr)
        self.assertEqual(json.loads(red.stdout)["status"], "red")
        # The Builder fix: lowercase every character (plain str.lower()).
        (repo / "src" / "text_ops.py").write_text(
            "def normalize(text):\n"
            "    return text.lower()\n"
        )
        green = self.fuzz_run(
            repo, "replay", "--harness", "tests/property.py",
            "--counterexample", ce_file, "--json",
        )
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
        self.assertEqual(json.loads(green.stdout)["status"], "green")


class PerfGateTests(RepoTestCase):
    """Stream G (issue #25): pinned-bench delta + differential equivalence.

    Both halves are mandatory: a faster-implementation candidate only
    becomes keepable when it is (a) beyond the configured speedup band on
    the pinned bench and (b) output-equivalent on the generated inputs.
    'Faster because it does less' is rejected by (b).
    """

    FIXTURE = SKILL_ROOT / "evals" / "fixtures" / "perf-python"

    def gate(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return run(
            [PYTHON, str(SKILL_ROOT / "scripts" / "autonomous" / "perf_gate.py"), *args],
            cwd=repo,
        )

    def copy_fixture(self) -> Path:
        repo = self.tmp / "repo"
        shutil.copytree(self.FIXTURE, repo)
        return repo

    def test_real_optimization_passes_both_halves(self):
        repo = self.copy_fixture()
        res = self.gate(
            repo, "gate",
            "--old", "src/hot.py", "--new", "src/optimized.py",
            "--fn-name", "dedup", "--min-speedup", "2.0",
            "--samples", "3", "--differential-count", "40", "--json",
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        payload = json.loads(res.stdout)
        self.assertEqual(payload["bench"]["verdict"], "beyond-band")
        self.assertGreaterEqual(payload["bench"]["speedup"], 2.0)
        self.assertEqual(payload["differential"]["verdict"], "equivalent")

    def test_does_less_is_rejected_by_differential_half(self):
        repo = self.copy_fixture()
        res = self.gate(
            repo, "gate",
            "--old", "src/hot.py", "--new", "src/cheat.py",
            "--fn-name", "dedup", "--min-speedup", "2.0",
            "--samples", "3", "--differential-count", "40", "--json",
        )
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
        blob = res.stdout + res.stderr
        self.assertIn("differential", blob)
        self.assertIn("rejected", blob)

    def test_streams_c_and_g_available(self):
        repo = self.copy_fixture()
        self.assertEqual(common_module.STREAMS["C"]["status"], "available")
        self.assertEqual(common_module.STREAMS["G"]["status"], "available")
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")
        install_scripts(repo)
        write_config(repo, {"candidates": {"C": {"include": ["src/**/*.py"]}, "G": {"include": ["src/**/*.py"]}}})
        for stream in ("C", "G"):
            res = script(repo, "ledger.py", "next", "--stream", stream, "--json")
            self.assertEqual(res.returncode, 0, res.stderr)


@unittest.skipUnless(
    all(shutil.which(tool) for tool in ("bash", "flock", "timeout", "stat", "date")),
    "watchdog shell dependencies are not installed",
)
class StreamLTests(RepoTestCase):
    """Stream L (issue #26): meta self-improvement, capped and kill-switched.

    Replay = every claim in an L3 proposal is recomputed from the
    recorded fleet history (rows, not folklore). L4-touching proposals
    (eval gates, weakening tripwire, tier floors...) are issue-only EVEN
    IF their numbers would pass. Stream L has its own kill switch,
    independent of the fleet switch. Lessons carry evidence + scope +
    re-validate-by and expired ones are demoted.
    """

    FIXTURE = SKILL_ROOT / "evals" / "fixtures" / "meta-fleet"

    def meta_run(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return run(
            [PYTHON, str(SKILL_ROOT / "scripts" / "autonomous" / "meta.py"), *args],
            cwd=repo,
        )

    def copy_fixture(self) -> Path:
        repo = self.tmp / "repo"
        repo.mkdir()
        shutil.copytree(self.FIXTURE, repo / "meta-fleet")
        return repo

    def write_fleet_config(self, repo: Path, fleet: dict) -> None:
        cfg_dir = repo / "scripts" / "autonomous"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.json").write_text(json.dumps({"fleet": fleet}))

    def test_kill_switch_is_independent_of_the_fleet_switch(self):
        repo = self.copy_fixture()
        self.write_fleet_config(repo, {"stream_l_enabled": False})
        res = self.meta_run(
            repo, "replay", "--fixture-dir", "meta-fleet",
            "--proposal", "meta-fleet/proposals/good-diff-budget.json", "--json",
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("kill switch", res.stdout + res.stderr)
        # Even with the fleet switch on, L needs its own flag AND no
        # stream-L stop file.
        self.write_fleet_config(repo, {"enabled": True, "stream_l_enabled": False})
        res = self.meta_run(
            repo, "replay", "--fixture-dir", "meta-fleet",
            "--proposal", "meta-fleet/proposals/good-diff-budget.json", "--json",
        )
        self.assertNotEqual(res.returncode, 0)
        self.write_fleet_config(repo, {"enabled": True, "stream_l_enabled": True})
        (repo / ".autonomous").mkdir()
        (repo / ".autonomous" / "STOP-STREAM-L").write_text("")
        res = self.meta_run(
            repo, "replay", "--fixture-dir", "meta-fleet",
            "--proposal", "meta-fleet/proposals/good-diff-budget.json", "--json",
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("STOP-STREAM-L", res.stdout + res.stderr)

    def test_l4_touching_proposal_is_issue_only_even_if_numbers_pass(self):
        repo = self.copy_fixture()
        self.write_fleet_config(repo, {"stream_l_enabled": True})
        res = self.meta_run(
            repo, "replay", "--fixture-dir", "meta-fleet",
            "--proposal", "meta-fleet/proposals/bad-weakening.json", "--json",
        )
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
        blob = res.stdout + res.stderr
        self.assertIn("L4", blob)
        self.assertIn("issue-only", blob)
        self.assertNotIn("draft_pr", blob)
        # recorded as rejected, not silently dropped
        journal = json.loads((repo / ".autonomous" / "l-journal.json").read_text())
        self.assertEqual(journal["proposals"][0]["verdict"], "rejected")

    def test_good_proposal_passes_replay_with_before_after_draft(self):
        repo = self.copy_fixture()
        self.write_fleet_config(repo, {"stream_l_enabled": True})
        res = self.meta_run(
            repo, "replay", "--fixture-dir", "meta-fleet",
            "--proposal", "meta-fleet/proposals/good-diff-budget.json", "--json",
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        payload = json.loads(res.stdout)
        self.assertEqual(payload["verdict"], "pass")
        claims = payload["claims_replay"]
        before = claims["median_owner_minutes"]["before_recorded"]
        after = claims["median_owner_minutes"]["after_claimed"]
        self.assertLess(after, before)
        self.assertEqual(before, 40.0)  # recomputed from the recorded rows
        self.assertEqual(payload["multi_objective"]["passes_floors"], True)
        self.assertIn("draft", payload["draft_pr"]["title"].lower())
        self.assertIn("40.0", payload["draft_pr"]["body"])
        self.assertIn(str(after), payload["draft_pr"]["body"])

    def test_groundless_claim_is_rejected_by_replay(self):
        repo = self.copy_fixture()
        self.write_fleet_config(repo, {"stream_l_enabled": True})
        (repo / "meta-fleet" / "proposals" / "groundless.json").write_text(json.dumps({
            "schema": "stream-l-proposal/1",
            "kind": "param-tune",
            "touches": [],
            "basis": "6 of 12 rejections cite oversized diffs",
            "claims": {"median_owner_minutes": {"after": 2, "before": 99}},
        }))
        res = self.meta_run(
            repo, "replay", "--fixture-dir", "meta-fleet",
            "--proposal", "meta-fleet/proposals/groundless.json", "--json",
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("recorded", res.stdout + res.stderr)
        self.assertIn("before", res.stdout + res.stderr)

    def test_expired_lessons_are_demoted(self):
        repo = self.copy_fixture()
        self.write_fleet_config(repo, {"stream_l_enabled": True})
        res = self.meta_run(
            repo, "lessons", "--fixture-dir", "meta-fleet", "--json",
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        payload = json.loads(res.stdout)
        demoted = [entry["id"] for entry in payload["demoted"]]
        kept = [entry["id"] for entry in payload["kept"]]
        self.assertEqual(demoted, ["lsn-2025-keep-sse", "lsn-no-evidence"])
        self.assertEqual(kept, ["lsn-fresh-budget"])
        digest = res.stdout
        self.assertIn("re-validate", digest)

    def test_ledger_refuses_stream_l_without_its_switch(self):
        repo = self.copy_fixture()
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")
        install_scripts(repo)
        write_config(repo, {"fleet": {"enabled": True, "stream_l_enabled": False}})
        res = script(repo, "ledger.py", "next", "--stream", "L")
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertIn("kill switch", res.stderr)
        self.assertEqual(common_module.STREAMS["L"]["status"], "gated")


class DoctorTests(RepoTestCase):
    """Issue #19: fleet doctor preflight — advice becomes checks.

    Stubbed gh/loginctl/systemctl on PATH plus a temp state dir. Hard
    failures (linger off, over-scoped token, orphan worktrees) are named
    and exit != 0; the all-good staging exits 0. The Orchestrator cold
    start consults the doctor and reports instead of dispatching.
    """

    FIXTURE = SKILL_ROOT / "evals" / "fixtures" / "doctor-vm"

    def stage(self, **env_extra: str) -> Path:
        repo = self.tmp / "repo"
        repo.mkdir(exist_ok=True)
        shutil.copytree(self.FIXTURE, repo / "doctor-vm")
        install_scripts(repo)
        write_config(repo, {
            "session": {
                "keepalive_consent": {
                    "granted_by": "owner",
                    "date": "2026-10-25",
                    "shape": "watchdog",
                }
            },
            "fleet": {"merge_policy": "never"},
        })
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")
        git(repo, "remote", "add", "origin", "https://github.com/cassandre60/skills.git")
        commit(repo, "base")
        state = repo / ".autonomous" / "state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "clock_reference").write_text(str(int(time.time())))
        (state / "opencode_session_id").write_text("ses_TEST\n")
        home = self.tmp / "home"
        home.mkdir(parents=True, exist_ok=True)
        env_extra.setdefault("HOME", str(home))
        env_extra.setdefault("ORCH_STATE_DIR", "")
        self.env_extra = env_extra
        return repo

    def doctor(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        stub_bin = repo / "doctor-vm" / "bin"
        env = dict(os.environ)
        env["PATH"] = f"{stub_bin}:{os.environ['PATH']}"
        env.update(self.env_extra)
        return script(repo, "fleet.py", "doctor", "--json", *args, env=env)

    def test_all_good_staging_exits_zero(self):
        repo = self.stage()
        res = self.doctor(repo)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        payload = json.loads(res.stdout)
        self.assertTrue(payload["dispatch_ok"])
        self.assertEqual(payload["hard_failures"], [])
        names = {check["name"] for check in payload["checks"]}
        for expected in ("gh-auth", "token-scopes", "branch-protection",
                         "linger", "watchdog-timer", "worktree-registry",
                         "disk-headroom", "model-ladder"):
            self.assertIn(expected, names)
        linger = next(c for c in payload["checks"] if c["name"] == "linger")
        self.assertTrue(linger["ok"])

    def test_missing_linger_is_a_named_hard_failure(self):
        repo = self.stage(DOCTOR_STUB_LINGER="no")
        res = self.doctor(repo)
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
        payload = json.loads(res.stdout)
        self.assertFalse(payload["dispatch_ok"])
        self.assertIn("linger", payload["hard_failures"])
        linger = next(c for c in payload["checks"] if c["name"] == "linger")
        self.assertEqual(linger["severity"], "hard")
        self.assertFalse(linger["ok"])

    def test_overscoped_token_is_a_hard_failure(self):
        repo = self.stage()
        scopes = repo / "doctor-vm" / "scopes-overbroad.txt"
        scopes.write_text("'repo', 'admin:org'")
        self.env_extra = {"DOCTOR_STUB_SCOPES_FILE": str(scopes)}
        res = self.doctor(repo)
        payload = json.loads(res.stdout)
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("token-scopes", payload["hard_failures"])

    def test_orphan_worktree_registry_is_a_hard_failure(self):
        repo = self.stage()
        wtree = self.tmp / "orphan"
        git(repo, "worktree", "add", "-q", str(wtree), "-b", "orphan-branch")
        shutil.rmtree(wtree)
        res = self.doctor(repo)
        payload = json.loads(res.stdout)
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("worktree-registry", payload["hard_failures"])

    def test_govern_refuses_to_dispatch_when_doctor_is_enforced(self):
        repo = self.stage(DOCTOR_STUB_LINGER="no")
        write_config(repo, {"fleet": {"merge_policy": "never", "doctor": {"enforce": True}}})
        stub_bin = repo / "doctor-vm" / "bin"
        env = dict(os.environ)
        env["PATH"] = f"{stub_bin}:{os.environ['PATH']}"
        env.update(self.env_extra)
        res = script(repo, "fleet.py", "govern", "--json", env=env)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        payload = json.loads(res.stdout)
        self.assertFalse(payload["dispatch"])
        self.assertIn("doctor", payload["reason"].lower())

    def test_laptop_profile_with_live_timer_warns_about_elective_machinery(self):
        # The keep-alive stack (watchdog/wake timers, beats) is elective: on a
        # personal machine (session.environment: laptop, the default) a live
        # timer means it was installed without — or beyond — an owner ask.
        repo = self.stage()
        write_config(repo, {"fleet": {"merge_policy": "never"},
                            "session": {
                                "environment": "laptop",
                                "keepalive_consent": {
                                    "granted_by": "owner",
                                    "date": "2026-10-25",
                                    "shape": "watchdog",
                                },
                            }})
        res = self.doctor(repo)
        payload = json.loads(res.stdout)
        profile = next(c for c in payload["checks"]
                       if c["name"] == "environment-profile")
        self.assertEqual(profile["severity"], "soft")
        self.assertFalse(profile["ok"])
        self.assertIn("elective", profile["detail"])
        # A warning names the condition; it does not block dispatch.
        self.assertTrue(payload["dispatch_ok"], payload["hard_failures"])

    def test_vm_profile_with_live_timer_is_clean(self):
        repo = self.stage()
        write_config(repo, {"fleet": {"merge_policy": "never"},
                            "session": {
                                "environment": "vm",
                                "keepalive_consent": {
                                    "granted_by": "owner",
                                    "date": "2026-10-25",
                                    "shape": "watchdog",
                                },
                            }})
        res = self.doctor(repo)
        payload = json.loads(res.stdout)
        self.assertNotIn("environment-profile",
                         {c["name"] for c in payload["checks"]})

    def test_two_candidate_state_dirs_are_a_hard_failure(self):
        # Issue #93: the doc's <project>-oracle search and the code's default
        # disagreed; adopting the wrong one reads a journal that is days dead.
        home = self.tmp / "home"
        share = home / ".local" / "share"
        live = share / "proj-oracle"
        live.mkdir(parents=True)
        (live / "journal.md").write_text("live\n")
        stale = share / "proj-old-oracle"
        stale.mkdir(parents=True)
        (stale / "journal.md").write_text("stale\n")
        repo = self.stage(HOME=str(home))
        res = self.doctor(repo)
        payload = json.loads(res.stdout)
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("state-dir-candidates", payload["hard_failures"])
        check = next(c for c in payload["checks"]
                     if c["name"] == "state-dir-candidates")
        self.assertIn("proj-old-oracle", check["detail"])
        self.assertIn("proj-oracle", check["detail"])
        self.assertIn("journal", check["detail"])

    def test_symlinked_candidates_are_one_dir(self):
        home = self.tmp / "home"
        share = home / ".local" / "share"
        live = share / "proj-oracle"
        live.mkdir(parents=True)
        (live / "journal.md").write_text("live\n")
        (share / "proj-oracle-alias").symlink_to(live)
        repo = self.stage(HOME=str(home))
        res = self.doctor(repo)
        payload = json.loads(res.stdout)
        self.assertNotIn("state-dir-candidates", payload["hard_failures"])
        check = next(c for c in payload["checks"]
                     if c["name"] == "state-dir-candidates")
        self.assertTrue(check["ok"], check)
        self.assertIn("one candidate", check["detail"])

    def test_archived_candidate_name_does_not_keep_the_ambiguity(self):
        home = self.tmp / "home"
        share = home / ".local" / "share"
        (share / "proj-oracle").mkdir(parents=True)
        (share / "proj-old-oracle.stale-2026-10-22").mkdir(parents=True)
        repo = self.stage(HOME=str(home))
        res = self.doctor(repo)
        payload = json.loads(res.stdout)
        self.assertNotIn("state-dir-candidates", payload["hard_failures"])

    def test_explicit_state_dir_skips_the_candidate_scan(self):
        home = self.tmp / "home"
        share = home / ".local" / "share"
        (share / "a-oracle").mkdir(parents=True)
        (share / "b-oracle").mkdir(parents=True)
        repo = self.stage(HOME=str(home), ORCH_STATE_DIR=str(self.tmp / "chosen"))
        res = self.doctor(repo)
        payload = json.loads(res.stdout)
        self.assertNotIn("state-dir-candidates", payload["hard_failures"])
        check = next(c for c in payload["checks"]
                     if c["name"] == "state-dir-candidates")
        self.assertIn("explicit", check["detail"])

    def test_no_candidate_state_dirs_is_fine(self):
        repo = self.stage()
        res = self.doctor(repo)
        payload = json.loads(res.stdout)
        self.assertNotIn("state-dir-candidates", payload["hard_failures"])

    def test_live_keepalive_without_recorded_consent_is_a_hard_failure(self):
        # Issue #91: a live timer is the shape that flooded two frozen
        # sessions (84 and 90 queued nudges). Arming it is an owner decision
        # recorded in the config — never inferred from a profile or a prior
        # deployment's leftovers.
        repo = self.stage()
        write_config(repo, {"fleet": {"merge_policy": "never"}})
        res = self.doctor(repo)
        payload = json.loads(res.stdout)
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("keepalive-consent", payload["hard_failures"])
        consent = next(c for c in payload["checks"]
                       if c["name"] == "keepalive-consent")
        self.assertEqual(consent["severity"], "hard")
        self.assertFalse(consent["ok"])
        self.assertIn("recorded owner ask", consent["detail"])

    def test_malformed_keepalive_consent_does_not_count(self):
        repo = self.stage()
        write_config(repo, {
            "session": {"keepalive_consent": {"granted_by": "owner",
                                              "shape": "always-on"}},
            "fleet": {"merge_policy": "never"},
        })
        res = self.doctor(repo)
        payload = json.loads(res.stdout)
        self.assertIn("keepalive-consent", payload["hard_failures"])

    def test_recorded_keepalive_consent_clears_the_check(self):
        repo = self.stage()
        res = self.doctor(repo)
        payload = json.loads(res.stdout)
        consent = next(c for c in payload["checks"]
                       if c["name"] == "keepalive-consent")
        self.assertTrue(consent["ok"], consent)
        self.assertEqual(consent["severity"], "hard")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_vm_profile_without_consent_is_soft_until_a_timer_exists(self):
        repo = self.stage(DOCTOR_STUB_TIMER="inactive")
        write_config(repo, {"fleet": {"merge_policy": "never"},
                            "session": {"environment": "vm"}})
        res = self.doctor(repo)
        payload = json.loads(res.stdout)
        consent = next(c for c in payload["checks"]
                       if c["name"] == "keepalive-consent")
        self.assertEqual(consent["severity"], "soft")
        self.assertFalse(consent["ok"])
        self.assertTrue(payload["dispatch_ok"], payload["hard_failures"])


class StatelessWakeTests(RepoTestCase):
    """Issue #18 (state half): a fresh wake reads only files and leaves
    state a SECOND fresh wake can continue from — no session id involved.

    `fleet.py needs-brain` is the 0-token quiet-hours predicate: it
    derives reasons from the snapshot + stub-gh PR state, records the
    decision digest in state/wake.json, and reports "quiet" when nothing
    changed since the last wake.
    """

    def stage(self, prs):
        repo, origin = init_repo(self.tmp, name="wake-repo", with_origin=True)
        install_scripts(repo)
        write_config(repo)
        json_path = self.tmp / "wake-prs.json"
        json_path.write_text(json.dumps(prs) + "\n")
        bin_dir = path_with_only(self.tmp, ("git",))
        gh = write(
            bin_dir / "gh",
            "#!/bin/sh\nwhile IFS= read -r line; do printf '%s\\n' \"$line\"; done < \"$GH_JSON\"\n",
        )
        gh.chmod(0o755)
        env = env_with_path(bin_dir, GH_JSON=str(json_path), ORCH_STATE_DIR=str(repo / ".autonomous" / "state"))
        return repo, env, json_path

    def red_pr(self, number=7):
        return {
            "number": number,
            "title": f"Stream A: PR {number}",
            "headRefName": f"task/pr-{number}",
            "headRefOid": f"sha-{number}",
            "statusCheckRollup": [
                {"name": "unit-tests", "status": "COMPLETED", "conclusion": "FAILURE"}
            ],
        }

    def test_first_wake_needs_brain_and_records_state(self):
        repo, env, _ = self.stage([self.red_pr()])
        res = script(repo, "fleet.py", "needs-brain", "--json", env=env)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        payload = json.loads(res.stdout)
        self.assertTrue(payload["needs_brain"])
        self.assertIn("red-pr #7", payload["reasons"][0])
        wake = repo / ".autonomous" / "state" / "wake.json"
        self.assertTrue(wake.is_file(), "the wake decision must be recorded")
        self.assertEqual(json.loads(wake.read_text())["digest"], payload["digest"])
        # stateless by construction: no session id is read or written
        self.assertFalse((repo / ".autonomous" / "state" / "opencode_session_id").exists())

    def test_second_wake_on_unchanged_snapshot_is_quiet(self):
        repo, env, _ = self.stage([self.red_pr()])
        first = script(repo, "fleet.py", "needs-brain", "--json", env=env)
        self.assertTrue(json.loads(first.stdout)["needs_brain"])
        second = script(repo, "fleet.py", "needs-brain", "--json", env=env)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        payload = json.loads(second.stdout)
        self.assertFalse(payload["needs_brain"])
        self.assertIn("unchanged", payload["reason"])

    def test_changed_snapshot_reopens_the_wake(self):
        repo, env, json_path = self.stage([self.red_pr()])
        script(repo, "fleet.py", "needs-brain", "--json", env=env)
        # The PR went green and unmerged: a fresh wake must pick it up.
        green = self.red_pr()
        green["statusCheckRollup"] = [
            {"name": "unit-tests", "status": "COMPLETED", "conclusion": "SUCCESS"}
        ]
        json_path.write_text(json.dumps([green]) + "\n")
        res = script(repo, "fleet.py", "needs-brain", "--json", env=env)
        payload = json.loads(res.stdout)
        self.assertTrue(payload["needs_brain"])
        self.assertIn("green-unmerged-pr #7", payload["reasons"][0])

    def test_kill_switch_is_a_tend_only_reason(self):
        repo, env, _ = self.stage([self.red_pr()])
        (repo / ".autonomous").mkdir(exist_ok=True)
        (repo / ".autonomous" / "STOP").write_text("")
        res = script(repo, "fleet.py", "needs-brain", "--json", env=env)
        payload = json.loads(res.stdout)
        self.assertTrue(payload["needs_brain"])
        self.assertTrue(
            any("kill-switch-present" in reason for reason in payload["reasons"]),
            payload["reasons"],
        )

    def test_idle_queue_is_a_reason_to_generate(self):
        repo, env, _ = self.stage([])
        res = script(repo, "fleet.py", "needs-brain", "--json", env=env)
        payload = json.loads(res.stdout)
        self.assertTrue(payload["needs_brain"])
        self.assertIn("queue-idle", payload["reasons"][0])

if __name__ == "__main__":
    unittest.main()
