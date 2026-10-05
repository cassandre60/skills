#!/usr/bin/env python3
"""Run the red-team corpus against the gates and report recall.

Corpus layout:
  redteam/cases/<case-id>/patch.diff   a deliberately WRONG or gaming change (git diff format)
  redteam/cases/<case-id>/case.md      what it does, which gate should catch it

For each case we apply the patch to a clean worktree of a ref (default HEAD), run the gate
command, and expect a NON-ZERO exit. A zero exit means the bad change ESCAPED: the gate is weak.

Usage:
  redteam_runner.py --gate-cmd "./scripts/gates.sh" [--ref HEAD] [--cases redteam/cases] [--allow-stale]
Exit codes: 0 all rejected, 1 at least one escaped, 2 stale/unappliable patches (unless --allow-stale).
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile


def run(cmd, cwd, shell=False):
    return subprocess.run(cmd, cwd=cwd, shell=shell, capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate-cmd", required=True)
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--cases", default="redteam/cases")
    ap.add_argument("--allow-stale", action="store_true")
    args = ap.parse_args()

    repo = os.getcwd()
    if not os.path.isdir(args.cases):
        print(f"no corpus at {args.cases}")
        return 2
    cases = sorted(d for d in os.listdir(args.cases) if os.path.isfile(os.path.join(args.cases, d, "patch.diff")))
    if not cases:
        print("corpus is empty: add cases before trusting the gates")
        return 2

    rejected, escaped, stale = [], [], []
    for case in cases:
        patch = os.path.abspath(os.path.join(args.cases, case, "patch.diff"))
        wt = tempfile.mkdtemp(prefix="redteam-")
        try:
            if run(["git", "worktree", "add", "--detach", wt, args.ref], repo).returncode != 0:
                print(f"cannot create worktree for {case}", file=sys.stderr)
                return 2
            applied = run(["git", "apply", "--whitespace=nowarn", patch], wt)
            if applied.returncode != 0:
                stale.append(case)
                print(f"STALE    {case}: patch no longer applies ({applied.stderr.strip().splitlines()[:1]})")
                continue
            result = run(args.gate_cmd, wt, shell=True)
            if result.returncode == 0:
                escaped.append(case)
                print(f"ESCAPED  {case}: gates passed a known-bad change")
            else:
                rejected.append(case)
                print(f"rejected {case}")
        finally:
            run(["git", "worktree", "remove", "--force", wt], repo)
            shutil.rmtree(wt, ignore_errors=True)

    total = len(cases) - len(stale)
    recall = (len(rejected) / total) if total else 0.0
    print(f"\nrecall: {len(rejected)}/{total} = {recall:.0%}   stale: {len(stale)}")
    if escaped:
        print("Escaped cases mean a gate is missing or weak. Strengthen the gate; do not delete the case.")
        return 1
    if stale and not args.allow_stale:
        print("Stale patches must be regenerated from their case.md description.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
