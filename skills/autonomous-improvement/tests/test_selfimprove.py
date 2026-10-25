"""Phase 5.5: the self-improvement loop's mechanical invariants.

A diff touching a protected path is refused BEFORE application; an
unverifiable change (no recorded matrix results) fails evaluation and is
reverted; every revert appends the negative-ledger line.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support

EVALS = support.SKILL_ROOT / "evals"
SELFIMPROVE = EVALS / "selfimprove.py"


class SelfImproveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _patch(self, name: str, path: str) -> Path:
        patch = self.tmp / name
        patch.write_text(
            f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@\n-a\n+b\n",
            encoding="utf-8",
        )
        return patch

    def run_tool(self, *args: str):
        return support.run([support.PYTHON, str(SELFIMPROVE), *args], cwd=support.SKILL_ROOT)

    def test_protected_diff_is_refused_before_application(self) -> None:
        for protected in (
            "skills/autonomous-improvement/evals/evals.json",
            "skills/autonomous-improvement/tests/pins.json",
            "skills/autonomous-improvement/tests/test_weakening_check.py",
        ):
            proc = self.run_tool(
                "apply", "--diff", str(self._patch(f"p-{protected[-6:]}.patch", protected)),
                "--motivation", "try to weaken the referee",
            )
            self.assertEqual(proc.returncode, 2, protected)
            self.assertIn("protected path", proc.stderr)
            self.assertIn("refused", proc.stderr)

    def test_reference_08_contract_diff_is_owner_only(self) -> None:
        proc = self.run_tool(
            "apply",
            "--diff",
            str(self._patch("r08.patch", "skills/autonomous-improvement/references/08-recursive-self-improvement.md")),
            "--motivation", "loosen the stream contract",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("owner-only", proc.stderr)

    def test_evaluate_fails_without_recorded_matrix(self) -> None:
        # Hermetic: an empty results dir (the real evals/results carries
        # committed measurement records).
        empty = self.tmp / "no-records"
        empty.mkdir()
        proc = self.run_tool(
            "evaluate",
            "--worktree", str(support.SKILL_ROOT.parent.parent),
            "--results-dir", str(empty),
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("UNVERIFIABLE", proc.stderr)

    def test_revert_writes_negative_ledger_line(self) -> None:
        why = "tried X to lift m-ts-weak; deterministic layer could not verify it"
        proc = self.run_tool(
            "revert", "--worktree", str(self.tmp / "never-existed"), "--why", why
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        ledger = support.SKILL_ROOT / ".autonomous" / "negative-ledger.md"
        self.assertIn(why, ledger.read_text(encoding="utf-8"))

    def test_propose_lists_protocol_and_never_spends(self) -> None:
        proc = self.run_tool("propose")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("one change", proc.stdout)


if __name__ == "__main__":
    unittest.main()
