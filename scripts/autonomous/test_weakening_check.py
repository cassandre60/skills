#!/usr/bin/env python3
"""Heuristic test-weakening tripwire.

Fails when a diff removes assertions, deletes a test file, or adds a
skip/xfail/focus marker. This is deliberately heuristic: a human reviews every
hit. It is a stand-in when the repo has no gate system of its own.

Usage:
  python3 scripts/autonomous/test_weakening_check.py --base origin/main
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    EXIT_ENV,
    EXIT_NEGATIVE,
    EXIT_OK,
    EXIT_USAGE,
    EnvError,
    UsageError,
    format_exit,
    git,
    glob_match,
    load_config,
    repo_root,
)

ASSERTION_RE = re.compile(
    r"(?:\bassert\b|\bassert[A-Z]\w*\s*\(|\bexpect\s*\(|\bexpect\s*\{|"
    r"\.should\b|\brequire\s*\(|\bt\.(?:Error|Fatal|Fatalf)\b|"
    r"\bassertThat\s*\(|\bself\.assert\w+\s*\()"
)
WEAKENING_RE = re.compile(
    r"(?:pytest\.mark\.(?:skip|xfail)|@unittest\.skip|@Skip\b|"
    r"\b(?:it|test|describe|context)\.(?:skip|only)\s*\(|"
    r"\b(?:xit|xdescribe)\s*\(|\bskip\s*[:=]\s*(?:true|'|\")|"
    r"#\s*(?:noqa|type:\s*ignore)\b|//\s*eslint-disable)"
)
DEFAULT_TEST_GLOBS = [
    "**/test_*.py",
    "**/*_test.py",
    "**/*_test.dart",
    "**/*.test.ts",
    "**/*.test.tsx",
    "**/*.test.js",
    "**/*.spec.ts",
    "**/*.spec.js",
    "**/*_test.go",
]


def _zsplit(value: str) -> List[str]:
    return [part for part in value.split("\0") if part]


def _test_globs(cfg: Dict[str, Any]) -> List[str]:
    section = cfg.get("candidates", {}).get("B", {})
    include = section.get("include") or []
    return include or DEFAULT_TEST_GLOBS


def _changed_test_files(root: Path, base: str, globs: List[str]) -> Tuple[List[str], List[str]]:
    proc = git(["diff", "--name-status", "-z", f"{base}...HEAD"], cwd=root, quiet=True)
    if proc.returncode != 0:
        raise UsageError(f"cannot diff against {base}: {proc.stderr.strip()}")
    parts = _zsplit(proc.stdout)
    changed: List[str] = []
    deleted: List[str] = []
    index = 0
    while index < len(parts):
        status = parts[index]
        index += 1
        if status.startswith(("R", "C")):
            # Renames/copies carry the old path and the new path.
            index += 1
            if index >= len(parts):
                break
        if index >= len(parts):
            break
        path = parts[index]
        index += 1
        if not any(glob_match(path, pattern) for pattern in globs):
            continue
        if status.startswith("D"):
            deleted.append(path)
        elif status[:1] in ("A", "M", "R", "C"):
            changed.append(path)
    return sorted(set(changed)), sorted(set(deleted))


def _diff_lines(root: Path, base: str, path: str) -> Tuple[List[str], List[str]]:
    proc = git(["diff", "-U0", f"{base}...HEAD", "--", path], cwd=root, quiet=True)
    if proc.returncode != 0:
        raise UsageError(f"cannot diff {path}: {proc.stderr.strip()}")
    removed: List[str] = []
    added: List[str] = []
    for line in proc.stdout.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-"):
            removed.append(line[1:].strip())
        elif line.startswith("+"):
            added.append(line[1:].strip())
    return removed, added


def check(root: Path, base: str, globs: List[str]) -> List[Dict[str, Any]]:
    changed, deleted = _changed_test_files(root, base, globs)
    findings: List[Dict[str, Any]] = []
    for path in deleted:
        findings.append({"file": path, "kind": "deleted-test-file", "line": None, "text": ""})

    for path in changed:
        removed, added = _diff_lines(root, base, path)
        added_set = set(added)
        removed_assertions = [
            text for text in removed if ASSERTION_RE.search(text) and text not in added_set
        ]
        added_assertions = [text for text in added if ASSERTION_RE.search(text)]
        net_removed = len(removed_assertions) - len(added_assertions)
        if net_removed > 0:
            findings.append(
                {
                    "file": path,
                    "kind": "removed-assertion",
                    "line": None,
                    "text": removed_assertions[0][:300],
                    "count": net_removed,
                }
            )
        for text in added:
            match = WEAKENING_RE.search(text)
            if match:
                findings.append(
                    {
                        "file": path,
                        "kind": "added-weakening-marker",
                        "line": None,
                        "text": text[:300],
                        "marker": match.group(0),
                    }
                )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Heuristic test-weakening tripwire")
    parser.add_argument("--base", default="origin/main", help="base ref (default: origin/main)")
    parser.add_argument("--config", help="path to config.json")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()
    try:
        root = repo_root()
        cfg = load_config(root, args.config)
        globs = _test_globs(cfg)
        findings = check(root, args.base, globs)
        if args.json:
            json.dump({"base": args.base, "findings": findings}, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            if not findings:
                print(f"[+] No test-weakening signals against {args.base}.")
            for finding in findings:
                detail = finding.get("text") or finding.get("marker") or ""
                print(
                    f"[!] {finding['kind']}: {finding['file']}"
                    + (f" — {detail}" if detail else "")
                )
            if findings:
                print(
                    "\nReview every hit. A removed assertion or added skip can be legitimate; "
                    "this tripwire exists to make the reviewer prove that."
                )
        return EXIT_NEGATIVE if findings else EXIT_OK
    except (UsageError, EnvError) as exc:
        return format_exit(exc)
    except Exception as exc:
        return format_exit(exc)


if __name__ == "__main__":
    sys.exit(main())
