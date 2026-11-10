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
SCRIPTS = TESTS.parent / "scripts" / "autonomous"


def _test_modules():
    return sorted(TESTS.glob("test_*.py"))


def _script_modules():
    return sorted(SCRIPTS.glob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_main_guard(node: ast.stmt) -> bool:
    if not isinstance(node, ast.If):
        return False
    test = node.test
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
    )


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

    def test_no_script_module_redefines_a_top_level_name(self) -> None:
        problems = []
        for path in _script_modules():
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

    def test_main_guard_is_only_final_statement_in_test_modules(self) -> None:
        problems = []
        for path in _test_modules():
            body = _parse(path).body
            for idx, node in enumerate(body[:-1]):
                if _is_main_guard(node):
                    problems.append(
                        f"{path.name}: mid-file __main__ guard at line {node.lineno} "
                        f"(statement {idx + 1} of {len(body)})"
                    )
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

    def test_fleet_and_mutation_engine_submodules_extracted(self) -> None:
        import sys

        for name in ("fleet_doctor.py", "fleet_pool.py", "mutation_stryker.py"):
            self.assertTrue((SCRIPTS / name).is_file(), f"missing submodule {name}")
        fleet_lines = len((SCRIPTS / "fleet.py").read_text(encoding="utf-8").splitlines())
        mutation_lines = len((SCRIPTS / "mutation_engine.py").read_text(encoding="utf-8").splitlines())
        self.assertLess(fleet_lines, 2050)
        self.assertLess(mutation_lines, 1060)

        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        import fleet
        import fleet_doctor
        import fleet_pool
        import mutation_engine
        import mutation_stryker

        self.assertIs(fleet.run_doctor_checks, fleet_doctor.run_doctor_checks)
        self.assertIs(fleet._resolve_model_pool, fleet_pool._resolve_model_pool)
        self.assertIs(mutation_engine.run_stryker_backend, mutation_stryker.run_stryker_backend)


if __name__ == "__main__":
    unittest.main()
