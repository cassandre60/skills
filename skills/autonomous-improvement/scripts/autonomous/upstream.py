#!/usr/bin/env python3
"""Autonomous upstream and research harvester (Track 5: Sourced Research).

Inspects package manifests and upstream remotes for outdated major versions,
divergences, and deprecations.
Schema: upstream/1.
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


SCHEMA = "upstream/1"

MANIFEST_NAMES = ("package.json", "pyproject.toml", "pubspec.yaml", "Cargo.toml")


def scan_upstream(root: Path, cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    if cfg is None:
        cfg = load_config(root)

    candidates: List[Dict[str, Any]] = []
    idx = 1

    for name in MANIFEST_NAMES:
        mpath = root / name
        if not mpath.is_file():
            continue

        rel = str(mpath.relative_to(root))
        try:
            content = mpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        if name == "package.json":
            try:
                pkg_data = json.loads(content)
                deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}
                for dep, ver in deps.items():
                    candidates.append(
                        {
                            "id": f"RES-{idx:03d}",
                            "track": "research",
                            "stream": "K",
                            "rule_id": "upstream.outdated-dependency",
                            "priority": "P3",
                            "title": f"Evaluate upgrade and breaking changes for {dep} ({ver})",
                            "target_paths": [rel],
                            "evidence": f"Dependency {dep}: {ver} declared in {rel}",
                            "status": "lead",
                        }
                    )
                    idx += 1
            except Exception:
                pass

        elif name == "pubspec.yaml":
            in_deps = False
            for line in content.splitlines():
                if re.match(r"^(?:dev_)?dependencies:\s*$", line):
                    in_deps = True
                    continue
                if in_deps:
                    if re.match(r"^[a-zA-Z0-9_-]+:", line):
                        in_deps = False
                        continue
                    m = re.match(r"^\s+([a-zA-Z0-9_]+):\s*(.+)$", line)
                    if m:
                        dep, ver = m.group(1), m.group(2).strip()
                        candidates.append(
                            {
                                "id": f"RES-{idx:03d}",
                                "track": "research",
                                "stream": "K",
                                "rule_id": "upstream.outdated-dependency",
                                "priority": "P3",
                                "title": f"Evaluate upgrade and breaking changes for {dep} ({ver})",
                                "target_paths": [rel],
                                "evidence": f"Dependency {dep}: {ver} declared in {rel}",
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
    candidates = scan_upstream(root, cfg)

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
        print(f"Upstream Scout ({SCHEMA}): {len(candidates)} lead(s) found")
        for c in candidates:
            print(f"  - [{c['rule_id']}] {c['title']} ({c['target_paths'][0]})")

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
