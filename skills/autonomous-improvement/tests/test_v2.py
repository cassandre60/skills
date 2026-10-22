from __future__ import annotations

import importlib
import json
import os
import shutil
import signal
import subprocess
import sys
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
import ledger as ledger_module  # noqa: E402
import mutants as mutants_module  # noqa: E402
import mutation_engine as engine_module  # noqa: E402


class RepoTestCase(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)


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
        planned = script(repo, "ledger.py", "next", "--stream", "C")
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
            if info["status"] == "available":
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
            "references/gates-and-vocabulary.md",
            "references/stack-adapters.md",
            "references/examples.md",
            "assets/watchdog.sh",
            "assets/snapshot.sh",
            "assets/orchestrator-watchdog.service",
            "assets/orchestrator-watchdog.timer",
            "assets/orchestrator-snapshot.service",
            "assets/orchestrator-snapshot.timer",
            "assets/install-watchdog.sh",
            "assets/gitattributes.fragment.txt",
            "assets/gitignore.fragment.txt",
            "scripts/autonomous/fleet.py",
        ):
            self.assertTrue((SKILL_ROOT / relative).exists(), relative)


if __name__ == "__main__":
    unittest.main()
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

    def test_the_checkpoint_covers_new_files(self) -> None:
        self.assertIn("stage new files by name", self.unattended.lower())

    def test_a_dead_tabs_untracked_work_is_preserved(self) -> None:
        self.assertIn("dirty and untracked", self.unattended.lower())
        self.assertIn("before touching its worktree", self.unattended.lower())

    def test_the_dispatch_brief_carries_the_precheck(self) -> None:
        self.assertIn("ledger.py check --target", self.unattended)
        self.assertIn("correcting the brief's premise", self.unattended.lower())
        self.assertIn("keeps the dispatch brief short", self.protocol.lower())




"""Validator tests: the rows the parser cannot see must be reported.

Red before `ledger.py validate` existed: there was no command that could tell
a fleet that its registry was invisible.
"""

import json


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





"""Git-reality contract tests.

Red before this change: the skill told a reader to rebase a pushed branch in
one reference and never to force-push in another, and said nothing about
strict protection, branch inheritance, or batching the fleet's own PRs.
"""

def _flat_git(text: str) -> str:
    """Collapse whitespace so an assertion survives a re-wrap."""
    return " ".join(text.split())


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





"""Owner-ask and gh-fallback contract tests.

Red before this change: the skill said "re-ask the owner per item" without
saying what an ask contains or where the consent is recorded, and the gh
fallbacks were left for each agent to rediscover.
"""

def _flat_ask(text: str) -> str:
    """Collapse whitespace so an assertion survives a re-wrap."""
    return " ".join(text.split())


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

        unknown = script(
            repo, "mutation_engine.py", "--source", "src/app.py",
            "--test-cmd", f"{PYTHON} -c pass", "--backend", "stryker",
        )
        self.assertEqual(unknown.returncode, 2)
        self.assertIn("invalid choice: 'stryker'", unknown.stderr)


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
        for stream in ("H", "K"):
            res = script(repo, "ledger.py", "next", "--stream", stream, "--json")
            self.assertEqual(res.returncode, 0, res.stderr)


