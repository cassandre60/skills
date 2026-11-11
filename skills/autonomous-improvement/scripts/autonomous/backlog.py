#!/usr/bin/env python3
"""Unified Autonomous Backlog and Harvester Engine (V3 Milestone 3).

Rebuilds the derived cache `.autonomous/backlog.json` from 0-token deterministic harvesters.
Schema: autonomous/backlog/v3.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from common import (
        EXIT_OK,
        EXIT_NEGATIVE,
        UsageError,
        hash_at_head,
        load_config,
        repo_root,
        run,
    )
except ImportError:
    EXIT_OK = 0
    EXIT_NEGATIVE = 1
    UsageError: type[Exception] = Exception

    def repo_root(start: Optional[os.PathLike | str] = None) -> Path:
        return Path(start or Path.cwd()).resolve()

    def load_config(root: Path, explicit: Optional[os.PathLike | str] = None) -> Dict[str, Any]:
        return {}

    def hash_at_head(root: Path, path: str) -> Optional[str]:
        p = root / path
        if p.is_file():
            return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        return None


SCHEMA = "autonomous/backlog/v3"


def compute_fingerprint(track: str, rule_id: str, target_paths: List[str], anchor: str) -> str:
    norm_paths = ",".join(sorted(target_paths))
    raw = f"{track}|{rule_id}|{norm_paths}|{anchor}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"sha256:{digest}"


def get_head_sha(root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass
    return "0" * 40


def load_active_rejections(root: Path, cfg: Dict[str, Any]) -> Set[str]:
    """Load active (non-expired) fingerprints from ledger REJECTED rows."""
    rejections: Set[str] = set()
    ledger_path = root / cfg.get("ledger_path", "docs/AUTONOMOUS_WORK_LEDGER.md")
    if not ledger_path.is_file():
        return rejections

    now = datetime.datetime.now(datetime.timezone.utc)
    for line in ledger_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "REJECTED" not in line and "WONTFIX" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 8:
            summary = parts[7]
            # Check for fingerprint
            for token in summary.split():
                if token.startswith("sha256:"):
                    # Check for expiration if present
                    if "expires:" in summary:
                        try:
                            exp_part = summary.split("expires:")[1].split()[0].strip()
                            exp_date = datetime.datetime.fromisoformat(exp_part.replace("Z", "+00:00"))
                            if exp_date < now:
                                continue  # expired
                        except Exception:
                            pass
                    rejections.add(token.strip("`*.,()"))
    return rejections


def load_open_claims(root: Path) -> Tuple[Set[str], Set[str]]:
    """Inspect open PRs for claimed fingerprints and target paths."""
    claimed_fps: Set[str] = set()
    claimed_paths: Set[str] = set()
    try:
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--json",
                "number,title,body,files",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            prs = json.loads(proc.stdout)
            for pr in prs:
                body = pr.get("body") or ""
                for token in body.split():
                    if token.startswith("sha256:"):
                        claimed_fps.add(token.strip("`*.,()"))
                for f in pr.get("files") or []:
                    p = f.get("path") if isinstance(f, dict) else str(f)
                    if p:
                        claimed_paths.add(p)
    except Exception:
        pass
    return claimed_fps, claimed_paths


def harvest_items(root: Path, cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    if cfg is None:
        cfg = load_config(root)

    items: List[Dict[str, Any]] = []

    # 1. CI Harvester
    try:
        from ci_audit import audit_ci

        for f in audit_ci(root, cfg):
            items.append(
                {
                    "track": "ci",
                    "stream": "I",
                    "rule_id": f.get("rule", "ci.missing-workflows"),
                    "priority": "P1" if f.get("severity") == "high" else "P2",
                    "title": f.get("message", "CI workflow issue"),
                    "target_paths": [f.get("target", ".github/workflows")],
                    "evidence": f.get("message", ""),
                    "status": "ready",
                }
            )
    except Exception:
        pass

    # 2. Test Gap Harvester
    try:
        from test_gap import find_test_gaps

        for g in find_test_gaps(root, cfg):
            items.append(
                {
                    "track": "test",
                    "stream": "A",
                    "rule_id": "test.no-companion",
                    "priority": "P2",
                    "title": f"Create unit tests for {Path(g['source']).stem}",
                    "target_paths": [g["source"]],
                    "evidence": f"{g['lines']} lines, expected test: {g['expected_test']}",
                    "status": "ready",
                }
            )
    except Exception:
        pass

    # 3. Complexity Harvester
    try:
        from complexity import find_complexity_candidates

        for c in find_complexity_candidates(root, cfg):
            items.append(
                {
                    "track": "refactor",
                    "stream": "M",
                    "rule_id": "refactor.high-complexity",
                    "priority": "P2" if c.get("severity") == "high" else "P3",
                    "title": f"Stream M ratchet refactor for {Path(c['file']).name}",
                    "target_paths": [c["file"]],
                    "evidence": f"{c['lines']} lines exceeds threshold {c.get('threshold', 500)}",
                    "status": "ready",
                }
            )
    except Exception:
        pass

    # 4. Bug Scout Harvester
    try:
        from bug_scout import scan_bugs

        for b in scan_bugs(root, cfg):
            items.append(
                {
                    "track": "bug",
                    "stream": "E",
                    "rule_id": b["rule_id"],
                    "priority": b.get("priority", "P2"),
                    "title": b["title"],
                    "target_paths": b["target_paths"],
                    "evidence": b["evidence"],
                    "status": "lead",
                }
            )
    except Exception:
        pass

    # 5. Upstream Harvester
    try:
        from upstream import scan_upstream

        for u in scan_upstream(root, cfg):
            items.append(
                {
                    "track": "research",
                    "stream": "K",
                    "rule_id": u["rule_id"],
                    "priority": u.get("priority", "P3"),
                    "title": u["title"],
                    "target_paths": u["target_paths"],
                    "evidence": u["evidence"],
                    "status": "lead",
                }
            )
    except Exception:
        pass

    return items


def harvest_backlog(root: Path, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if cfg is None:
        cfg = load_config(root)

    raw_items = harvest_items(root, cfg)
    active_rejections = load_active_rejections(root, cfg)
    claimed_fps, claimed_paths = load_open_claims(root)

    filtered_items: List[Dict[str, Any]] = []
    track_counts: Dict[str, int] = {}

    for item in raw_items:
        track = item["track"]
        target_paths = item["target_paths"]
        primary = target_paths[0] if target_paths else ""

        anchor = ""
        if primary:
            anchor = hash_at_head(root, primary) or ""

        fingerprint = compute_fingerprint(track, item["rule_id"], target_paths, anchor)
        item["fingerprint"] = fingerprint

        # Drop if fingerprint is an active rejection
        if fingerprint in active_rejections:
            continue

        # Check claim status
        if fingerprint in claimed_fps or primary in claimed_paths:
            item["status"] = "claimed"

        prefix = {
            "ci": "CI",
            "test": "TEST",
            "bug": "BUG",
            "refactor": "REF",
            "research": "RES",
        }.get(track, "ITEM")

        track_counts[track] = track_counts.get(track, 0) + 1
        item["id"] = f"{prefix}-{track_counts[track]:03d}"
        filtered_items.append(item)

    # Sort items by priority (P1 > P2 > P3), then status (ready > lead > claimed)
    prio_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    status_order = {"ready": 0, "lead": 1, "claimed": 2, "done": 3}
    filtered_items.sort(
        key=lambda it: (
            prio_order.get(it["priority"], 9),
            status_order.get(it["status"], 9),
            it["id"],
        )
    )

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    head_sha = get_head_sha(root)

    backlog = {
        "$schema": SCHEMA,
        "updated_at": now_iso,
        "head_sha": head_sha,
        "items": filtered_items,
    }

    # Write to .autonomous/backlog.json
    out_dir = root / ".autonomous"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "backlog.json"
    out_file.write_text(json.dumps(backlog, indent=2) + "\n", encoding="utf-8")

    return backlog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_harvest = subparsers.add_parser("harvest", help="rebuild .autonomous/backlog.json")
    p_harvest.add_argument("--json", action="store_true", help="output backlog as JSON")

    p_list = subparsers.add_parser("list", help="list backlog items")
    p_list.add_argument("--track", help="filter by track (ci, test, bug, refactor, research)")
    p_list.add_argument("--status", help="filter by status (ready, lead, claimed)")
    p_list.add_argument("--json", action="store_true", help="output list as JSON")

    args = parser.parse_args()
    root = repo_root()
    cfg = load_config(root)

    if args.command == "harvest":
        backlog = harvest_backlog(root, cfg)
        if args.json:
            print(json.dumps(backlog, indent=2))
        else:
            items = backlog["items"]
            print(f"Backlog Harvested ({SCHEMA}): {len(items)} item(s) in .autonomous/backlog.json")
            for it in items:
                print(f"  - [{it['id']}] ({it['priority']}) {it['title']} [{it['status']}]")
        return EXIT_OK

    if args.command == "list":
        backlog_path = root / ".autonomous" / "backlog.json"
        if not backlog_path.is_file():
            backlog = harvest_backlog(root, cfg)
        else:
            try:
                backlog = json.loads(backlog_path.read_text(encoding="utf-8"))
            except Exception:
                backlog = harvest_backlog(root, cfg)

        items = backlog.get("items", [])
        if args.track:
            items = [it for it in items if it.get("track") == args.track]
        if args.status:
            items = [it for it in items if it.get("status") == args.status]

        if args.json:
            print(json.dumps(items, indent=2))
        else:
            print(f"Backlog Items ({len(items)}):")
            for it in items:
                print(f"  [{it['id']}] {it['priority']} | {it['track']} | {it['title']} ({it['status']})")
        return EXIT_OK

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
