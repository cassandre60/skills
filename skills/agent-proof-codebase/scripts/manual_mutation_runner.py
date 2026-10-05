#!/usr/bin/env python3
"""Tiny mutation runner for ecosystems without a mature mutation tool.

A mutants file (JSON list) names exact source edits:
  [{"file": "lib/core/score.dart", "find": "a >= b", "replace": "a > b",
    "occurrence": 1, "note": "boundary"}, ...]
Mark a mutant {"equivalent": true, "note": "why"} when it cannot change behavior (e.g. x<0 -> x<=0
inside abs); it is expected to survive and does not fail the run, but is listed for review.
Each mutant is applied to the working tree, the test command runs, then the file is restored.
A mutant is KILLED when the tests fail (good) and SURVIVES when they pass (a test gap).
Survivors fail the run. Keep mutants hand-picked around invariants: boundaries, ordering,
negation, off-by-one, removed statements, swapped arguments, dropped error handling.

Usage:
  manual_mutation_runner.py --mutants .gates/mutants.json --test-cmd "dart test test/core" [--timeout 600]
Safety: refuses to run if a target file has uncommitted changes (use --force to override);
files are always restored in a finally block.
Exit codes: 0 all killed, 1 survivors, 2 setup error.
"""
import argparse
import json
import subprocess
import sys


def dirty(path):
    out = subprocess.run(["git", "status", "--porcelain", "--", path], capture_output=True, text=True)
    return bool(out.stdout.strip())


def nth_index(text, needle, n):
    idx = -1
    for _ in range(n):
        idx = text.find(needle, idx + 1)
        if idx == -1:
            return -1
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mutants", required=True)
    ap.add_argument("--test-cmd", required=True)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    with open(args.mutants, encoding="utf-8") as fh:
        mutants = json.load(fh)

    baseline = subprocess.run(args.test_cmd, shell=True, capture_output=True, text=True, timeout=args.timeout)
    if baseline.returncode != 0:
        print("error: tests must pass on unmodified code before mutating", file=sys.stderr)
        return 2

    killed, survived, invalid, equivalent = [], [], [], []
    for i, m in enumerate(mutants, 1):
        path, find, repl = m["file"], m["find"], m["replace"]
        occ = m.get("occurrence", 1)
        if dirty(path) and not args.force:
            print(f"error: {path} has uncommitted changes; commit first or use --force", file=sys.stderr)
            return 2
        with open(path, encoding="utf-8") as fh:
            original = fh.read()
        idx = nth_index(original, find, occ)
        label = f"#{i} {path} [{m.get('note', '')}] {find!r} -> {repl!r}"
        if idx == -1:
            invalid.append(label)
            print(f"INVALID  {label}: pattern not found (mutant list is stale)")
            continue
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(original[:idx] + repl + original[idx + len(find):])
            try:
                res = subprocess.run(args.test_cmd, shell=True, capture_output=True, text=True, timeout=args.timeout)
                rc = res.returncode
            except subprocess.TimeoutExpired:
                rc = 1  # a hang counts as detected
        finally:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(original)
        if rc != 0:
            killed.append(label)
            print(f"killed   {label}")
        elif m.get("equivalent"):
            equivalent.append(label)
            print(f"equivalent (survived, expected) {label}")
        else:
            survived.append(label)
            print(f"SURVIVED {label}")

    total = len(killed) + len(survived)
    score = (len(killed) / total) if total else 0.0
    print(f"\nmutation score: {len(killed)}/{total} = {score:.0%}   invalid: {len(invalid)}   declared-equivalent: {len(equivalent)}")
    if survived:
        print("Survivors show what no test checks. Add a test that fails for each, then re-run.")
        return 1
    return 2 if invalid else 0


if __name__ == "__main__":
    sys.exit(main())
