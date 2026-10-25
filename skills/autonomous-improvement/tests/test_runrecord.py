"""Phase 5.1: run records — structured, redacted-by-construction history.

``runrecord.py write`` appends one ``run-record/1`` JSON per run under
``.autonomous/run-records/`` (counts, ids and URLs only — no transcript
text, no secrets). ``sync`` joins later verdicts (merged/closed from gh,
ignored from ledger REJECTED/WONTFIX rows). The wake launcher writes a
fallback record with the exit code and duration so a record exists even
when the model forgets or dies.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import support

SCRIPTS = support.SCRIPTS
RUNRECORD = SCRIPTS / "runrecord.py"


class RunRecordWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = support.tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.repo, _ = support.python_repo(self.tmp, name="rr")
        support.commit(self.repo, "base2")

    def write(self, *args: str):
        return support.run(
            [support.PYTHON, str(RUNRECORD), "write", *args], cwd=self.repo
        )

    def records(self) -> list:
        return sorted((self.repo / ".autonomous" / "run-records").glob("rr-*.json"))

    def test_write_creates_sequential_ids_and_schema(self) -> None:
        first = self.write("--outcome", "no-gap", "--chosen", "docs-reconcile")
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.write("--outcome", "pr-opened", "--chosen", "mutation")
        self.assertEqual(second.returncode, 0, second.stderr)
        paths = self.records()
        self.assertEqual([p.name for p in paths], ["rr-0001.json", "rr-0002.json"])
        record = json.loads(paths[0].read_text(encoding="utf-8"))
        self.assertEqual(record["schema"], "run-record/1")
        self.assertEqual(record["outcome"], "no-gap")
        self.assertEqual(record["chosen"], "docs-reconcile")
        self.assertEqual(record["questions_asked"], 0)
        self.assertIsNone(record["tokens"])
        self.assertTrue(record["skill_ref"])
        self.assertTrue(record["ts"])

    def test_record_is_redacted_by_construction(self) -> None:
        # Counts and ids only: no field may carry transcript-like text.
        self.write("--outcome", "blocked", "--questions", "1")
        record = json.loads(self.records()[0].read_text(encoding="utf-8"))
        allowed = {
            "schema", "id", "ts", "skill_ref", "repo_slug", "harness", "model",
            "autonomy", "environment", "source", "passes_run", "chosen",
            "runner_up", "questions_asked", "tokens", "wall_s", "artifacts",
            "outcome", "blockers", "exit_code", "verdict",
        }
        self.assertEqual(set(record) - allowed, set())

    def test_repo_slug_falls_back_to_directory_name(self) -> None:
        self.write("--outcome", "no-gap")
        record = json.loads(self.records()[0].read_text(encoding="utf-8"))
        self.assertEqual(record["repo_slug"], "rr")

    def test_list_prints_ids_and_outcomes(self) -> None:
        self.write("--outcome", "no-gap")
        self.write("--outcome", "error")
        proc = support.run([support.PYTHON, str(RUNRECORD), "list"], cwd=self.repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("rr-0001", proc.stdout)
        self.assertIn("no-gap", proc.stdout)


class RunRecordSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = support.tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.repo, _ = support.python_repo(self.tmp, name="sync")
        support.commit(self.repo, "base2")

    def _write_record(self, name: str, **fields) -> Path:
        record = {
            "schema": "run-record/1",
            "id": name,
            "ts": "2026-10-20T10:00:00+00:00",
            "skill_ref": "abc",
            "repo_slug": "owner/repo",
            "harness": "opencode",
            "model": "x/y",
            "autonomy": "auto",
            "environment": "laptop",
            "source": "session",
            "passes_run": ["mutation"],
            "chosen": "mutation",
            "runner_up": None,
            "questions_asked": 0,
            "tokens": None,
            "wall_s": None,
            "artifacts": ["#12"],
            "outcome": "pr-opened",
            "blockers": [],
        }
        record.update(fields)
        path = self.repo / ".autonomous" / "run-records" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        return path

    def test_sync_marks_merged_from_gh(self) -> None:
        self._write_record("rr-0001")
        import shlex

        payload = json.dumps(
            {"state": "MERGED", "mergedAt": "2026-10-21T00:00:00Z", "number": 12}
        )
        bin_dir = support.stub_path(
            self.tmp,
            {"gh": f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(payload)}\n"},
        )
        (bin_dir / "git").symlink_to(__import__("shutil").which("git"))
        proc = support.run(
            [support.PYTHON, str(RUNRECORD), "sync"],
            cwd=self.repo,
            env=support.env_with_path(bin_dir),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        record = json.loads(
            (self.repo / ".autonomous" / "run-records" / "rr-0001.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(record["verdict"], "merged")

    def test_sync_marks_ignored_from_ledger_rejections(self) -> None:
        # The ledger row is stream A (Mutation) → tool "mutation"; the record
        # that chose that tool inherits the REJECTED verdict.
        self._write_record("rr-0002", chosen="mutation", outcome="issue-filed")
        ledger = self.repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md"
        ledger.write_text(
            support.ledger_text(
                support.row("src/app.py", "ab12cd", status="REJECTED"),
            ),
            encoding="utf-8",
        )
        proc = support.run([support.PYTHON, str(RUNRECORD), "sync"], cwd=self.repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        record = json.loads(
            (self.repo / ".autonomous" / "run-records" / "rr-0002.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(record["verdict"], "ignored")


class MetaRecordsTests(unittest.TestCase):
    """5.3: meta.py lessons --records converts run records to history rows."""

    def test_records_become_fleet_history_rows(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            repo, _ = support.python_repo(tmp, name="meta")
            support.commit(repo, "base2")
            records = repo / ".autonomous" / "run-records"
            records.mkdir(parents=True)
            (records / "rr-0001.json").write_text(
                json.dumps(
                    {
                        "schema": "run-record/1", "id": "rr-0001",
                        "ts": "2026-10-20T10:00:00+00:00", "chosen": "mutation",
                        "source": "session", "outcome": "pr-opened",
                        "verdict": "merged", "tokens": 12000,
                        "artifacts": ["#12"],
                    }
                ),
                encoding="utf-8",
            )
            (records / "rr-0002.json").write_text(
                json.dumps(
                    {
                        "schema": "run-record/1", "id": "rr-0002",
                        "ts": "2026-10-21T10:00:00+00:00", "chosen": "docs-reconcile",
                        "source": "stateless-wake", "outcome": "issue-filed",
                        "verdict": None, "tokens": None, "artifacts": [],
                    }
                ),
                encoding="utf-8",
            )
            proc = support.run(
                [
                    support.PYTHON,
                    str(SCRIPTS / "meta.py"),
                    "lessons",
                    "--records",
                    ".autonomous/run-records",
                ],
                cwd=repo,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["schema"], "fleet-history/1")
            rows = {row["id"]: row for row in payload["rows"]}
            self.assertEqual(rows["rr-0001"]["stream"], "A")
            self.assertEqual(rows["rr-0001"]["outcome"], "accepted")
            self.assertEqual(rows["rr-0001"]["tokens"], 12000)
            self.assertEqual(rows["rr-0002"]["stream"], "I")
            self.assertEqual(rows["rr-0002"]["outcome"], "open")
            self.assertEqual(rows["rr-0002"]["tokens"], 0)


if __name__ == "__main__":
    unittest.main()
