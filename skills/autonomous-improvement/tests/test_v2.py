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
        for name in ("ledger.py", "mutation_engine.py", "orchestrator.py"):
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
            "assets/orchestrator-watchdog.service",
            "assets/orchestrator-watchdog.timer",
            "assets/install-watchdog.sh",
            "assets/gitattributes.fragment.txt",
            "assets/gitignore.fragment.txt",
        ):
            self.assertTrue((SKILL_ROOT / relative).exists(), relative)


if __name__ == "__main__":
    unittest.main()
