from __future__ import annotations

import datetime
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
        self.assertGreaterEqual(data["timeout_effective_s"], 2)

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
        self.assertEqual(data["counts"]["unconfirmed"], 1)

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


class EvidenceTests(RepoTestCase):
    """Issue #14: the verify-before/after contract as an artifact, not prose."""

    TEST_CMD = f'{PYTHON} -c "from src.app import f; assert f(1, 1) is True; assert f(1, 2) is False"'

    def _repo(self) -> Path:
        repo, _ = python_repo(self.tmp)
        commit(repo, "base")
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
        self.assertEqual(artifact["mutant"]["mutated"], "    return a != b")
        # The mutant must never survive the evidence run itself.
        self.assertEqual(
            (repo / "src" / "app.py").read_text(), "def f(a, b):\n    return a == b\n"
        )
        self.assertFalse((repo / "src" / "app.py.mutbak").exists())

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
        write(
            worktree / "tests" / "test_app.py",
            "from src.app import f\n\ndef test_f():\n    pass\n",
        )
        commit(worktree, "weaken the test")
        result = script(repo, "orchestrator.py", "verify-gate", "--worktree", str(worktree))
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("weakening", result.stdout + result.stderr)
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

    def test_govern_enforces_acceptance_floor(self):
        repo, _ = self.make()
        bin_dir = path_with_only(self.tmp, ("git",))
        open_path = self.tmp / "gov-open.json"
        all_path = self.tmp / "gov-all.json"
        gh = write(
            bin_dir / "gh",
            f"#!{PYTHON}\n"
            "import json, os, sys\n"
            "args = sys.argv[1:]\n"
            "state = args[args.index('--state') + 1] if '--state' in args else 'open'\n"
            "path = os.environ['GH_ALL'] if state == 'all' else os.environ['GH_OPEN']\n"
            "print(open(path).read())\n",
        )
        gh.chmod(0o755)
        env = env_with_path(bin_dir, GH_OPEN=str(open_path), GH_ALL=str(all_path))
        open_path.write_text("[]\n")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        def decided(merged: int, closed: int):
            prs = [
                {"number": i + 1, "headRefName": f"m{i}", "headRefOid": "x",
                 "mergedAt": now, "closedAt": now, "createdAt": now}
                for i in range(merged)
            ] + [
                {"number": 100 + i, "headRefName": f"c{i}", "headRefOid": "y",
                 "mergedAt": None, "closedAt": now, "createdAt": now}
                for i in range(closed)
            ]
            all_path.write_text(json.dumps(prs) + "\n")

        # 4/10 accepted over 14d is below the 0.5 floor with enough decisions.
        decided(merged=4, closed=6)
        blocked = script(repo, "fleet.py", "govern", "--json", env=env)
        self.assertEqual(blocked.returncode, 1, blocked.stdout + blocked.stderr)
        data = json.loads(blocked.stdout)
        self.assertFalse(data["dispatch"])
        self.assertIn("acceptance", data["reason"])
        self.assertEqual(data["acceptance"]["decided"], 10)

        # Too few decisions to judge: the floor does not engage.
        decided(merged=0, closed=4)
        allowed = script(repo, "fleet.py", "govern", "--json", env=env)
        self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)
        self.assertTrue(json.loads(allowed.stdout)["dispatch"])

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


class FleetWakeTests(RepoTestCase):
    """Issue #18: stateless wakes — files in, fresh run, files out."""

    def _pr(self, number: int, conclusion: str = "", status: str = "COMPLETED"):
        return {
            "number": number,
            "title": f"pr {number}",
            "headRefName": f"b{number}",
            "headRefOid": f"sha{number}",
            "statusCheckRollup": [
                {"name": "test", "status": status, "conclusion": conclusion}
            ],
            "labels": [],
            "commits": [{"oid": f"sha{number}"}],
        }

    def _setup(self, payload):
        repo, _ = python_repo(self.tmp)
        commit(repo)
        json_path = self.tmp / "prs.json"
        json_path.write_text(json.dumps(payload) + "\n")
        bin_dir = path_with_only(self.tmp, ("git",))
        gh = write(
            bin_dir / "gh",
            "#!/bin/sh\nwhile IFS= read -r line; do printf '%s\\n' \"$line\"; done < \"$GH_JSON\"\n",
        )
        gh.chmod(0o755)
        return repo, env_with_path(bin_dir, GH_JSON=str(json_path)), json_path

    def test_needs_brain_quiet_at_cap_and_loud_when_red(self):
        repo, env, json_path = self._setup(
            [self._pr(i, status="IN_PROGRESS") for i in range(1, 6)]
        )
        quiet = script(repo, "fleet.py", "needs-brain", "--json", env=env)
        self.assertEqual(quiet.returncode, 1, quiet.stdout + quiet.stderr)
        self.assertFalse(json.loads(quiet.stdout)["needs_brain"])

        json_path.write_text(json.dumps([self._pr(7, conclusion="FAILURE")]) + "\n")
        loud = script(repo, "fleet.py", "needs-brain", "--json", env=env)
        self.assertEqual(loud.returncode, 0, loud.stdout + loud.stderr)
        data = json.loads(loud.stdout)
        self.assertTrue(data["needs_brain"])
        self.assertEqual(data["tend"], [7])
        self.assertIn("red-pr:#7", data["reasons"])

    def test_wake_launches_fresh_harness_and_second_wake_continues(self):
        repo, env, _ = self._setup([self._pr(7, conclusion="FAILURE")])
        state_dir = self.tmp / "state"
        marker = self.tmp / "harness-marker.json"
        harness = write(
            self.tmp / "harness_stub.py",
            "import json, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "prompt = ''\n"
            "for a in args:\n"
            "    if a.endswith('last-wake-prompt.md'):\n"
            "        prompt = pathlib.Path(a).read_text()\n"
            f"pathlib.Path({str(marker)!r}).write_text(json.dumps({{'args': args, 'prompt': prompt}}))\n",
        )
        cmd = ("fleet.py", "wake", "--state-dir", str(state_dir),
               "--harness-cmd", f"{PYTHON} {harness} {{prompt_file}}")
        first = script(repo, *cmd, env=env)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        launched = json.loads(marker.read_text())
        self.assertIn("PR #7", launched["prompt"])
        self.assertIn("fleet.py doctor", launched["prompt"])
        self.assertTrue((state_dir / "snapshot.json").exists())
        self.assertTrue((state_dir / "last-wake-prompt.md").exists())
        wakes = (state_dir / "wakes.jsonl").read_text().splitlines()
        self.assertEqual(len(wakes), 1)
        self.assertEqual(json.loads(wakes[0])["action"], "launched")

        second = script(repo, *cmd, env=env)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(
            len((state_dir / "wakes.jsonl").read_text().splitlines()), 2
        )
        # The stateless shape has no session to immortalize.
        self.assertEqual(list(state_dir.rglob("*session*")), [])

    def test_wake_quiet_never_launches(self):
        repo, env, _ = self._setup(
            [self._pr(i, status="IN_PROGRESS") for i in range(1, 6)]
        )
        state_dir = self.tmp / "state"
        marker = self.tmp / "should-not-exist"
        harness = write(
            self.tmp / "harness_stub.py",
            f"import pathlib\npathlib.Path({str(marker)!r}).write_text('ran')\n",
        )
        result = script(
            repo, "fleet.py", "wake", "--state-dir", str(state_dir),
            "--harness-cmd", f"{PYTHON} {harness}", env=env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(marker.exists())
        record = json.loads((state_dir / "wakes.jsonl").read_text().splitlines()[0])
        self.assertEqual(record["action"], "quiet")


class DoctorTests(RepoTestCase):
    """Issue #19: blast-radius advice becomes checks, not hope."""

    def _env(self, scenario: Dict[str, Any], linger: str = "yes") -> Dict[str, str]:
        repo = self._repo
        scenario_path = self.tmp / "doctor-scenario.json"
        scenario_path.write_text(json.dumps(scenario))
        bin_dir = path_with_only(self.tmp, ("git",))
        gh = write(
            bin_dir / "gh",
            f"#!{PYTHON}\n"
            "import datetime, email.utils, json, os, sys\n"
            "args = sys.argv[1:]\n"
            "sc = json.loads(open(os.environ['DOCTOR_SCENARIO']).read())\n"
            "if args[:2] == ['auth', 'status']:\n"
            "    print('github.com', file=sys.stderr)\n"
            "    print(f\"  - Token scopes: {sc['scopes']}\", file=sys.stderr)\n"
            "    sys.exit(0)\n"
            "if args[:2] == ['repo', 'view']:\n"
            "    print(json.dumps({'nameWithOwner': 'o/r', 'viewerPermission': sc.get('permission', 'WRITE')}))\n"
            "    sys.exit(0)\n"
            "if args and args[0] == 'api':\n"
            "    joined = ' '.join(args)\n"
            "    if 'protection' in joined:\n"
            "        if sc.get('protection'):\n"
            "            print(json.dumps({'required_status_checks': {'contexts': ['check']}}))\n"
            "            sys.exit(0)\n"
            "        print('Branch not protected', file=sys.stderr)\n"
            "        sys.exit(1)\n"
            "    if '-i' in args:\n"
            "        print('HTTP/2 200')\n"
            "        print('Date: ' + email.utils.format_datetime(datetime.datetime.now(datetime.timezone.utc)))\n"
            "        print('{}')\n"
            "        sys.exit(0)\n"
            "    print('{}')\n"
            "    sys.exit(0)\n"
            "sys.exit(1)\n",
        )
        gh.chmod(0o755)
        loginctl = write(bin_dir / "loginctl", "#!/bin/sh\nprintf '%s\\n' \"$LINGER_VALUE\"\n")
        loginctl.chmod(0o755)
        systemctl = write(
            bin_dir / "systemctl",
            "#!/bin/sh\ncase \"$*\" in *orchestrator-wake*) printf 'active\\n' ;; *) printf 'inactive\\n'; exit 1 ;; esac\n",
        )
        systemctl.chmod(0o755)
        return env_with_path(
            bin_dir,
            DOCTOR_SCENARIO=str(scenario_path),
            LINGER_VALUE=linger,
            ORCH_MODEL_LADDER="provider/a provider/b",
        )

    @property
    def _repo(self) -> Path:
        if not hasattr(self, "_cached_repo"):
            repo, _ = python_repo(self.tmp)
            commit(repo)
            self._cached_repo = repo
        return self._cached_repo

    def _doctor(self, scenario, linger="yes"):
        state_dir = self.tmp / "state"
        state_dir.mkdir(exist_ok=True)
        result = script(
            self._repo, "fleet.py", "doctor", "--json",
            "--state-dir", str(state_dir), env=self._env(scenario, linger),
        )
        checks = {}
        if result.stdout.strip():
            data = json.loads(result.stdout)
            checks = {item["name"]: item for item in data["checks"]}
        return result, checks

    GOOD = {"scopes": "'repo:status'", "protection": True, "permission": "WRITE"}

    def test_all_good_fixture_exits_zero(self):
        result, checks = self._doctor(self.GOOD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(checks["linger"]["status"], "ok")
        self.assertEqual(checks["token-scopes"]["status"], "ok")

    def test_missing_linger_is_named_hard_failure(self):
        result, checks = self._doctor(self.GOOD, linger="no")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(checks["linger"]["status"], "fail")

    def test_overscoped_token_is_named_hard_failure(self):
        scenario = dict(self.GOOD, scopes="'repo', 'read:org'")
        result, checks = self._doctor(scenario)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(checks["token-scopes"]["status"], "fail")
        self.assertIn("repo", checks["token-scopes"]["detail"])


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
            "assets/wake.sh",
            "assets/orchestrator-wake.service",
            "assets/orchestrator-wake.timer",
            "assets/install-watchdog.sh",
            "assets/gitattributes.fragment.txt",
            "assets/gitignore.fragment.txt",
            "scripts/autonomous/fleet.py",
        ):
            self.assertTrue((SKILL_ROOT / relative).exists(), relative)


if __name__ == "__main__":
    unittest.main()
