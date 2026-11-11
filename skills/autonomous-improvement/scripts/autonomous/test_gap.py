#!/usr/bin/env python3
"""Autonomous test gap finder (Track 2: Test Engineering).

Detects production files with no companion test on disk.
Schema: test-gap/1.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from common import (
        EXIT_OK,
        load_config,
        match_any,
        repo_root,
        test_for_source,
    )
except ImportError:
    EXIT_OK = 0

    def repo_root(start: os.PathLike | str | None = None) -> Path:
        return Path(start or Path.cwd()).resolve()

    def load_config(root: Path, explicit: os.PathLike | str | None = None) -> dict[str, Any]:
        return {}

    def match_any(path: str, patterns: Any) -> bool:
        import fnmatch
        return any(fnmatch.fnmatch(path, pat) for pat in patterns)

    def test_for_source(root: Path, cfg: dict[str, Any], source: str) -> str | None:
        return None


SCHEMA = "test-gap/1"

SKIP_DIRS = {".git", ".dart_tool", ".agents", ".opencode", "build", "node_modules", "__pycache__", ".autonomous", ".venv", "venv"}
GENERATED_SUFFIXES = (".g.dart", ".freezed.dart", ".mocks.dart", ".gen.dart", ".pb.go", "_pb2.py", ".d.ts")

SOURCE_EXTENSIONS = {
    ".py",
    ".dart",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".c",
    ".cpp",
    ".h",
}

TEST_NAME_PATTERNS = [
    re.compile(r"(?:^|/)(?:test_[^/]+|[^/]+_test)\.[^/]+$", re.IGNORECASE),
    re.compile(r"\.(?:test|spec)\.[^/]+$", re.IGNORECASE),
    re.compile(r"(?:^|/)tests?/", re.IGNORECASE),
]


def _is_test_file(rel_path: str) -> bool:
    for pat in TEST_NAME_PATTERNS:
        if pat.search(rel_path):
            return True
    return False


def _is_file_in_ledger(ledger_text: str, rel_path: str) -> bool:
    for line in ledger_text.splitlines():
        if f"`{rel_path}`" in line or f" {rel_path} " in line:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 6:
                status = parts[5].strip().upper()
                if any(s in status for s in ("SOUND", "COMPLETED", "REJECTED", "WONTFIX")):
                    return True
    return False


def find_test_gaps(
    root: Path,
    cfg: dict[str, Any] | None = None,
    include_audited: bool = False,
) -> list[dict[str, Any]]:
    if cfg is None:
        cfg = load_config(root)

    candidates_cfg = cfg.get("candidates", {}).get("A", {})
    includes = candidates_cfg.get("include") or ["src/**/*", "lib/**/*"]
    excludes = set(candidates_cfg.get("exclude") or [])

    ledger_path = root / cfg.get("ledger_path", "docs/AUTONOMOUS_WORK_LEDGER.md")
    ledger_text = ledger_path.read_text(encoding="utf-8", errors="replace") if ledger_path.is_file() else ""

    gaps: list[dict[str, Any]] = []

    # Gather matching files
    matched_paths: set[str] = set()
    for inc in includes:
        for p in root.glob(inc):
            if p.is_file():
                rel = str(p.relative_to(root))
                matched_paths.add(rel)

    # Also search common source roots if glob found nothing
    if not matched_paths:
        for sdir in ("src", "lib"):
            base = root / sdir
            if base.is_dir():
                for p in base.rglob("*"):
                    if p.is_file():
                        matched_paths.add(str(p.relative_to(root)))

    for rel in sorted(matched_paths):
        if any(part in SKIP_DIRS for part in Path(rel).parts):
            continue
        if rel.endswith(GENERATED_SUFFIXES):
            continue
        ext = Path(rel).suffix.lower()
        if ext not in SOURCE_EXTENSIONS:
            continue
        if _is_test_file(rel):
            continue
        if excludes and match_any(rel, excludes):
            continue
        if not include_audited and ledger_text and _is_file_in_ledger(ledger_text, rel):
            continue

        companion = test_for_source(root, cfg, rel)
        has_test = False
        if companion and (root / companion).is_file():
            has_test = True

        if not has_test:
            line_count = 0
            full = root / rel
            try:
                line_count = len(full.read_text(encoding="utf-8", errors="replace").splitlines())
            except Exception:
                pass

            gaps.append(
                {
                    "source": rel,
                    "expected_test": companion or f"tests/test_{Path(rel).stem}{ext}",
                    "lines": line_count,
                    "has_companion": False,
                }
            )

    # Sort gaps by line count descending (higher risk/LOC first)
    gaps.sort(key=lambda g: -g["lines"])
    return gaps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument("--json", action="store_true", help="output candidates as JSON")
    parser.add_argument("--limit", type=int, default=None, help="limit results")
    parser.add_argument("--all", action="store_true", help="include already-audited files")
    args = parser.parse_args()

    root = repo_root()
    cfg = load_config(root)
    gaps = find_test_gaps(root, cfg, include_audited=args.all)

    if args.limit is not None and args.limit > 0:
        gaps = gaps[:args.limit]

    if args.json:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "count": len(gaps),
                    "candidates": gaps,
                },
                indent=2,
            )
        )
    else:
        print(f"Test Gaps ({SCHEMA}): {len(gaps)} untested candidate(s) found")
        for g in gaps:
            print(f"  - {g['source']} ({g['lines']} lines) -> expected test: {g['expected_test']}")

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
