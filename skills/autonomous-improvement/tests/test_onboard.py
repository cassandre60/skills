"""Phase 4.4: onboarding — detect, write and verify a repo's own config.

Red-first suite for ``scripts/autonomous/onboard.py``: ``detect`` derives a
test command, test_for_source mappings and stream-A globs from manifests,
CI files and the repo's own test naming (no YAML parser, argv-only, shell
metacharacters refused); ``write`` turns a detection into
``.autonomous/config.json``; ``verify`` runs the command once in a
temporary git worktree and records the baseline verdict.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

import support

SKILL_ROOT = support.SKILL_ROOT
SCRIPTS = support.SCRIPTS
ONBOARD = SCRIPTS / "onboard.py"
PYTHON_DEFAULT = ["python3", "-m", "unittest", "discover", "-s", "tests", "-t", "."]

sys.path.insert(0, str(SCRIPTS))
import common  # noqa: E402

PYPROJECT = '[project]\nname = "demo"\nversion = "0.1.0"\n'
APP = "def f(a, b):\n    return a == b\n"
TEST_GREEN = (
    "import unittest\n\nfrom src.app import f\n\n\n"
    "class AppTests(unittest.TestCase):\n"
    "    def test_f(self):\n"
    "        self.assertTrue(f(1, 1))\n"
)
TEST_RED = (
    "import unittest\n\nfrom src.app import f\n\n\n"
    "class AppTests(unittest.TestCase):\n"
    "    def test_f(self):\n"
    "        self.assertTrue(f(1, 2))\n"
)


class OnboardTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def onboard(self, repo: Path, *args: str):
        return support.run([support.PYTHON, str(ONBOARD), *args], cwd=repo)

    def detect(self, repo: Path) -> dict:
        proc = self.onboard(repo, "detect")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def python_repo(self, name: str, test_body: str = TEST_GREEN) -> Path:
        repo, _ = support.init_repo(self.tmp, name=name)
        support.write(repo / "pyproject.toml", PYPROJECT)
        support.write(repo / "src" / "app.py", APP)
        support.write(repo / "tests" / "__init__.py", "")
        support.write(repo / "tests" / "test_app.py", test_body)
        support.commit(repo, "base")
        return repo

    def config_on_disk(self, repo: Path) -> dict:
        return json.loads(
            (repo / ".autonomous" / "config.json").read_text(encoding="utf-8")
        )


class OnboardDetectTests(OnboardTestCase):
    def test_python_repo_finds_unittest_default(self) -> None:
        repo = self.python_repo("pyrepo")
        payload = self.detect(repo)
        self.assertEqual(payload["schema"], "onboard-detect/1")
        blocks = {p["package"]: p for p in payload["packages"]}
        self.assertIn(".", blocks)
        root = blocks["."]
        self.assertIn("python", root["stacks"])
        self.assertEqual(root["test_cmd"]["argv"], PYTHON_DEFAULT)
        self.assertEqual(root["test_cmd"]["source"], "stack-default")
        self.assertEqual(root["test_cmd"]["confidence"], "low")
        # The repo's own naming (src/app.py + tests/test_app.py) configures A.
        self.assertEqual(root["stream_a"]["include"], ["src/**/*.py"])
        mappings = root["test_for_source"]
        self.assertTrue(any(m["test"] == "tests/test_\\1.py" for m in mappings))

    def test_node_manifest_script_beats_stack_default(self) -> None:
        repo, _ = support.init_repo(self.tmp, name="noderepo")
        support.write(
            repo / "package.json",
            json.dumps({"name": "demo", "scripts": {"test": "node --test test/"}}),
        )
        support.write(repo / "test" / "app.test.js", "import assert from 'node:assert';\n")
        support.commit(repo, "base")
        payload = self.detect(repo)
        root = payload["packages"][0]
        self.assertIn("node", root["stacks"])
        self.assertEqual(root["test_cmd"]["argv"], ["node", "--test", "test/"])
        self.assertEqual(root["test_cmd"]["source"], "package-json-script")
        self.assertEqual(root["test_cmd"]["confidence"], "high")

    def test_ci_run_line_beats_manifest_script(self) -> None:
        repo, _ = support.init_repo(self.tmp, name="cirepo")
        support.write(
            repo / "package.json",
            json.dumps({"name": "demo", "scripts": {"test": "node --test test/"}}),
        )
        support.write(
            repo / ".github" / "workflows" / "ci.yml",
            "name: CI\n"
            "on: push\n"
            "jobs:\n"
            "  test:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - run: npm ci\n"
            "      - run: npx jest\n",
        )
        support.commit(repo, "base")
        payload = self.detect(repo)
        root = payload["packages"][0]
        self.assertEqual(root["test_cmd"]["argv"], ["npx", "jest"])
        self.assertEqual(root["test_cmd"]["source"], "ci-yaml-linescan")
        self.assertEqual(root["test_cmd"]["confidence"], "medium")

    def test_gitlab_script_line_detected(self) -> None:
        repo, _ = support.init_repo(self.tmp, name="glrepo")
        support.write(repo / "pyproject.toml", PYPROJECT)
        support.write(
            repo / ".gitlab-ci.yml",
            "stages: [test]\n" "test:\n" "  script:\n" "    - pip install -e .\n" "    - pytest -q\n",
        )
        support.commit(repo, "base")
        payload = self.detect(repo)
        root = payload["packages"][0]
        self.assertEqual(root["test_cmd"]["argv"], ["pytest", "-q"])
        self.assertEqual(root["test_cmd"]["source"], "ci-yaml-linescan")

    def test_metachar_commands_land_in_needs_owner_only(self) -> None:
        repo, _ = support.init_repo(self.tmp, name="needsowner")
        support.write(
            repo / "package.json",
            json.dumps({"name": "demo", "scripts": {"test": "node --test test/"}}),
        )
        support.write(
            repo / ".github" / "workflows" / "ci.yml",
            "name: CI\n"
            "on: push\n"
            "jobs:\n"
            "  test:\n"
            "    steps:\n"
            "      - run: pytest -q && echo done\n"
            "      - run: go test ./... | tee log.txt\n",
        )
        support.commit(repo, "base")
        payload = self.detect(repo)
        refused = {entry["command"]: entry for entry in payload["needs_owner"]}
        self.assertIn("pytest -q && echo done", refused)
        self.assertIn("go test ./... | tee log.txt", refused)
        self.assertIn("&&", refused["pytest -q && echo done"]["reason"])
        self.assertIn("|", refused["go test ./... | tee log.txt"]["reason"])
        # Never in the command map: every chosen argv must be metachar-free.
        for pkg in payload["packages"]:
            joined = " ".join(pkg["test_cmd"]["argv"])
            self.assertNotIn("&&", joined)
            self.assertNotIn("|", joined)
        # The usable manifest script survives the refused CI lines.
        root = payload["packages"][0]
        self.assertEqual(root["test_cmd"]["source"], "package-json-script")

    def test_makefile_target_detected(self) -> None:
        repo, _ = support.init_repo(self.tmp, name="makerepo")
        support.write(repo / "pyproject.toml", PYPROJECT)
        support.write(repo / "Makefile", "test:\n\tpython3 -m unittest discover -s tests -t .\n\nbuild:\n\techo hi\n")
        support.commit(repo, "base")
        payload = self.detect(repo)
        root = payload["packages"][0]
        self.assertEqual(root["test_cmd"]["argv"], ["python3", "-m", "unittest", "discover", "-s", "tests", "-t", "."])
        self.assertEqual(root["test_cmd"]["source"], "makefile-target")
        self.assertEqual(root["test_cmd"]["confidence"], "medium")

    def test_monorepo_one_block_per_package(self) -> None:
        repo, _ = support.init_repo(self.tmp, name="mono")
        support.write(
            repo / "packages" / "a" / "package.json",
            json.dumps({"name": "a", "scripts": {"test": "node --test test/"}}),
        )
        support.write(repo / "packages" / "b" / "pyproject.toml", PYPROJECT)
        support.write(repo / "packages" / "b" / "src" / "b.py", APP)
        support.write(repo / "packages" / "b" / "tests" / "test_b.py", TEST_GREEN)
        support.commit(repo, "base")
        payload = self.detect(repo)
        blocks = {p["package"]: p for p in payload["packages"]}
        self.assertEqual(set(blocks), {"a", "b"})
        self.assertEqual(blocks["a"]["path"], "packages/a")
        self.assertEqual(blocks["b"]["path"], "packages/b")
        self.assertIn("node", blocks["a"]["stacks"])
        self.assertIn("python", blocks["b"]["stacks"])
        self.assertEqual(blocks["b"]["test_cmd"]["argv"], PYTHON_DEFAULT)
        # Package globs and mappings are repo-root-relative.
        self.assertEqual(blocks["b"]["stream_a"]["include"], ["packages/b/src/**/*.py"])

    def test_base_ref_falls_back_to_main_without_remote(self) -> None:
        repo = self.python_repo("noremote")
        payload = self.detect(repo)
        self.assertEqual(payload["base_ref"], "main")

    def test_base_ref_uses_origin_head_symbolic_ref(self) -> None:
        repo, _ = support.init_repo(self.tmp, name="withhead", with_origin=True)
        support.write(repo / "pyproject.toml", PYPROJECT)
        support.commit(repo, "base")
        support.git(
            repo,
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/main",
        )
        payload = self.detect(repo)
        self.assertEqual(payload["base_ref"], "origin/main")

    def test_mutation_backend_detects_mutmut(self) -> None:
        repo, _ = support.init_repo(self.tmp, name="mutmutrepo")
        support.write(repo / "pyproject.toml", PYPROJECT + "\n[tool.mutmut]\npaths_to_mutate = [\"src/\"]\n")
        support.commit(repo, "base")
        payload = self.detect(repo)
        self.assertEqual(payload["mutation_backend"], "mutmut")

    def test_detect_is_deterministic(self) -> None:
        repo, _ = support.init_repo(self.tmp, name="det")
        support.write(repo / "pyproject.toml", PYPROJECT)
        support.write(
            repo / "package.json",
            json.dumps({"name": "demo", "scripts": {"test": "node --test test/"}}),
        )
        support.write(repo / "src" / "app.py", APP)
        support.write(repo / "tests" / "test_app.py", TEST_GREEN)
        support.write(
            repo / ".github" / "workflows" / "a.yml",
            "on: push\njobs:\n  b:\n    steps:\n      - run: pytest -q && echo done\n",
        )
        support.commit(repo, "base")
        first = self.onboard(repo, "detect")
        second = self.onboard(repo, "detect")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)


class OnboardWriteTests(OnboardTestCase):
    def test_write_creates_loadable_config_and_ledger_next_runs(self) -> None:
        repo = self.python_repo("writerepo")
        proc = self.onboard(repo, "write")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        cfg_path = repo / ".autonomous" / "config.json"
        self.assertTrue(cfg_path.is_file(), proc.stdout)
        # Loads through the real loader, with the detected command.
        loaded = common.load_config(repo, str(cfg_path))
        self.assertEqual(loaded["test_cmd"], PYTHON_DEFAULT)
        self.assertEqual(loaded["base_ref"], "main")
        # ledger.py next must run without a traceback (exit 0 or its
        # not-configured exit 2).
        nxt = support.run(
            [
                support.PYTHON,
                str(SCRIPTS / "ledger.py"),
                "next",
                "--stream",
                "A",
                "--limit",
                "1",
                "--config",
                str(cfg_path),
            ],
            cwd=repo,
        )
        self.assertIn(nxt.returncode, (0, 2), nxt.stderr)
        self.assertNotIn("Traceback", nxt.stderr)
        self.assertNotIn("Traceback", nxt.stdout)

    def test_write_refuses_overwrite_without_force(self) -> None:
        repo = self.python_repo("forcerepo")
        self.assertEqual(self.onboard(repo, "write").returncode, 0)
        again = self.onboard(repo, "write")
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("--force", again.stderr)
        forced = self.onboard(repo, "write", "--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)

    def test_write_records_onboard_block(self) -> None:
        repo = self.python_repo("blockrepo")
        self.assertEqual(self.onboard(repo, "write").returncode, 0)
        block = self.config_on_disk(repo)["onboard"]
        self.assertTrue(re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", block["detected_at"]))
        self.assertIsInstance(block["sources"], dict)
        self.assertTrue(block["sources"])
        self.assertIn(block["confidence"], ("high", "medium", "low"))
        self.assertIsNone(block["verified_baseline"])

    def test_write_leaves_stream_a_unconfigured_when_not_inferable(self) -> None:
        repo, _ = support.init_repo(self.tmp, name="noglobs")
        support.write(repo / "pyproject.toml", PYPROJECT)
        support.commit(repo, "base")
        self.assertEqual(self.onboard(repo, "write").returncode, 0)
        cfg = self.config_on_disk(repo)
        self.assertEqual(cfg["candidates"]["A"]["include"], [])
        # And detect agrees with write.
        payload = self.detect(repo)
        self.assertEqual(payload["packages"][0]["stream_a"]["include"], [])


class OnboardVerifyTests(OnboardTestCase):
    def test_green_baseline_is_verified(self) -> None:
        repo = self.python_repo("greenrepo", test_body=TEST_GREEN)
        self.assertEqual(self.onboard(repo, "write").returncode, 0)
        proc = self.onboard(repo, "verify", "--timeout", "60")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("exit_code: 0", proc.stdout)
        self.assertIn("duration_seconds:", proc.stdout)
        self.assertIn("output_tail:", proc.stdout)
        cfg = self.config_on_disk(repo)
        self.assertIs(cfg["onboard"]["verified_baseline"], True)
        self.assertEqual(cfg["candidates"]["A"]["include"], ["src/**/*.py"])

    def test_red_baseline_disables_stream_a(self) -> None:
        repo = self.python_repo("redrepo", test_body=TEST_RED)
        self.assertEqual(self.onboard(repo, "write").returncode, 0)
        proc = self.onboard(repo, "verify", "--timeout", "60")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("defect repair", proc.stdout + proc.stderr)
        cfg = self.config_on_disk(repo)
        self.assertIs(cfg["onboard"]["verified_baseline"], False)
        self.assertEqual(cfg["candidates"]["A"]["include"], [])

    def test_verify_runs_head_in_worktree_not_working_tree(self) -> None:
        # HEAD is red; the working tree is silently repaired but uncommitted.
        # A verify that ran in the working tree would falsely report green.
        repo = self.python_repo("wtrepo", test_body=TEST_RED)
        self.assertEqual(self.onboard(repo, "write").returncode, 0)
        support.write(repo / "tests" / "test_app.py", TEST_GREEN)
        proc = self.onboard(repo, "verify", "--timeout", "60")
        self.assertNotEqual(proc.returncode, 0)
        cfg = self.config_on_disk(repo)
        self.assertIs(cfg["onboard"]["verified_baseline"], False)


if __name__ == "__main__":
    unittest.main()
