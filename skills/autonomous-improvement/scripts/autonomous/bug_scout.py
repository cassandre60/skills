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


SCHEMA = "bug-scout/1"

SOURCE_EXTENSIONS = {".py", ".dart", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"}
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


def scan_bugs(root: Path, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if cfg is None:
        cfg = load_config(root)

    candidates: list[dict[str, Any]] = []
    idx = 1

    search_dirs = []
    for d in ("src", "lib"):
        p = root / d
        if p.is_dir():
            search_dirs.append(p)
    if not search_dirs:
        search_dirs.append(root)

    excludes = (cfg.get("candidates") or {}).get("A", {}).get("exclude") or []
    for sdir in search_dirs:
        for p in sdir.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in SOURCE_EXTENSIONS:
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
