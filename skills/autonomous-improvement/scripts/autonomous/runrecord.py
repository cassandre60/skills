#!/usr/bin/env python3
"""Run records: structured, redacted-by-construction run history.

Phase 5.1 of the generality plan: real history becomes data instead of
prose. Every run appends one ``run-record/1`` JSON under
``.autonomous/run-records/`` — counts, ids and URLs only. There is no
transcript field to leak and no secret to redact: the schema cannot carry
prose by construction. ``sync`` joins later verdicts (merged/closed from
gh, ``ignored`` from ledger REJECTED/WONTFIX rows); the stateless-wake
launcher writes a fallback record with the exit code and duration so a
record exists even when the model forgets or dies.

Usage:
  python3 scripts/autonomous/runrecord.py write [--outcome O] [--chosen T] ...
  python3 scripts/autonomous/runrecord.py sync
  python3 scripts/autonomous/runrecord.py list
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import eprint, repo_root  # noqa: E402

SCHEMA = "run-record/1"
OUTCOMES = (
    "pr-opened",
    "issue-filed",
    "no-gap",
    "blocked",
    "error",
    "wake-complete",
    "wake-failed",
)

STREAM_TO_TOOL = {
    "A": "mutation",
    "B": "adversarial",
    "C": "fuzz",
    "E": "defect-repair",
    "F": "flake",
    "G": "performance",
    "H": "security",
    "I": "docs-reconcile",
    "J": "simplification",
    "K": "security",
}


def records_dir(root: Path) -> Path:
    return root / ".autonomous" / "run-records"


def _skill_ref() -> str:
    """A stable hash over the skill text the run used (SKILL.md + references,
    falling back to the script tree when the skill docs are not installed)."""
    here = Path(__file__).resolve().parent
    candidates = [here.parent / "SKILL.md", here.parent / "references"]
    digest = hashlib.sha256()
    found = False
    for candidate in candidates:
        if candidate.is_file():
            found = True
            digest.update(candidate.read_bytes())
        elif candidate.is_dir():
            for path in sorted(candidate.rglob("*")):
                if path.is_file():
                    found = True
                    digest.update(path.read_bytes())
    if not found:
        for path in sorted(here.glob("*.py")):
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def _repo_slug(root: Path) -> str:
    proc = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    url = proc.stdout.strip() if proc.returncode == 0 else ""
    if url:
        match = re.search(r"[:/]([^/:]+?)(?:\.git)?/?$", url)
        if match:
            return match.group(1)
    return root.name


def _next_id(directory: Path) -> str:
    highest = 0
    for path in directory.glob("rr-*.json"):
        match = re.match(r"rr-(\d+)", path.stem)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"rr-{highest + 1:04d}"


def _load_records(directory: Path) -> List[Dict[str, Any]]:
    out = []
    for path in sorted(directory.glob("rr-*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def cmd_write(args: argparse.Namespace) -> int:
    root = repo_root()
    directory = records_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": SCHEMA,
        "id": _next_id(directory),
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "skill_ref": _skill_ref(),
        "repo_slug": _repo_slug(root),
        "harness": args.harness,
        "model": args.model,
        "autonomy": args.autonomy,
        "environment": args.environment,
        "source": args.source,
        "passes_run": args.passes,
        "chosen": args.chosen,
        "runner_up": args.runner_up,
        "questions_asked": args.questions,
        "tokens": args.tokens,
        "wall_s": args.wall_s,
        "artifacts": list(args.artifact),
        "outcome": args.outcome,
        "blockers": list(args.blocker),
    }
    if args.exit_code is not None:
        record["exit_code"] = args.exit_code
    path = directory / f"{record['id']}.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[+] run record {path}")
    if args.json:
        print(json.dumps(record, indent=2, sort_keys=True))
    return 0


def _verdict_from_gh(root: Path, artifact: str) -> Optional[str]:
    match = re.search(r"#(\d+)", str(artifact))
    if not match:
        return None
    proc = subprocess.run(
        ["gh", "pr", "view", match.group(1), "--json", "state,mergedAt"],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if data.get("mergedAt"):
        return "merged"
    if str(data.get("state", "")).upper() in ("CLOSED",):
        return "closed"
    return None


def _ledger_rejected_tools(root: Path) -> List[str]:
    ledger = root / "docs" / "AUTONOMOUS_WORK_LEDGER.md"
    if not ledger.is_file():
        return []
    tools = []
    for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
        if "REJECTED" not in line and "WONTFIX" not in line:
            continue
        match = re.match(r"\|\s*\S+\s*\|\s*([A-Z])\s*\(", line)
        if match and match.group(1) in STREAM_TO_TOOL:
            tools.append(STREAM_TO_TOOL[match.group(1)])
    return tools


def cmd_sync(args: argparse.Namespace) -> int:
    root = repo_root()
    directory = records_dir(root)
    if not directory.is_dir():
        eprint("no run records yet; nothing to sync")
        return 0
    rejected_tools = set(_ledger_rejected_tools(root))
    changed = 0
    for record in _load_records(directory):
        if record.get("verdict"):
            continue
        verdict = None
        if record.get("outcome") == "pr-opened":
            for artifact in record.get("artifacts", []):
                verdict = _verdict_from_gh(root, artifact)
                if verdict:
                    break
        if verdict is None and record.get("chosen") in rejected_tools:
            verdict = "ignored"
        if verdict:
            record["verdict"] = verdict
            path = directory / f"{record['id']}.json"
            path.write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            changed += 1
            print(f"[sync] {record['id']}: verdict {verdict}")
    print(f"[+] sync complete: {changed} verdict(s) joined")
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    root = repo_root()
    directory = records_dir(root)
    records = _load_records(directory) if directory.is_dir() else []
    if not records:
        print("no run records")
        return 0
    for record in records:
        verdict = record.get("verdict") or "—"
        print(
            f"{record.get('id', '?'):9} {str(record.get('ts', ''))[:19]:20} "
            f"{str(record.get('outcome', '')):13} {str(record.get('chosen') or '—'):14} "
            f"{verdict}"
        )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    write = sub.add_parser("write", help="append a run record")
    write.add_argument("--outcome", required=True, choices=OUTCOMES)
    write.add_argument("--chosen", default=None)
    write.add_argument("--runner-up", dest="runner_up", default=None)
    write.add_argument("--passes", nargs="*", default=[])
    write.add_argument("--autonomy", default="auto", choices=["auto", "ask"])
    write.add_argument("--environment", default="laptop", choices=["laptop", "vm", "ci"])
    write.add_argument("--source", default="session", help="session | stateless-wake | ...")
    write.add_argument("--harness", default=None)
    write.add_argument("--model", default=None)
    write.add_argument("--questions", dest="questions", type=int, default=0)
    write.add_argument("--tokens", type=int, default=None)
    write.add_argument("--wall-s", dest="wall_s", type=float, default=None)
    write.add_argument("--exit-code", dest="exit_code", type=int, default=None)
    write.add_argument("--artifact", action="append", default=[])
    write.add_argument("--blocker", action="append", default=[])
    write.add_argument("--json", action="store_true")

    sub.add_parser("sync", help="join later verdicts into existing records")
    sub.add_parser("list", help="print the run records")

    args = parser.parse_args(argv)
    if args.cmd == "write":
        return cmd_write(args)
    if args.cmd == "sync":
        return cmd_sync(args)
    return cmd_list(args)


if __name__ == "__main__":
    sys.exit(main())
