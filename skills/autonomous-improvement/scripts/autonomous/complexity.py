#!/usr/bin/env python3
"""Autonomous complexity finder (Track 4: Architecture & Safe Simplification).

Identifies oversized files and complex modules suitable for Stream M ratchet refactoring.
Schema: complexity/1.
"""

from __future__ import annotations

import argparse
import json
import os
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


SCHEMA = "complexity/1"
DEFAULT_LINE_THRESHOLD = 500

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
    cfg: Optional[Dict[str, Any]] = None,
    threshold: int = DEFAULT_LINE_THRESHOLD,
) -> List[Dict[str, Any]]:
    if cfg is None:
        cfg = load_config(root)

    candidates: List[Dict[str, Any]] = []

    search_dirs = []
    for d in ("src", "lib"):
        p = root / d
        if p.is_dir():
            search_dirs.append(p)

    if not search_dirs:
        search_dirs.append(root)

    for base_dir in search_dirs:
        for p in base_dir.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in SOURCE_EXTENSIONS:
                continue
            rel = str(p.relative_to(root))
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
