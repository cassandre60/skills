#!/usr/bin/env python3
"""Fail when a change touches protected paths (the referee) without approval.

Protected patterns live in a text file, one glob per line ('#' comments allowed).
'*' matches across '/' (fnmatch semantics), so 'tests/golden/*' covers subfolders.

Usage:
  protected_paths_check.py --base origin/main [--head HEAD] [--patterns .gates/protected-paths.txt] [--approved]

Exit codes: 0 nothing protected touched (or --approved), 3 protected paths touched, 2 usage error.
CI should pass --approved only when a designated human reviewer/label has signed off.
"""
import argparse
import fnmatch
import subprocess
import sys


def changed_files(base, head):
    out = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", f"{base}...{head}"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        print(out.stderr, file=sys.stderr)
        sys.exit(2)
    return [line for line in out.stdout.splitlines() if line]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--patterns", default=".gates/protected-paths.txt")
    ap.add_argument("--approved", action="store_true")
    args = ap.parse_args()

    try:
        with open(args.patterns, encoding="utf-8") as fh:
            patterns = [p.strip() for p in fh if p.strip() and not p.lstrip().startswith("#")]
    except OSError as exc:
        print(f"error: cannot read patterns: {exc}", file=sys.stderr)
        return 2

    hits = sorted(
        {(f, p) for f in changed_files(args.base, args.head) for p in patterns if fnmatch.fnmatch(f, p)}
    )
    if not hits:
        print("no protected paths touched")
        return 0
    print("Protected paths touched:")
    for f, p in hits:
        print(f"  {f}   (matches {p})")
    if args.approved:
        print("approved by a human reviewer: allowing")
        return 0
    print("\nThis change edits the referee (gates/specs/baselines/CI). It needs human approval "
          "and must be a separate 'gate-change' PR, not bundled with product code.")
    return 3


if __name__ == "__main__":
    sys.exit(main())
