"""Standalone spec-audit: every SPEC.md row must be verifiable from the skill alone.

``docs_truth.py check`` reports candidates (symbol drift, dead links, spec-id
trace breaks); it has no literal "NO ENFORCEMENT" / "NO FAILING TEST" verdict.
This suite is the standalone form of those two concepts:

* NO ENFORCEMENT  — a row names an enforcement path that does not exist under
  the skill directory and does not say where it lives.
* NO FAILING TEST — a row's proof names a test this suite does not define, or
  a path that does not resolve under the skill directory.

A row may delegate enforcement to the enclosing skills repository, but only by
saying "repository root" in the cell that names the non-local path. An
unmarked dangling path is exactly the silent NO ENFORCEMENT this catches, and
a marker on a path that actually resolves locally is a lie the third check
refuses. The audit subtest keeps ``docs_truth.py check --paths .`` honest:
anything outside the documented intentional findings fails the suite.
"""

from __future__ import annotations

import json
import re
import unittest

import support

SPEC = support.SKILL_ROOT / "SPEC.md"
REMOTE_MARKER = "repository root"
PATH_SUFFIXES = (".py", ".sh", ".yml", ".yaml", ".json", ".md", ".txt")
TOKEN_STRIP = "`*_>,()[];:"
# Findings that are intentional today: E402/G06/Z99 are spec ids cited by
# negative tests on purpose (the spec-trace check must be able to fail), and
# src.config is the condition planted by the docs-drift fixture itself.
AUDIT_ALLOWLIST = {
    ("spec-id-undefined", "E402"),
    ("spec-id-undefined", "G06"),
    ("spec-id-undefined", "Z99"),
    ("symbol-drift", "src.config"),
}

ROW_RE = re.compile(r"^\|\s*`(R-\d+)`")


def _rows():
    """Yield (row_id, enforcement_cell, proof_cell) for every SPEC table row."""
    for line in SPEC.read_text(encoding="utf-8").splitlines():
        match = ROW_RE.match(line)
        if not match:
            continue
        cells = [cell.strip() for cell in line.split("|")]
        # ['', '<id> <rule text>', '<enforcement>', '<proof>', '']
        yield match.group(1), cells[2], cells[3]


def _tokens(cell):
    for token in cell.split():
        token = token.strip(TOKEN_STRIP)
        if token:
            yield token


def _classify(token):
    if token.startswith("test_") or token.endswith("Tests"):
        return "test"
    if "/" in token or token.endswith(PATH_SUFFIXES):
        return "path"
    return None


class SpecStandaloneTests(unittest.TestCase):
    def test_spec_row_paths_resolve_standalone(self):
        problems = []
        for row_id, enforcement, proof in _rows():
            for label, cell in (("enforcement", enforcement), ("proof", proof)):
                paths = [t for t in _tokens(cell) if _classify(t) == "path"]
                if label == "enforcement" and not paths:
                    problems.append(f"{row_id}: {label} cell names no path")
                if REMOTE_MARKER in cell:
                    if not paths or all(
                        (support.SKILL_ROOT / t).exists() for t in paths
                    ):
                        problems.append(
                            f"{row_id}: {label} cell says {REMOTE_MARKER!r} but names no "
                            f"non-local path; the marker must mark a real delegation"
                        )
                    continue
                for token in paths:
                    if not (support.SKILL_ROOT / token).exists():
                        problems.append(
                            f"{row_id}: {label} path {token!r} does not exist under the "
                            f"skill directory; if it lives in the enclosing skills repo, "
                            f"mark the cell {REMOTE_MARKER!r}"
                        )
        self.assertEqual([], problems, "\n".join(problems))

    def test_spec_row_proofs_name_real_tests(self):
        suite_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((support.SKILL_ROOT / "tests").glob("test_*.py"))
        )
        problems = []
        for row_id, _enforcement, proof in _rows():
            proofs = [t for t in _tokens(proof) if _classify(t) in ("test", "path")]
            if not proofs:
                problems.append(f"{row_id}: proof cell names no test and no path")
            for token in [t for t in _tokens(proof) if _classify(t) == "test"]:
                if not re.search(rf"(?:def|class) {re.escape(token)}\b", suite_text):
                    problems.append(
                        f"{row_id}: proof test {token!r} is not defined in tests/test_*.py"
                    )
        self.assertEqual([], problems, "\n".join(problems))

    def test_docs_truth_audit_reports_no_unexpected_findings(self):
        proc = support.run(
            [
                support.PYTHON,
                str(support.SCRIPTS / "docs_truth.py"),
                "check",
                "--paths",
                ".",
                "--spec",
                "SPEC.md",
                "--json",
            ],
            cwd=support.SKILL_ROOT,
        )
        payload = json.loads(proc.stdout)
        unexpected = [
            f"{c['kind']} {c['name']} ({c['file']}:{c['line']})"
            for c in payload["candidates"]
            if (c["kind"], c["name"]) not in AUDIT_ALLOWLIST
        ]
        self.assertEqual([], unexpected, "\n".join(unexpected))


if __name__ == "__main__":
    unittest.main()
