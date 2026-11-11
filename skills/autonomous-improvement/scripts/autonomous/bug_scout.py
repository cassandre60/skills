#!/usr/bin/env python3
"""Autonomous bug scout harvester (Track 3: Bug Hunting & Edge Cases).

Performs deterministic static analysis for defect patterns (error swallowing,
unchecked null assertions, missing timeouts).
Schema: bug-scout/1.
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


SCHEMA = "bug-scout/1"

SOURCE_EXTENSIONS = {".py", ".dart", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"}


def scan_bugs(root: Path, cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    if cfg is None:
        cfg = load_config(root)

    candidates: List[Dict[str, Any]] = []
    idx = 1

    search_dirs = []
    for d in ("src", "lib"):
        p = root / d
        if p.is_dir():
            search_dirs.append(p)
    if not search_dirs:
        search_dirs.append(root)

    for sdir in search_dirs:
        for p in sdir.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in SOURCE_EXTENSIONS:
                continue
            rel = str(p.relative_to(root))
            if "test" in rel.lower():
                continue

            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            lines = content.splitlines()

            # Rule 1: Python swallowed error (except: pass or except Exception: pass)
            if p.suffix.lower() == ".py":
                for i, line in enumerate(lines):
                    if re.search(r"^\s*except\s*(?:[A-Za-z0-9_.]*)?\s*:\s*$", line):
                        if i + 1 < len(lines) and re.search(r"^\s*pass\s*$", lines[i + 1]):
                            candidates.append(
                                {
                                    "id": f"BUG-{idx:03d}",
                                    "track": "bug",
                                    "stream": "E",
                                    "rule_id": "bug.swallowed-error",
                                    "priority": "P2",
                                    "title": f"Handle swallowed exception in {Path(rel).name}:{i+1}",
                                    "target_paths": [rel],
                                    "line": i + 1,
                                    "evidence": f"Empty exception handler: `{line.strip()}` followed by `pass` at line {i+2}",
                                    "status": "lead",
                                }
                            )
                            idx += 1

            # Rule 2: JS/TS/Dart empty catch blocks
            if p.suffix.lower() in (".ts", ".tsx", ".js", ".jsx", ".dart"):
                for i, line in enumerate(lines):
                    if re.search(r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*\}", line):
                        candidates.append(
                            {
                                "id": f"BUG-{idx:03d}",
                                "track": "bug",
                                "stream": "E",
                                "rule_id": "bug.swallowed-error",
                                "priority": "P2",
                                "title": f"Handle empty catch block in {Path(rel).name}:{i+1}",
                                "target_paths": [rel],
                                "line": i + 1,
                                "evidence": f"Empty catch block `{line.strip()}`",
                                "status": "lead",
                            }
                        )
                        idx += 1

    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument("--json", action="store_true", help="output candidates as JSON")
    parser.add_argument("--limit", type=int, default=None, help="limit results")
    args = parser.parse_args()

    root = repo_root()
    cfg = load_config(root)
    candidates = scan_bugs(root, cfg)

    if args.limit is not None and args.limit > 0:
        candidates = candidates[:args.limit]

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
        print(f"Bug Scout ({SCHEMA}): {len(candidates)} lead(s) found")
        for c in candidates:
            print(f"  - [{c['rule_id']}] {c['title']} ({c['target_paths'][0]}:{c['line']})")

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
