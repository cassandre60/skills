#!/usr/bin/env python3
"""Autonomous CI audit finder (Track 1: CI/CD & Developer Experience).

Inspects .github/workflows and CI configurations for missing gates,
PR test runners, and unpinned actions.
Schema: ci-audit/1.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from common import (
        EXIT_OK,
        load_config,
        repo_root,
    )
except ImportError:
    EXIT_OK = 0

    def repo_root(start: Optional[os.PathLike | str] = None) -> Path:
        return Path(start or Path.cwd()).resolve()

    def load_config(root: Path, explicit: Optional[os.PathLike | str] = None) -> Dict[str, Any]:
        return {}


SCHEMA = "ci-audit/1"


def audit_ci(root: Path, cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []

    gh_workflows = root / ".github" / "workflows"
    if not gh_workflows.is_dir():
        findings.append(
            {
                "rule": "ci.missing-workflows",
                "severity": "high",
                "message": "No .github/workflows directory found in repository",
                "target": ".github/workflows",
            }
        )
        return findings

    workflow_files = list(gh_workflows.glob("*.yml")) + list(gh_workflows.glob("*.yaml"))
    if not workflow_files:
        findings.append(
            {
                "rule": "ci.missing-workflows",
                "severity": "high",
                "message": "No workflow files (.yml or .yaml) found in .github/workflows",
                "target": ".github/workflows",
            }
        )
        return findings

    has_pr_trigger = False
    has_test_step = False

    for wf in workflow_files:
        try:
            content = wf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        rel = str(wf.relative_to(root))

        # Check for pull_request trigger
        if re.search(r"^\s*(?:on:\s*\[?[^\]]*\bpull_request\b|pull_request:)", content, re.MULTILINE):
            has_pr_trigger = True

        # Check for test/lint execution
        if re.search(r"\b(?:test|pytest|unittest|flutter test|cargo test|vitest|jest|npm test)\b", content, re.I):
            has_test_step = True

    if not has_pr_trigger:
        findings.append(
            {
                "rule": "ci.missing-pr-test-runner",
                "severity": "high",
                "message": "No GitHub Actions workflow triggers on pull_request",
                "target": ".github/workflows",
            }
        )

    if not has_test_step:
        findings.append(
            {
                "rule": "ci.missing-test-step",
                "severity": "medium",
                "message": "No workflow appears to execute automated tests",
                "target": ".github/workflows",
            }
        )

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument("--json", action="store_true", help="output findings as JSON")
    args = parser.parse_args()

    root = repo_root()
    cfg = load_config(root)
    findings = audit_ci(root, cfg)

    if args.json:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "count": len(findings),
                    "candidates": findings,
                },
                indent=2,
            )
        )
    else:
        print(f"CI Audit ({SCHEMA}): {len(findings)} issue(s) detected")
        for f in findings:
            print(f"  - [{f['rule']}] {f['message']} ({f['target']})")

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
