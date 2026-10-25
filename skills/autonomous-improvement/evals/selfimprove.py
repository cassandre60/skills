#!/usr/bin/env python3
"""Self-improvement mechanics (Phase 5.5): the loop, minus the judgment.

Protocol: evals/selfimprove.md. This tool is deliberately mechanical:
`propose` lists below-threshold runs; `apply` applies one diff in a worktree
and REFUSES protected paths before touching anything; `evaluate` runs the
deterministic layers and checks the owner's gates; `revert` drops the
worktree and appends the negative-ledger line. It never spends tokens, never
merges, and cannot verify an improvement without recorded matrix results —
in that case the only honest outcome is a revert.

Usage:
  python3 evals/selfimprove.py propose
  python3 evals/selfimprove.py apply --diff <patch> --motivation "<text>"
  python3 evals/selfimprove.py evaluate --worktree <path>
  python3 evals/selfimprove.py revert --worktree <path> --why "<text>"
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

EVALS_DIR = Path(__file__).resolve().parent
SKILL_ROOT = EVALS_DIR.parent
REPO_ROOT = SKILL_ROOT.parent
RESULTS_DIR = EVALS_DIR / "results"
GATES_JSON = EVALS_DIR / "gates.json"
NEGATIVE_LEDGER = SKILL_ROOT / ".autonomous" / "negative-ledger.md"

# The referee, restated as path rules (the plan's list plus meta.py's crown
# jewels, mapped to files). A diff touching any of these is refused outright.
PROTECTED_PREFIXES = (
    "skills/autonomous-improvement/evals/",
    "skills/autonomous-improvement/tests/pins.json",
    "skills/autonomous-improvement/tests/pins.baseline.json",
    "skills/autonomous-improvement/tests/test_weakening_check.py",
    "skills/autonomous-improvement/assets/protected-paths.fragment.txt",
)
LAWS_SECTION = "## The five laws"
CONTRACT_MARKERS = ("stream contract", "tier floors", "autonomy tiers")


def _below_threshold(threshold: float) -> List[dict]:
    out = []
    if RESULTS_DIR.is_dir():
        for path in sorted(RESULTS_DIR.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if record.get("schema") != "matrix-record/1":
                continue
            score = record.get("score")
            if isinstance(score, (int, float)) and score < threshold:
                out.append(record)
    return out


def cmd_propose(_: argparse.Namespace) -> int:
    gates = json.loads(GATES_JSON.read_text(encoding="utf-8"))["gates"]
    runs = _below_threshold(gates.get("dev_mean_min_delta", 0.05) + 0.25)
    print("Below-threshold matrix runs (transcripts stay local):")
    for record in runs:
        print(
            f"  {record.get('case')} run#{record.get('run_index')} "
            f"score={record.get('score')} hard_fails={record.get('hard_fails')}"
        )
    if not runs:
        print("  none recorded. Without recorded runs the loop cannot propose —")
        print("  record the dev matrix first (owner go-ahead required; the")
        print("  commands are in .autonomous/owner-asks.md).")
    print(
        "\nOne iteration = one change: file + diff + motivating evidence;\n"
        "apply via `selfimprove.py apply --diff <patch> --motivation ...`.\n"
        "Protected paths are refused mechanically; see evals/selfimprove.md."
    )
    return 0


def _diff_paths(patch_text: str) -> List[str]:
    paths = []
    for line in patch_text.splitlines():
        if line.startswith("diff --git a/"):
            paths.append(line.split(" b/", 1)[0][len("diff --git a/"):])
    return paths


def _protected_hits(patch_text: str) -> List[str]:
    hits = [p for p in _diff_paths(patch_text) if p.startswith(PROTECTED_PREFIXES)]
    if any(p.endswith("references/08-recursive-self-improvement.md") for p in _diff_paths(patch_text)):
        # The laws/contract/tier sections of reference 08 are referee text:
        # a diff that edits that file may only be judged by the owner.
        hits.append("references/08-recursive-self-improvement.md (stream contract / tiers — owner-only)")
    return hits


def cmd_apply(args: argparse.Namespace) -> int:
    patch_text = Path(args.diff).read_text(encoding="utf-8")
    hits = _protected_hits(patch_text)
    if hits:
        for hit in hits:
            print(f"[refused] protected path: {hit}", file=sys.stderr)
        print(
            "The improver never edits its own referee. Proposal rejected "
            "before application; take it to the owner instead.",
            file=sys.stderr,
        )
        return 2
    worktree = REPO_ROOT.parent / f"{REPO_ROOT.name}-selfimprove"
    if worktree.exists():
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=str(REPO_ROOT), capture_output=True, check=False,
        )
    subprocess.run(
        ["git", "worktree", "add", "-b", "autoplan/selfimprove-iteration", str(worktree)],
        cwd=str(REPO_ROOT), capture_output=True, check=True,
    )
    applied = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", str(Path(args.diff).resolve())],
        cwd=str(worktree), capture_output=True, text=True,
    )
    if applied.returncode != 0:
        print(f"[refused] patch does not apply: {applied.stderr.strip()}", file=sys.stderr)
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=str(REPO_ROOT), capture_output=True, check=False,
        )
        return 2
    print(f"[+] applied in {worktree} (motivation: {args.motivation[:80]}...)")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    worktree = Path(args.worktree)
    skill = worktree / "skills" / "autonomous-improvement"
    det = subprocess.run(
        [sys.executable, "evals/run.py", "--deterministic"],
        cwd=str(skill), capture_output=True, text=True, timeout=600,
    )
    print(det.stdout[-500:])
    if det.returncode != 0:
        print("[evaluate] deterministic layer failed: the change is NOT kept", file=sys.stderr)
        return 1
    records = [p for p in RESULTS_DIR.glob("*.json")] if RESULTS_DIR.is_dir() else []
    if not records:
        print(
            "[evaluate] no recorded matrix results: improvement UNVERIFIABLE —\n"
            "record the dev matrix (owner go-ahead; see .autonomous/owner-asks.md)\n"
            "before this iteration can keep any change. Revert.",
            file=sys.stderr,
        )
        return 1
    print("[evaluate] deterministic layer green; matrix gates need recorded runs to compare")
    return 0


def cmd_revert(args: argparse.Namespace) -> int:
    worktree = Path(args.worktree)
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree)],
        cwd=str(REPO_ROOT), capture_output=True, check=False,
    )
    subprocess.run(
        ["git", "worktree", "prune"], cwd=str(REPO_ROOT), capture_output=True, check=False
    )
    subprocess.run(
        ["git", "branch", "-D", "autoplan/selfimprove-iteration"],
        cwd=str(REPO_ROOT), capture_output=True, check=False,
    )
    NEGATIVE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with open(NEGATIVE_LEDGER, "a", encoding="utf-8") as handle:
        handle.write(f"- {stamp} reverted: {args.why}\n")
    print(f"[+] reverted; negative-ledger line appended to {NEGATIVE_LEDGER}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("propose", help="list below-threshold runs and the protocol")
    apply_p = sub.add_parser("apply", help="apply one non-protected diff in a worktree")
    apply_p.add_argument("--diff", required=True, help="patch file path")
    apply_p.add_argument("--motivation", required=True, help="the evidence behind the change")
    evaluate_p = sub.add_parser("evaluate", help="run the deterministic layers and gates")
    evaluate_p.add_argument("--worktree", required=True)
    revert_p = sub.add_parser("revert", help="drop the worktree and record why it lost")
    revert_p.add_argument("--worktree", required=True)
    revert_p.add_argument("--why", required=True, help="what was tried and why it lost")
    args = parser.parse_args(argv)
    if args.cmd == "propose":
        return cmd_propose(args)
    if args.cmd == "apply":
        return cmd_apply(args)
    if args.cmd == "evaluate":
        return cmd_evaluate(args)
    return cmd_revert(args)


if __name__ == "__main__":
    sys.exit(main())
