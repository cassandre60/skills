#!/usr/bin/env python3
"""Stream E: defect repair — 0-token intake + red/green oracle (candidates only for direction).

references/08-recursive-self-improvement.md contract instantiation: the fleet
repairs bugs a human already justified. The pieces:

* ``intake`` — mine bug-report files (docs/bugreports/*.md style, one issue
  per file with ``## Expected`` / ``## Actual`` / optional ``## Repro hint``,
  ``## Repro hints`` sections) into candidates. A report that cannot become a
  failing test (missing expected/actual facts) is BOUNCED BACK with the
  missing-facts comment; the tool never writes to the repo.
* ``verify-red`` — the Breaker's anchor: the repro test must fail right now
  (the fix intentionally does not exist yet) and NOTHING outside tests/ may
  be changed vs. --base-ref. Writes the stream-E evidence JSON (base sha,
  red output tail, changed files). If the repo is already green through the
  repro, there is no baseline proof and the tool refuses.
* ``verify-green`` — the Builder's gate: the repro test now passes, the diff
  vs. the recorded base contains at least one non-test fix file (bounded by
  --max-fix-files), and --spot-check optionally runs the mutation engine's
  ``--diff-base`` on every fix file and requires ZERO surviving mutants on
  the touched lines.

Scope discipline: the tool writes only the evidence file you point it at
(--evidence); the fix itself stays the worker's minimal diff. Drift needs
the repro facts — guessing is banned by contract.

Usage:
  python3 scripts/autonomous/repair.py intake --reports docs/bugreports --json
  python3 scripts/autonomous/repair.py verify-red --test-script tests/test_repro.py \
      --evidence evidence.json [--base-ref origin/main]
  python3 scripts/autonomous/repair.py verify-green --test-cmd "python3 -m pytest -q" \
      --test-id tests/test_repro.py --evidence evidence.json [--spot-check 2]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import EXIT_OK, Fatal, UsageError, eprint  # noqa: E402

SCHEMA = "stream-e/1"
SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

BOUNCE_COMMENT = """\
## Bounced back: missing repro facts (Stream E)

This defect candidate cannot become a failing test yet: `{report}` is
missing {missing}.

Please comment with:
{asks}

(Stream E bounce-back per references/08: candidates the reproducer cannot
turn into a failing test are bounced with the missing facts — zero code
changes were made to this repository.)
"""


def git_out(base_cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(base_cwd), capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise Fatal(f"git {' '.join(args)}: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def _is_noise(name: str) -> bool:
    parts = name.split("/")
    return "__pycache__" in parts or any(p.endswith((".pyc", ".pyo")) for p in parts)


def changed_files(repo: Path, base: str) -> List[str]:
    names = set(git_out(repo, "diff", "--name-only", base).splitlines())
    # -uall so an untracked *directory* shows individual files instead of the
    # collapsed "?? tests/" entry.
    status = git_out(repo, "status", "--porcelain", "-uall")
    for line in status.splitlines():
        if not line.strip():
            continue
        _, _, path = line.partition(" ")
        names.add(path.strip().strip('"'))
    return sorted(n for n in names if n and not _is_noise(n))


def parse_report(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        match = SECTION_RE.match(line)
        if match:
            current = match.group(1).lower().strip()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    def bodies(*names: str) -> Optional[str]:
        for name in names:
            body = "\n".join(sections.get(name, [])).strip()
            if body:
                return body
        return None

    expected = bodies("expected", "expected behavior")
    actual = bodies("actual", "actual behavior", "observed")
    hints = bodies("repro hint", "repro hints", "reproduction", "steps")
    missing: List[str] = []
    if not expected:
        missing.append("expected")
    if not actual:
        missing.append("actual")
    data: Dict[str, Any] = {"report": str(path), "expected": expected, "actual": actual}
    if hints:
        data["repro_hint"] = hints
    if missing:
        data["kind"] = "bounce"
        data["missing"] = missing
        data["bounce_comment"] = BOUNCE_COMMENT.format(
            report=str(path),
            missing=f"the section(s) {', '.join(missing)}",
            asks="\n".join(f"- {s}" for s in ("Expected behavior", "Actual behavior", "minimal repro snippet")),
        )
    else:
        data["kind"] = "candidate"
    return data


def cmd_intake(args: argparse.Namespace) -> int:
    base = Path.cwd()
    reports: List[Path] = []
    for raw in args.reports:
        path = base / raw
        if path.is_dir():
            reports.extend(sorted(p for p in path.glob("*.md")))
        elif path.is_file():
            reports.append(path)
        else:
            raise Fatal(f"bug report path not found: {path}")
    if not reports:
        raise Fatal(f"no bug reports found under {args.reports}")
    candidates = [parse_report(path) for path in reports]
    bounces = [c for c in candidates if c["kind"] == "bounce"]
    for bounce in bounces:
        eprint("<!-- paste into the issue thread -->")
        eprint(bounce["bounce_comment"])
    payload = {
        "schema": SCHEMA,
        "tool": "repair.py intake",
        "reports": [str(r) for r in reports],
        "candidates": candidates,
        "note": "Candidates name expected vs actual. Reproducible ones go to the Breaker phase;"
        " bounces are comments asking for the missing facts — no code is guessed at.",
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for c in candidates:
            if c["kind"] == "candidate":
                eprint(f"[candidate] {c['report']}: expected={c['expected']!r}")
            else:
                eprint(f"[bounce] {c['report']}: missing {', '.join(c['missing'])}")
    return EXIT_OK if not bounces else 1


def _test_argv(args: argparse.Namespace) -> List[str]:
    if args.test_script:
        script = Path(args.test_script)
        if not script.is_file():
            raise Fatal(f"--test-script is not a file: {script}")
        return [args.runner, str(script)]
    if not args.test_cmd or not args.test_id:
        raise Fatal("needs --test-script, or --test-cmd together with --test-id")
    return shlex.split(args.test_cmd) + [args.test_id]


def _run_test(argv: List[str], cwd: Path, timeout: int) -> Tuple[int, str]:
    proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    tail = ((proc.stdout or "") + (proc.stderr or "")).strip()[-600:]
    return proc.returncode, tail


def load_evidence(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise Fatal(f"evidence file not found: {path} (run verify-red first)")
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_verify_red(args: argparse.Namespace) -> int:
    repo = Path.cwd()
    base_ref = args.base_ref
    base_sha = git_out(repo, "rev-parse", base_ref)
    argv = _test_argv(args)
    changed = changed_files(repo, base_sha)
    src_changes = [n for n in changed if not n.startswith("tests/")]
    if src_changes:
        eprint(
            f"[!] non-test changes present at red time: {src_changes}. "
            "The Breaker's anchor needs ZERO source diffs vs "
            f"{base_ref} — write only the repro test, then re-run."
        )
        return 3
    if os.path.exists(args.evidence):
        raise Fatal(f"evidence file already exists: {args.evidence}")
    try:
        rc, tail = _run_test(argv, repo, args.timeout)
    except subprocess.TimeoutExpired:
        rc, tail = 124, f"timed out after {args.timeout}s"
    if rc == 0:
        eprint(
            "[!] repro test is already green at base — that is no baseline. "
            "Record red first (verify the bug reproduces before building)."
        )
        return 3
    evidence = {
        "schema": SCHEMA,
        "tool": "repair.py verify-red",
        "base": {"ref": base_ref, "sha": base_sha},
        "red": {
            "verified": True,
            "returncode": rc,
            "output_tail": tail,
            "test": argv[-1] if args.test_script else args.test_id,
        },
        "changed_files": changed,
        "issue": args.issue,
    }
    path = Path(args.evidence)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    eprint(f"evidence: {path} (red on {base_sha[:9]} recorded; fix may now land)")
    if args.json:
        print(json.dumps(evidence, indent=2))
    return EXIT_OK


def cmd_verify_green(args: argparse.Namespace) -> int:
    repo = Path.cwd()
    evidence = load_evidence(Path(args.evidence))
    base_sha = (evidence.get("base") or {}).get("sha")
    if not base_sha:
        raise Fatal("evidence JSON has no base.sha; verify-red must run first")
    argv = _test_argv(args)
    try:
        rc, tail = _run_test(argv, repo, args.timeout)
    except subprocess.TimeoutExpired:
        rc, tail = 124, f"timed out after {args.timeout}s"
    if rc != 0:
        eprint(f"[!] repro test still red (exit {rc}). It must pass before verify-green.")
        return 3
    changed = changed_files(repo, base_sha)
    evidence_path = Path(args.evidence).resolve()
    fix_files = [
        n for n in changed
        if not n.startswith("tests/")
        and Path(n).resolve() != evidence_path
    ]
    if not fix_files:
        eprint(
            "[!] no fix present: the diff vs the recorded base contains only "
            "tests. The defect repair needs a minimal fix, not the repro alone."
        )
        return 3
    if len(fix_files) > args.max_fix_files:
        eprint(
            f"[!] scope discipline: {len(fix_files)} non-test files changed "
            f"(cap {args.max_fix_files}). Minimal fix only; drive-by refactors "
            "are separate work."
        )
        return 3
    spot: Dict[str, Any] = {"ran": False}
    if args.spot_check:
        spot_cmd = args.spot_test_cmd or (
            f"{args.runner} {args.test_script}" if args.test_script else args.test_cmd
        )
        if not spot_cmd:
            raise Fatal(
                "--spot-check needs a command that can kill the mutants: pass "
                "--test-script or --test-cmd (with the scoped test in it). A "
                "stub command would let every mutant survive."
            )
        engine = Path(__file__).resolve().parent / "mutation_engine.py"
        spot_args = [
            sys.executable, str(engine), "--output-json", str(repo / "_spot.json"),
            "--max-mutants", str(args.spot_check),
            "--diff-base", base_sha,
        ]
        total_survivors = 0
        detail: List[Dict[str, Any]] = []
        for fix_file in fix_files:
            try:
                proc = subprocess.run(
                    spot_args + ["--source", fix_file, "--test-cmd", spot_cmd],
                    cwd=str(repo), capture_output=True, text=True,
                    timeout=max(args.timeout * args.spot_check, 120),
                )
            except subprocess.TimeoutExpired as exc:
                raise Fatal(f"engine spot-check timed out on {fix_file}: {exc}") from exc
            spot_json_path = repo / "_spot.json"
            if not spot_json_path.is_file():
                raise Fatal(f"engine spot-check produced no results: {(proc.stderr or proc.stdout).strip()[-300:]}")
            results = json.loads(spot_json_path.read_text())
            survivors = len(results.get("survived") or [])
            total_survivors += survivors
            detail.append({"file": fix_file, "survivors": survivors, "mutants": args.spot_check})
            eprint(f"[spot-check] {fix_file}: {survivors} survivor(s) over {args.spot_check} mutants")
        spot = {"ran": True, "file": fix_files[0], "survivors": total_survivors, "detail": detail}
        if total_survivors:
            eprint(
                "[!] mutation spot-check found surviving mutants on the touched"
                " lines — harden the test or triage as equivalent before green."
            )
            (repo / "_spot.json").unlink(missing_ok=True)
            return 3
        (repo / "_spot.json").unlink(missing_ok=True)
    evidence.update(
        {
            "tool": "repair.py verify-green",
            "green": {
                "verified": True,
                "returncode": 0,
                "output_tail": tail,
                "test": argv[-1] if args.test_script else args.test_id,
            },
            "fix_files": fix_files,
            "spot_check": spot,
        }
    )
    Path(args.evidence).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    eprint(f"evidence updated: {args.evidence} (green ok; fix files: {fix_files})")
    if args.json:
        print(json.dumps(evidence, indent=2))
    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    sub = parser.add_subparsers(dest="cmd", required=True)

    intake = sub.add_parser("intake", help="mine bug reports into candidates / bounces")
    intake.add_argument("--reports", nargs="+", help="bug report files or a directory (e.g. docs/bugreports)")
    intake.add_argument("--json", action="store_true")
    intake.set_defaults(func=cmd_intake)

    red = sub.add_parser("verify-red", help="record the baseline red (Breaker anchor)")
    red.add_argument("--evidence", default=".autonomous/repair-evidence.json")
    red.add_argument("--base-ref", default="origin/main")
    red.add_argument("--test-script", help="single runnable repro script")
    red.add_argument("--test-cmd", help="test runner command to prefix")
    red.add_argument("--test-id", help="pytest-style node id appended to --test-cmd")
    red.add_argument("--runner", default="python3", help="interpreter for --test-script")
    red.add_argument("--timeout", type=int, default=120)
    red.add_argument("--issue", help="issue id or number this repair addresses")
    red.add_argument("--json", action="store_true")
    red.set_defaults(func=cmd_verify_red)

    green = sub.add_parser("verify-green", help="verify repro green + minimal fix (+ spot-check)")
    green.add_argument("--evidence", default=".autonomous/repair-evidence.json")
    green.add_argument("--test-script", help="single runnable repro script")
    green.add_argument("--test-cmd", help="test runner command to prefix")
    green.add_argument("--test-id", help="pytest-style node id appended to --test-cmd")
    green.add_argument("--runner", default="python3", help="interpreter for --test-script")
    green.add_argument("--max-fix-files", type=int, default=5)
    green.add_argument("--spot-check", type=int, help="run the mutation engine --diff-base spot-check with this many mutants per fix file")
    green.add_argument("--spot-test-cmd", help="optional scoped runner for the spot-check (defaults to --test-cmd or a stub)")
    green.add_argument("--timeout", type=int, default=120)
    green.add_argument("--issue", help="issue id or number this repair addresses")
    green.add_argument("--json", action="store_true")
    green.set_defaults(func=cmd_verify_green)

    args = parser.parse_args()
    try:
        return args.func(args)
    except Fatal as exc:
        eprint(f"[!] {exc}")
        return 2
    except UsageError as exc:
        eprint(f"[!] {exc}")
        return 2
    except (json.JSONDecodeError, OSError) as exc:
        eprint(f"[!] {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
