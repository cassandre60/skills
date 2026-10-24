#!/usr/bin/env python3
"""Stream F: flake & determinism — finder (0-token candidates) + N-run oracle.

references/08-recursive-self-improvement.md contract instantiation:

* ``finder`` — emits CANDIDATES only (never verdicts): a static scan of test
  files for flake idioms (sleep, timed waits, wall-clock reads, timers,
  unseeded randomness, order-sensitive collection asserts) and, when a run
  history JSON is available, mining of tests that showed BOTH pass and fail
  outcomes. Ranked by flake rate (x optional CI cost seconds).
* ``oracle`` — the verdict machine. Runs one test N>=20 times (runnable
  script or pytest node id via --test-cmd), optionally after a peers
  command, and demands ZERO failures after a fix. "Passed twice in a row"
  is not an oracle. Cause class: ``order-dependence`` when only the
  after-peers matrix fails; ``timing-or-shared-state`` when the isolated
  matrix itself flakes.

Banned fix shapes (05/08): longer sleeps, blanket retries, quarantine or
skip without a root-cause issue + expiry.

Usage:
  python3 scripts/autonomous/flakes.py finder --history history.json --json
  python3 scripts/autonomous/flakes.py finder --paths tests --json
  python3 scripts/autonomous/flakes.py oracle --test-script tests/t.py --runs 20
  python3 scripts/autonomous/flakes.py oracle --test-cmd "python3 -m pytest -q" \
      --test-id tests/test_x.py::test_y --runs 20
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import EXIT_OK, Fatal, eprint  # noqa: E402

HISTORY_SCHEMA = "stream-f/1"
# Each rule: (id, regex, human description of the flake mechanism).
STATIC_RULES: List[Tuple[str, str, str]] = [
    ("F-S1-sleep", r"\bsleep\s*\(", "sleep in a test is a timing guess, not a wait"),
    ("F-S2-wallclock", r"\b(datetime\.now|date\.today|time\.time|perf_counter|Date\.now|new Date)\b",
     "wall-clock read makes the assertion environment-dependent"),
    ("F-S3-timed-wait", r"\b(setTimeout|Future\.delayed|waitForTimeout|await\s+delay)\b",
     "fixed timed wait instead of poll-with-deadline"),
    ("F-S4-unseeded-random", r"\bimport\s+random\b|\brandom\.\w+\(", 
     "unseeded randomness makes the assertion nondeterministic"),
    ("F-S5-unordered-assert", r"\bassert[A-Z]\w*\(.*(\bset\(|\bfrozenset\(|\bCounter\()",
     "assertion over an unordered collection depends on iteration order"),
]


def eprint_rule(rule: Tuple[str, str, str]) -> str:
    rule_id, pattern, why = rule
    return f"{rule_id}: {why}"


def scan_static(paths: List[Path], label_base: Optional[Path] = None) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        hits: List[Dict[str, Any]] = []
        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "//")):
                continue
            for rule_id, pattern, why in STATIC_RULES:
                if re.search(pattern, line):
                    hits.append(
                        {"line": lineno, "pattern": rule_id, "why": why, "text": line.strip()[:120]}
                    )
        if hits:
            label = str(path)
            if label_base is not None and path.is_relative_to(label_base):
                label = str(path.relative_to(label_base))
            candidates.append(
                {
                    "test": label,
                    "kind": "static",
                    "hits": len(hits),
                    "evidence": {"hits": [f"{h['pattern']}:{h['line']} {h['text']}" for h in hits]},
                    "rank": float(len(hits)),
                }
            )
    candidates.sort(key=lambda c: (-c["rank"], c["test"]))
    return candidates


def _label_test_values(runs: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    per_test: Dict[str, List[str]] = {}
    for run in runs:
        for test, outcome in (run.get("labels") or {}).items():
            per_test.setdefault(str(test), []).append(str(outcome))
    return per_test


def mine_history(history: Dict[str, Any]) -> List[Dict[str, Any]]:
    runs = history.get("runs") or []
    meta_cost = (history.get("meta") or {}).get("cost_s") or {}
    candidates: List[Dict[str, Any]] = []
    by_sha: Dict[str, Dict[str, List[str]]] = {}

    for run in runs:
        if "sha" not in run:
            raise Fatal(f"history run missing 'sha' (schema {HISTORY_SCHEMA})")
        sha = str(run["sha"])
        for test, outcome in (run.get("labels") or {}).items():
            by_sha.setdefault(sha, {}).setdefault(str(test), []).append(str(outcome))

    for test, outcomes in _label_test_values(runs).items():
        passes = outcomes.count("pass")
        fails = outcomes.count("fail")
        total = passes + fails
        if total == 0 or passes == 0 or fails == 0:
            continue  # stable pass, stable fail, or skipped-only: no candidate
        # "same test with both outcomes on same sha-range": strongest variant
        # is a sha whose runs disagree with themselves.
        same_sha = any(
            len(shas.get(test, [])) >= 1
            and len(set(shas.get(test, []))) > 1
            for shas in by_sha.values()
        )
        rate = round(fails / total, 4)
        cost = float(meta_cost.get(test, 1.0))
        candidates.append(
            {
                "test": test,
                "kind": "history",
                "flake_rate": rate,
                "outcomes": {"pass": passes, "fail": fails},
                "same_sha": same_sha,
                "evidence": {
                    "mechanism": "mixed outcomes recorded for this test id"
                    + (" on the same sha (re-run disagreement)" if same_sha else "")
                },
                "rank": round(rate * max(cost, 0.1), 4),
            }
        )
    candidates.sort(key=lambda c: (-c["rank"], str(c["test"])))
    return candidates


def cmd_finder(args: argparse.Namespace) -> int:
    candidates: List[Dict[str, Any]] = []
    if args.history:
        raw = Path(args.history).read_text(encoding="utf-8")
        history = json.loads(raw)
        candidates.extend(mine_history(history))
    scans: List[Tuple[List[Path], Path]] = []
    for raw in args.paths or []:
        path = Path(raw)
        if path.is_dir():
            scans.append(
                (sorted(p for p in path.glob("**/*") if p.is_file()), path.parent)
            )
        elif path.is_file():
            scans.append(([path], path.parent))
    for files, label_base in scans:
        candidates.extend(scan_static(files, label_base=label_base))
    payload = {"schema": HISTORY_SCHEMA, "tool": "flakes.py finder",
               "candidates": candidates,
               "note": "Candidates only. The N-run oracle decides; never quarantine on a finder hit alone."}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        if not candidates:
            print("finder: no candidates (0 static hits, no mixed-outcome history)")
        for c in candidates:
            if c["kind"] == "history":
                eprint(
                    f"[candidate] {c['test']}: rate {c['flake_rate']} "
                    f"({c['truth']['fail']} fail / {c['truth']['total']}, same_sha={c['same_sha']})"
                )
            else:
                eprint(f"[candidate] {c['test']}: static idiom x{c['hits']}")
    return EXIT_OK


def _one_run(argv: List[str], cwd: Path, timeout: int) -> str:
    try:
        proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return "pass" if proc.returncode == 0 else "fail"
    except subprocess.TimeoutExpired:
        return "timeout"


def run_matrix(argv: List[str], cwd: Path, runs: int, timeout: int,
               peers: Optional[List[str]] = None) -> Dict[str, Any]:
    """One matrix: argv x runs from the ORACLE CALLER's cwd (peers run first
    in each rep when given). The caller owns cwd semantics — invoke inside
    the repo, not from elsewhere."""
    counts = {"pass": 0, "fail": 0, "timeout": 0}
    failures: List[int] = []
    for index in range(runs):
        if peers:
            peer = subprocess.run(
                peers, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
            )
            if peer.returncode != 0:
                counts["timeout"] += 0
                # A broken peers command is a fix-shape bug, not a flake.
                raise Fatal(f"peers command failed (exit {peer.returncode}); fix the setup, not the test")
        outcome = _one_run(argv, cwd, timeout)
        counts[outcome] += 1
        if outcome != "pass":
            failures.append(index)
    matrix: Dict[str, Any] = {
        "runs": runs,
        "counts": counts,
        "fail_indexes": failures[:20],
    }
    matrix["flake_rate"] = round((counts["fail"] + counts["timeout"]) / max(runs, 1), 4)
    return matrix


def cmd_oracle(args: argparse.Namespace) -> int:
    if args.test_script:
        script = Path(args.test_script)
        if not script.is_file():
            raise Fatal(f"--test-script is not a file: {script}")
        argv = [args.runner, str(script)]
    else:
        if not args.test_cmd or not args.test_id:
            raise Fatal("oracle needs --test-script, or --test-cmd together with --test-id")
        argv = shlex.split(args.test_cmd) + [args.test_id]
    if args.runs < 1:
        raise Fatal("--runs must be >= 1")
    peers: Optional[List[str]] = shlex.split(args.peers_cmd) if args.peers_cmd else None

    cwd = Path.cwd()
    eprint(f"oracle: isolated x{args.runs} ({' '.join(argv[:2])}...)")
    isolated = run_matrix(argv, cwd, args.runs, args.timeout)
    cause_class: Optional[str] = None
    verdict = "stable" if isolated["counts"]["pass"] == args.runs else "flaky"
    if verdict == "stable" and peers:
        eprint(f"oracle: after-peers x{args.runs}")
        after_peers = run_matrix(argv, cwd, args.runs, args.timeout, peers=peers)
        if after_peers["counts"]["pass"] != args.runs:
            verdict = "flaky"
            cause_class = "order-dependence"
    elif verdict == "flaky" and peers:
        after_peers = run_matrix(argv, cwd, args.runs, args.timeout, peers=peers)
        after_peers["note"] = "isolated already flaky; peers matrix recorded for comparison"
    else:
        after_peers = None
    if verdict == "flaky" and cause_class is None:
        cause_class = "timing-or-shared-state" if isolated["counts"]["timeout"] == 0 else "timing"

    payload: Dict[str, Any] = {
        "tool": "flakes.py oracle",
        "test": args.test_id or str(args.test_script),
        "runs": args.runs,
        "modes": {"isolated": isolated, "after_peers": after_peers},
        "verdict": verdict,
        "cause_class": cause_class,
        "note": "Oracle demands ZERO failing runs after a fix (0/N); 'passed twice in a row' is not evidence.",
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        eprint(json.dumps(payload["modes"]["isolated"]["counts"]))
        eprint(f"verdict: {verdict}" + (f"; cause: {cause_class}" if cause_class else ""))
    return EXIT_OK if verdict == "stable" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    sub = parser.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("finder", help="emit flake candidates (static idioms + run history)")
    f.add_argument("--history", help="run history JSON (runs[].sha + runs[].labels)")
    f.add_argument("--paths", nargs="*", help="test files or dirs to statically scan")
    f.add_argument("--json", action="store_true")

    o = sub.add_parser("oracle", help="N-run verdict matrix for one test")
    o.add_argument("--test-script", help="single runnable test file (argv[runner, path])")
    o.add_argument("--test-cmd", help="test runner command to prefix, e.g. '\"python3 -m pytest -q\"'")
    o.add_argument("--test-id", help="pytest-style node id appended to --test-cmd")
    o.add_argument("--runner", default="python3", help="interpreter for --test-script (default python3)")
    o.add_argument("--runs", type=int, default=20, help="matrix size (contract: >= 20)")
    o.add_argument("--timeout", type=int, default=120, help="per-run seconds")
    o.add_argument("--peers-cmd", help="run this command before each rep to probe order-dependence")
    o.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        if args.cmd == "finder":
            return cmd_finder(args)
        return cmd_oracle(args)
    except Fatal as exc:
        eprint(f"[!] {exc}")
        return 2
    except (json.JSONDecodeError, FileNotFoundError, OSError) as exc:
        eprint(f"[!] {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
