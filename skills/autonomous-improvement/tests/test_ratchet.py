"""Phase 7: the ratchet stream — measurable proxies with a strict-improvement oracle.

``scan`` measures a configured metric on the base ref and at HEAD (0 tokens,
worktree-isolated); ``check`` is the mechanical oracle: a diff is accepted
only when it stays in scope, keeps the targeted tests green, and STRICTLY
improves the metric; ``scaffold-issue`` is the T1 wave's only output (a
proposal for a human — the stream never commits). Refuses to inspect a diff
that touches the ratchet's own config: no self-modification.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support

SCRIPTS = support.SCRIPTS
RATCHET = SCRIPTS / "ratchet.py"

CONFIG = {
    "base_ref": "main",
    "ratchets": [
        {
            "name": "todos",
            "cmd": ["grep", "-c", "TODO", "src/app.py"],
            "parse": {"regex": r"^(\d+)$", "group": 1},
            "direction": "minimize",
            "scope": ["src/**"],
            "targeted_test_cmd": [
                "python3", "-m", "unittest", "discover", "-s", "tests", "-t", "."
            ],
        },
        {
            "name": "todos-wide",
            "cmd": ["grep", "-c", "TODO", "src/app.py"],
            "parse": {"regex": r"^(\d+)$", "group": 1},
            "direction": "minimize",
            "scope": ["src/**", "tests/**"],
            "targeted_test_cmd": [
                "python3", "-m", "unittest", "discover", "-s", "tests", "-t", "."
            ],
        },
        {
            "name": "broken",
            "cmd": ["/nonexistent/metric-binary"],
            "parse": {"regex": r"^(\d+)$", "group": 1},
            "direction": "minimize",
            "scope": ["src/**"],
            "targeted_test_cmd": ["true"],
        },
    ],
}

APP = "def f():\n    return 1  # TODO one\n# TODO two\n# TODO three\n"
APP_TWO = "def f():\n    return 1  # TODO one\n# TODO two\n"
APP_FOUR = APP + "# TODO four\n"
TESTS = (
    "import unittest\nimport src.app\n\n"
    "class AppTests(unittest.TestCase):\n"
    "    def test_f(self):\n"
    "        self.assertEqual(src.app.f(), 1)\n"
)
TESTS_RED = (
    "import unittest\nimport src.app\n\n"
    "class AppTests(unittest.TestCase):\n"
    "    def test_f(self):\n"
    "        self.assertEqual(src.app.f(), 2)\n"
)


def _make_diff(repo: Path, changes: dict) -> Path:
    """Write the changed contents, capture a real `git diff`, restore HEAD."""
    for rel, content in changes.items():
        support.write(repo / rel, content)
    proc = support.run(["git", "diff"], cwd=repo)
    support.run(["git", "checkout", "--", "."], cwd=repo)
    holder = tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False)
    holder.write(proc.stdout)
    holder.close()
    return Path(holder.name)


def _diff(patch: str) -> Path:
    holder = tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False)
    holder.write(patch)
    holder.close()
    return Path(holder.name)


class RatchetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        support.run(["git", "init", "-q", "-b", "main"], cwd=self.repo)
        support.git(self.repo, "config", "user.email", "test@example.com")
        support.git(self.repo, "config", "user.name", "Test User")
        support.write(self.repo / "src" / "app.py", APP)
        support.write(self.repo / "tests" / "__init__.py", "")
        support.write(self.repo / "tests" / "test_app.py", TESTS)
        cfg = dict(CONFIG)
        support.write(
            self.repo / ".autonomous" / "config.json", json.dumps(cfg, indent=2)
        )
        support.write(self.repo / "README.md", "hello\n")
        support.commit(self.repo, "base")

    def run_tool(self, *args: str, cwd: Path = None):
        return support.run(
            [support.PYTHON, str(RATCHET), *args], cwd=cwd or self.repo
        )

    def test_scan_equal_is_not_improving(self) -> None:
        proc = self.run_tool("scan", "--ratchet", "todos")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["schema"], "ratchet/1")
        self.assertEqual(payload["base_value"], 3)
        self.assertEqual(payload["head_value"], 3)
        self.assertFalse(payload["improving"])

    def test_scan_is_deterministic(self) -> None:
        first = self.run_tool("scan", "--ratchet", "todos")
        second = self.run_tool("scan", "--ratchet", "todos")
        self.assertEqual(first.stdout, second.stdout)

    def test_scan_names_a_broken_metric_without_traceback(self) -> None:
        proc = self.run_tool("scan", "--ratchet", "broken")
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertTrue(proc.stderr.strip())

    def test_check_accepts_a_strict_improvement(self) -> None:
        patch = _make_diff(self.repo, {"src/app.py": APP_TWO})
        proc = self.run_tool("check", "--ratchet", "todos", "--diff", str(patch))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["verdict"], "improving")
        self.assertEqual(payload["new_value"], 2)
        self.assertEqual(payload["tests"], "pass")

    def test_check_rejects_a_worse_metric(self) -> None:
        patch = _make_diff(self.repo, {"src/app.py": APP_FOUR})
        proc = self.run_tool("check", "--ratchet", "todos", "--diff", str(patch))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("did not improve", proc.stderr)

    def test_check_rejects_an_equal_metric(self) -> None:
        patch = _make_diff(
            self.repo,
            {"src/app.py": APP.replace("# TODO two", "# TODO two (reworded)")},
        )
        proc = self.run_tool("check", "--ratchet", "todos", "--diff", str(patch))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("did not improve", proc.stderr)

    def test_check_rejects_red_tests(self) -> None:
        patch = _make_diff(
            self.repo, {"src/app.py": APP_TWO, "tests/test_app.py": TESTS_RED}
        )
        proc = self.run_tool("check", "--ratchet", "todos-wide", "--diff", str(patch))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("tests red", proc.stderr)

    def test_check_refuses_out_of_scope_paths(self) -> None:
        patch = _make_diff(self.repo, {"README.md": "goodbye\n"})
        proc = self.run_tool("check", "--ratchet", "todos", "--diff", str(patch))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("scope", proc.stderr)

    def test_check_refuses_self_modification(self) -> None:
        # A config change cannot be produced via `git diff` (the file is
        # gitignored in real repos); synthesize a minimal valid patch.
        patch = _diff(
            "diff --git a/.autonomous/config.json b/.autonomous/config.json\n"
            "--- a/.autonomous/config.json\n+++ b/.autonomous/config.json\n"
            "@@ -1,3 +1,4 @@\n"
            " {\n"
            '   "base_ref": "main",\n'
            "+  \"tampered\": true\n"
            " }\n"
        )
        proc = self.run_tool("check", "--ratchet", "todos", "--diff", str(patch))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("config", proc.stderr)

    def test_scaffold_issue_is_proposal_only(self) -> None:
        proc = self.run_tool("scaffold-issue", "--ratchet", "todos")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Proposal only", proc.stdout)
        self.assertIn("todos", proc.stdout)


if __name__ == "__main__":
    unittest.main()
