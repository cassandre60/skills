"""Structural checks for the test suite itself.

Bad merges once concatenated eight partial copies of ``test_v2.py``.  Python
binds only the last definition of a shadowed name, so the tests in the earlier
copies silently never ran.  These checks turn that kind of damage into a CI
failure instead of a hidden loss of coverage.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def _test_modules():
    return sorted(TESTS.glob("test_*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


class SuiteStructureTests(unittest.TestCase):
    def test_no_test_module_redefines_a_top_level_name(self) -> None:
        problems = []
        for path in _test_modules():
            seen: dict[str, int] = {}
            for node in _parse(path).body:
                if not isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                    continue
                if node.name in seen:
                    problems.append(
                        f"{path.name}: {node.name} defined at line {node.lineno}, "
                        f"already defined at line {seen[node.name]}"
                    )
                seen[node.name] = node.lineno
        self.assertEqual([], problems, "\n".join(problems))

    def test_no_test_class_redefines_a_method(self) -> None:
        problems = []
        for path in _test_modules():
            for node in _parse(path).body:
                if not isinstance(node, ast.ClassDef):
                    continue
                seen: dict[str, int] = {}
                for member in node.body:
                    if not isinstance(member, ast.FunctionDef):
                        continue
                    if member.name in seen:
                        problems.append(
                            f"{path.name}: {node.name}.{member.name} defined at line "
                            f"{member.lineno}, already defined at line {seen[member.name]}"
                        )
                    seen[member.name] = member.lineno
        self.assertEqual([], problems, "\n".join(problems))

    def test_test_v2_has_a_single_repo_test_case(self) -> None:
        path = TESTS / "test_v2.py"
        classes = [node.name for node in _parse(path).body if isinstance(node, ast.ClassDef)]
        self.assertEqual(1, classes.count("RepoTestCase"))


if __name__ == "__main__":
    unittest.main()
