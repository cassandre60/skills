"""Tests for scripts/autonomous/profile.py (Phase 5.2).

`profile.py update` turns run records (schema run-record/1) plus the work
ledger's REJECTED/WONTFIX rows into .autonomous/profile.json (schema
profile/1): per-pass Laplace-smoothed acceptance priors, a 30-day
suppression after 3 consecutive rejections, and lessons that expire 90 days
after their last supporting decision. Every test builds a throwaway git
repository; --now pins the clock so outputs are deterministic. No network.
"""

from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional

from support import (
    init_repo,
    install_scripts,
    ledger_text,
    row,
    script,
    write,
)

KNOWN_TOOLS = {
    "mutation",
    "adversarial",
    "fuzz",
    "defect-repair",
    "flake",
    "performance",
    "security",
    "docs-reconcile",
    "simplification",
}
NOW = "2026-10-25T12:00:00+00:00"


class ProfileTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def make(self, name: str = "repo") -> Path:
        repo, _ = init_repo(self.tmp, name=name)
        install_scripts(repo)
        (repo / ".autonomous" / "run-records").mkdir(parents=True)
        return repo

    def update(self, repo: Path, *extra: str):
        proc = script(repo, "profile.py", "update", "--now", NOW, *extra)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        return proc

    def run_json(self, repo: Path, *extra: str) -> Dict[str, Any]:
        return json.loads(self.update(repo, *extra).stdout)

    def record(
        self,
        repo: Path,
        rid: str,
        ts: str,
        chosen: str = "mutation",
        verdict: Optional[str] = None,
        slug: str = "owner/repo",
    ) -> Path:
        payload: Dict[str, Any] = {
            "id": rid,
            "ts": ts,
            "skill_ref": "abc123",
            "repo_slug": slug,
            "harness": "opencode",
            "model": "x/y",
            "autonomy": "auto",
            "environment": "laptop",
            "passes_run": [chosen],
            "chosen": chosen,
            "runner_up": "adversarial",
            "questions_asked": 0,
            "tokens": 12000,
            "wall_s": 61.0,
            "artifacts": [],
            "outcome": "pr-opened",
            "blockers": [],
        }
        if verdict is not None:
            payload["verdict"] = verdict
        return write(
            repo / ".autonomous" / "run-records" / f"{rid}.json",
            json.dumps(payload, indent=2) + "\n",
        )


class NeutralDefaultTests(ProfileTestCase):
    def test_empty_inputs_give_neutral_priors_and_explanatory_note(self):
        repo = self.make()
        proc = self.update(repo)
        data = json.loads(proc.stdout)

        self.assertEqual(data["schema"], "profile/1")
        self.assertEqual(data["updated_at"], NOW)
        self.assertEqual(set(data["passes"]), KNOWN_TOOLS)
        for tool in KNOWN_TOOLS:
            entry = data["passes"][tool]
            self.assertEqual(entry["decided"], 0, tool)
            self.assertEqual(entry["accepted"], 0, tool)
            self.assertEqual(entry["smoothed_rate"], 0.5, tool)
            self.assertEqual(entry["prior"], 1.0, tool)
            self.assertIsNone(entry["last_decided"], tool)
            self.assertIsNone(entry["suppressed_until"], tool)
            self.assertEqual(entry["consecutive_rejections"], 0, tool)
        self.assertEqual(data["lessons"], [])
        self.assertEqual(data["expired_lessons"], [])
        # The note must explain the missing profile and the prior-1.0 semantics.
        self.assertIn("neutral", data["note"])
        self.assertIn("1.0", data["note"])

        # The profile is written to --out byte-identical to stdout.
        out = repo / ".autonomous" / "profile.json"
        self.assertTrue(out.is_file())
        self.assertEqual(out.read_text(encoding="utf-8"), proc.stdout)


class CountingTests(ProfileTestCase):
    def test_merged_and_closed_records_smooth(self):
        repo = self.make()
        self.record(repo, "rr-0001", "2026-10-01T10:00:00+00:00", verdict="merged")
        self.record(repo, "rr-0002", "2026-10-02T10:00:00+00:00", verdict="merged")
        self.record(repo, "rr-0003", "2026-10-03T10:00:00+00:00", verdict="closed")
        # A record without a verdict is not a decision and must not count.
        self.record(repo, "rr-0004", "2026-10-05T10:00:00+00:00")

        data = self.run_json(repo)
        entry = data["passes"]["mutation"]
        self.assertEqual(entry["decided"], 3)
        self.assertEqual(entry["accepted"], 2)
        self.assertEqual(entry["smoothed_rate"], 0.6)
        self.assertEqual(entry["prior"], 1.2)
        self.assertEqual(entry["last_decided"], "2026-10-03T10:00:00+00:00")
        self.assertEqual(entry["consecutive_rejections"], 1)
        self.assertIsNone(entry["suppressed_until"])

        self.assertEqual(len(data["lessons"]), 1)
        lesson = data["lessons"][0]
        self.assertEqual(lesson["scope"], "owner/repo")
        expected = (
            dt.datetime.fromisoformat("2026-10-03T10:00:00+00:00")
            + dt.timedelta(days=90)
        ).isoformat()
        self.assertEqual(lesson["revalidate_by"], expected)
        for rid in ("rr-0001", "rr-0002", "rr-0003"):
            self.assertIn(rid, lesson["evidence"])
        self.assertNotIn("rr-0004", lesson["evidence"])
        self.assertEqual(data["expired_lessons"], [])
        # Passes without data keep the neutral prior.
        self.assertEqual(data["passes"]["fuzz"]["decided"], 0)
        self.assertEqual(data["passes"]["fuzz"]["prior"], 1.0)


class LedgerRejectionTests(ProfileTestCase):
    def test_rejected_and_wontfix_rows_count_for_their_stream(self):
        repo = self.make()
        write(
            repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md",
            ledger_text(
                row("src/a.py", "aaaa1111", status="SOUND", date="2026-09-01"),
                row("src/b.py", "bbbb2222", status="REJECTED", date="2026-09-02"),
                row("src/c.py", "cccc3333", status="WONTFIX", date="2026-09-03"),
                "| 2026-09-04 | Z (Unknown) | `src/d.py` | `dddd4444` | REJECTED"
                " | — | nope | Any modification. |",
            ),
        )

        data = self.run_json(repo)
        entry = data["passes"]["mutation"]
        # REJECTED + WONTFIX count; SOUND and the unknown stream Z do not.
        self.assertEqual(entry["decided"], 2)
        self.assertEqual(entry["accepted"], 0)
        self.assertEqual(entry["smoothed_rate"], 0.25)
        self.assertEqual(entry["prior"], 0.5)
        self.assertEqual(entry["last_decided"], "2026-09-03T00:00:00+00:00")
        self.assertEqual(entry["consecutive_rejections"], 2)
        self.assertIsNone(entry["suppressed_until"])
        self.assertEqual(len(data["lessons"]), 1)
        self.assertIn("ledger", data["lessons"][0]["evidence"])


class SuppressionTests(ProfileTestCase):
    def test_three_consecutive_rejections_suppress_for_30_days(self):
        repo = self.make()
        t3 = "2026-08-03T09:00:00+00:00"
        self.record(repo, "rr-0001", "2026-08-01T09:00:00+00:00", verdict="closed")
        self.record(repo, "rr-0002", "2026-08-02T09:00:00+00:00", verdict="closed")
        self.record(repo, "rr-0003", t3, verdict="closed")

        entry = self.run_json(repo)["passes"]["mutation"]
        self.assertEqual(entry["decided"], 3)
        self.assertEqual(entry["consecutive_rejections"], 3)
        expected = (
            dt.datetime.fromisoformat(t3) + dt.timedelta(days=30)
        ).isoformat()
        self.assertEqual(entry["suppressed_until"], expected)
        self.assertEqual(entry["last_decided"], t3)

    def test_later_merged_verdict_clears_the_suppression(self):
        repo = self.make()
        for index, verdict in enumerate(("closed", "closed", "closed", "merged"), start=1):
            self.record(
                repo,
                f"rr-{index:04d}",
                f"2026-08-{index:02d}T09:00:00+00:00",
                verdict=verdict,
            )

        entry = self.run_json(repo)["passes"]["mutation"]
        self.assertEqual(entry["decided"], 4)
        self.assertEqual(entry["accepted"], 1)
        self.assertEqual(entry["consecutive_rejections"], 0)
        self.assertIsNone(entry["suppressed_until"])

    def test_acceptance_between_rejections_breaks_the_streak(self):
        repo = self.make()
        self.record(repo, "rr-0001", "2026-08-01T09:00:00+00:00", verdict="closed")
        self.record(repo, "rr-0002", "2026-08-02T09:00:00+00:00", verdict="merged")
        self.record(repo, "rr-0003", "2026-08-03T09:00:00+00:00", verdict="closed")

        entry = self.run_json(repo)["passes"]["mutation"]
        self.assertEqual(entry["consecutive_rejections"], 1)
        self.assertIsNone(entry["suppressed_until"])


class DeterminismTests(ProfileTestCase):
    def test_same_inputs_and_now_give_byte_identical_output(self):
        repo = self.make()
        self.record(repo, "rr-0001", "2026-10-01T10:00:00+00:00", verdict="merged")
        self.record(
            repo, "rr-0002", "2026-10-02T10:00:00+00:00", verdict="closed", chosen="flake"
        )
        write(
            repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md",
            ledger_text(row("src/a.py", "aaaa1111", status="REJECTED", date="2026-10-03")),
        )

        first = self.update(repo)
        first_file = (repo / ".autonomous" / "profile.json").read_text(encoding="utf-8")
        second = self.update(repo)
        second_file = (repo / ".autonomous" / "profile.json").read_text(encoding="utf-8")

        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first_file, second_file)
        self.assertEqual(first_file, first.stdout)
        self.assertIn("flake", first.stdout)


class ExpiryTests(ProfileTestCase):
    def test_lesson_past_revalidate_by_is_demoted(self):
        repo = self.make()
        self.record(repo, "rr-0001", "2026-01-05T10:00:00+00:00", verdict="merged")
        self.record(
            repo, "rr-0002", "2026-10-01T10:00:00+00:00", verdict="merged", chosen="flake"
        )

        data = self.run_json(repo)
        fresh_by = (
            dt.datetime.fromisoformat("2026-10-01T10:00:00+00:00")
            + dt.timedelta(days=90)
        ).isoformat()
        stale_by = (
            dt.datetime.fromisoformat("2026-01-05T10:00:00+00:00")
            + dt.timedelta(days=90)
        ).isoformat()

        self.assertEqual(
            [lesson["revalidate_by"] for lesson in data["lessons"]], [fresh_by]
        )
        self.assertEqual(
            [lesson["revalidate_by"] for lesson in data["expired_lessons"]], [stale_by]
        )
        self.assertIn("rr-0002", data["lessons"][0]["evidence"])
        self.assertIn("rr-0001", data["expired_lessons"][0]["evidence"])
        for lesson in data["lessons"] + data["expired_lessons"]:
            self.assertEqual(lesson["scope"], "owner/repo")


if __name__ == "__main__":
    unittest.main()
