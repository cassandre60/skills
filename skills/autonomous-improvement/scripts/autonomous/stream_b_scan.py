#!/usr/bin/env python3
"""Stream B mechanical pre-filter (issue #17): grep-class test smells.

Emits candidate smells with file:line so a Stream B worker starts from a
shortlist instead of spending a full LLM file read on discovery. Same
humility as test_weakening_check.py: these are triage inputs for the
fail-to-pass contract, never findings by themselves — a human (or the
mutation engine) decides whether the behavior is actually unguarded.

Kinds: vacuous-assert · no-assertion (Python AST) · sleeps · mock-assert
(mock returns X, test asserts X; Python AST, conservative).

Usage:
  python3 scripts/autonomous/stream_b_scan.py [paths...] [--json]
With no paths, scans the Stream B candidates from config.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import (
    EXIT_OK,
    EnvError,
    Fatal,
    UsageError,
    candidate_paths,
    format_exit,
    glob_match,
    load_config,
    repo_root,
)
from test_weakening_check import DEFAULT_TEST_GLOBS, _test_globs

VACUOUS_RE = re.compile(
    r"(?:\bassert\s+(?:True|1\s*==\s*1|\(\)\s*==\s*\(\))\b"
    r"|\bassert(?:True|That)?\s*\(\s*(?:true|True)\s*[,)]"
    r"|\bassertEquals?\s*\(\s*([\"'\w.]+)\s*,\s*\1\s*\)"
    r"|\bexpect\s*\(\s*true\s*\)\s*\.toBe(?:Truthy)?\s*\(\s*(?:true)?\s*\))"
)
SLEEP_RE = re.compile(
    r"(?:\btime\.sleep\s*\(|\bThread\.sleep\s*\(|\bFuture\.delayed\b"
    r"|\bsetTimeout\s*\(|\bawait\s+(?:asyncio|trio|anyio)\.sleep\s*\()"
)


def _finding(path: str, line: int, kind: str, text: str) -> Dict[str, Any]:
    return {"file": path, "line": line, "kind": kind, "text": text.strip()[:200]}


def _segment(source: str, node: ast.AST) -> str:
    return (ast.get_source_segment(source, node) or "").strip()


def _python_findings(rel: str, source: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return findings
    lines = source.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not (node.name.startswith("test") or node.name.startswith("Test")):
            continue
        has_assert = False
        mock_returns: Dict[str, str] = {}
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assert):
                has_assert = True
            elif isinstance(sub, ast.Call):
                func = sub.func
                name = ""
                if isinstance(func, ast.Attribute):
                    name = func.attr
                elif isinstance(func, ast.Name):
                    name = func.id
                if name.startswith("assert") or name in ("expect", "require"):
                    has_assert = True
            elif isinstance(sub, ast.With):
                # pytest.raises(...) is an assertion about behavior.
                if "raises" in _segment(source, sub):
                    has_assert = True
            if isinstance(sub, ast.Assign) and len(sub.targets) == 1:
                target = sub.targets[0]
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "return_value"
                ):
                    mock_returns[_segment(source, target.value)] = _segment(source, sub.value)
                elif isinstance(target, ast.Name) and isinstance(sub.value, ast.Call):
                    call = sub.value
                    func_name = _segment(source, call.func)
                    if "Mock" in func_name:
                        for keyword in call.keywords:
                            if keyword.arg == "return_value":
                                mock_returns[target.id] = _segment(source, keyword.value)
        if not has_assert:
            findings.append(
                _finding(rel, node.lineno, "no-assertion", lines[node.lineno - 1])
            )
        # mock-assert: an equality assert whose one side is exactly the value
        # the mock was told to return, and the other side is a call.
        returned = {value for value in mock_returns.values() if value}
        if returned:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assert) and isinstance(sub.test, ast.Compare):
                    sides = [_segment(source, sub.test.left)] + [
                        _segment(source, comp) for comp in sub.test.comparators
                    ]
                    nodes = [sub.test.left] + list(sub.test.comparators)
                    if any(side in returned for side in sides) and any(
                        isinstance(item, ast.Call) for item in nodes
                    ):
                        findings.append(
                            _finding(rel, sub.lineno, "mock-assert", lines[sub.lineno - 1])
                        )
    return findings


def scan_file(root: Path, path: Path) -> List[Dict[str, Any]]:
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = str(path)
    source = path.read_text(encoding="utf-8", errors="ignore")
    findings: List[Dict[str, Any]] = []
    for number, line in enumerate(source.splitlines(), start=1):
        if VACUOUS_RE.search(line):
            findings.append(_finding(rel, number, "vacuous-assert", line))
        if SLEEP_RE.search(line):
            findings.append(_finding(rel, number, "sleeps", line))
    if path.suffix == ".py":
        findings.extend(_python_findings(rel, source))
    # A vacuous `assert True` is still an assert for AST purposes; never
    # double-report the same line/kind.
    unique: Dict[Any, Dict[str, Any]] = {}
    for finding in findings:
        unique.setdefault((finding["line"], finding["kind"]), finding)
    return [unique[key] for key in sorted(unique)]


def _is_test_path(rel: str, globs: List[str]) -> bool:
    return any(glob_match(rel, pattern) for pattern in globs)


def _collect(root: Path, cfg: Dict[str, Any], raw_paths: List[str]) -> List[Path]:
    globs = _test_globs(cfg) or DEFAULT_TEST_GLOBS
    files: List[Path] = []
    if not raw_paths:
        for rel in candidate_paths(root, cfg, "B"):
            path = root / rel
            if path.exists():
                files.append(path)
        return files
    for raw in raw_paths:
        path = Path(raw)
        if not path.is_absolute():
            path = root / path
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if not child.is_file():
                    continue
                try:
                    rel = child.resolve().relative_to(root.resolve()).as_posix()
                except ValueError:
                    rel = child.as_posix()
                if _is_test_path(rel, globs):
                    files.append(child)
        elif path.exists():
            files.append(path)
        else:
            raise Fatal(f"path not found: {raw}")
    return files


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stream_b_scan.py",
        description="Mechanical Stream B pre-filter: candidate test smells.",
    )
    parser.add_argument("paths", nargs="*", help="files/dirs (default: Stream B candidates)")
    parser.add_argument("--config", help="path to config.json")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    try:
        root = repo_root()
        cfg = load_config(root, args.config)
        findings: List[Dict[str, Any]] = []
        for path in _collect(root, cfg, args.paths):
            findings.extend(scan_file(root, path))
        findings.sort(key=lambda item: (item["file"], item["line"], item["kind"]))
        if args.json:
            print(json.dumps(findings, indent=2))
        else:
            for finding in findings:
                print(
                    f"{finding['file']}:{finding['line']} [{finding['kind']}] {finding['text']}"
                )
            print(
                f"[*] {len(findings)} candidate smell(s) — triage input only; "
                "the fail-to-pass contract decides what is real.",
                file=sys.stderr,
            )
        return EXIT_OK
    except (UsageError, EnvError, Fatal) as exc:
        return format_exit(exc)


if __name__ == "__main__":
    sys.exit(main())
