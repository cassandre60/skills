"""The rollup judges one run per check context, on the PR head.

``gh`` keeps a superseded run in a PR's ``statusCheckRollup`` after a re-run,
so judging every entry let a CANCELLED attempt that a newer push replaced count
as a failure. The fleet called green PRs red when that happened (#376, #398).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "autonomous"
sys.path.insert(0, str(SCRIPTS))

import fleet  # noqa: E402

HEAD = "1" * 40
OLD = "2" * 40


def check(name: str, conclusion: str, at: str = "", sha: str = "") -> dict:
    item = {"name": name, "status": "COMPLETED", "conclusion": conclusion}
    if at:
        item["completedAt"] = at
    if sha:
        item["commit"] = {"oid": sha}
    return item


def state(rollup: list) -> dict:
    return fleet.evaluate_pr_checks(
        {"number": 1, "headRefOid": HEAD, "statusCheckRollup": rollup}
    )


class SupersededRunTests(unittest.TestCase):
    def test_a_superseded_cancellation_does_not_redden_a_green_pr(self) -> None:
        result = state(
            [
                check("T1 fast gates", "CANCELLED", "2026-10-22T20:00:00Z", HEAD),
                check("Docs check", "SUCCESS", "2026-10-22T20:05:00Z", HEAD),
                check("T1 fast gates", "SUCCESS", "2026-10-22T20:10:00Z", HEAD),
                check("Tests", "SUCCESS", "2026-10-22T20:15:00Z", HEAD),
                check("T3 red-team recall", "SKIPPED", "2026-10-22T20:15:00Z", HEAD),
            ]
        )
        self.assertEqual(result["checks"], "green")
        self.assertIsNone(result["red_kind"])

    def test_the_verdict_that_replaced_a_cancellation_wins(self) -> None:
        result = state(
            [
                check("T1 fast gates", "CANCELLED"),
                check("T1 fast gates", "SUCCESS"),
            ]
        )
        self.assertEqual(result["checks"], "green")

    def test_a_newer_failure_is_red_not_hidden_by_an_older_success(self) -> None:
        result = state(
            [
                check("Tests", "SUCCESS", "2026-10-22T20:00:00Z", HEAD),
                check("Tests", "FAILURE", "2026-10-22T20:10:00Z", HEAD),
            ]
        )
        self.assertEqual(result["checks"], "red")

    def test_a_cancellation_only_context_waits_instead_of_failing(self) -> None:
        result = state([check("Tests", "CANCELLED", "2026-10-22T20:00:00Z", HEAD)])
        self.assertEqual(result["checks"], "pending")

    def test_runs_from_another_commit_are_not_this_commits_verdict(self) -> None:
        result = state(
            [
                check("Tests", "SUCCESS", "2026-10-22T20:00:00Z", OLD),
                check("T1 fast gates", "SUCCESS", "2026-10-22T20:00:00Z", OLD),
            ]
        )
        self.assertEqual(result["checks"], "pending")

    def test_a_current_failure_is_red_next_to_a_superseded_cancellation(self) -> None:
        result = state(
            [
                check("T1 fast gates", "CANCELLED", "2026-10-22T20:00:00Z", HEAD),
                check("T1 fast gates", "FAILURE", "2026-10-22T20:10:00Z", HEAD),
            ]
        )
        self.assertEqual(result["checks"], "red")


if __name__ == "__main__":
    unittest.main()
