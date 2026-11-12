#!/usr/bin/env python3
"""Autonomous complexity finder (Track 4: Architecture & Safe Simplification).

Identifies oversized files and complex modules suitable for Stream M ratchet refactoring.
Schema: complexity/1.
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


SCHEMA = "complexity/1"
DEFAULT_LINE_THRESHOLD = 500
SKIP_DIRS = {".git", ".dart_tool", ".agents", ".opencode", "build", "node_modules", "__pycache__", ".autonomous", ".venv", "venv"}
GENERATED_SUFFIXES = (".g.dart", ".freezed.dart", ".mocks.dart", ".gen.dart", ".pb.go", "_pb2.py", ".d.ts")


def is_generated_source(path_str: str) -> bool:
    if path_str.endswith(GENERATED_SUFFIXES):
        return True
    p = Path(path_str)
    if "l10n" in p.parts and (path_str.endswith(".dart") and "l10n" in p.name.lower()):
        return True
    if re.search(r"(?:^|/)l10n_[a-z]{2,}(?:_[A-Za-z0-9]+)?\.dart$", path_str):
        return True
    return False

SOURCE_EXTENSIONS = {
    ".py",
    ".dart",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
}


def find_complexity_candidates(
    root: Path,
    cfg: dict[str, Any] | None = None,
    threshold: int = DEFAULT_LINE_THRESHOLD,
) -> list[dict[str, Any]]:
    if cfg is None:
        cfg = load_config(root)

    candidates: list[dict[str, Any]] = []

    search_dirs = []
    for d in ("src", "lib"):
        p = root / d
        if p.is_dir():
            search_dirs.append(p)

    if not search_dirs:
        search_dirs.append(root)

    excludes = (cfg.get("candidates") or {}).get("A", {}).get("exclude") or []
    for base_dir in search_dirs:
        for p in base_dir.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in SOURCE_EXTENSIONS:
                continue
            rel = str(p.relative_to(root))
            if any(part in SKIP_DIRS for part in Path(rel).parts):
                continue
            if is_generated_source(rel):
                continue
            if excludes and match_any(rel, excludes):
                continue
            if "test" in rel.lower():
                continue
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                loc = len(lines)
                if loc >= threshold:
                    candidates.append(
                        {
                            "file": rel,
                            "lines": loc,
                            "threshold": threshold,
                            "severity": "high" if loc >= threshold * 2 else "medium",
                        }
                    )
            except Exception:
                continue

    candidates.sort(key=lambda c: -c["lines"])
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument("--json", action="store_true", help="output candidates as JSON")
    parser.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_LINE_THRESHOLD,
        help="line threshold (default: 500)",
    )
    args = parser.parse_args()

    root = repo_root()
    cfg = load_config(root)
    candidates = find_complexity_candidates(root, cfg, threshold=args.threshold)

    if args.json:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "count": len(candidates),
                    "candidates": candidates,
                },
                indent=2,
            )
        )
    else:
        print(f"Complexity Finder ({SCHEMA}): {len(candidates)} candidate(s) found (threshold: {args.threshold} lines)")
        for c in candidates:
            print(f"  - {c['file']}: {c['lines']} lines")

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
