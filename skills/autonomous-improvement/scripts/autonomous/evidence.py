#!/usr/bin/env python3
"""Fail-to-pass evidence as a machine-checkable artifact (issue #14).

The verify-before/after contract used to be prose pasted into a PR body: a
summary line is indistinguishable from a test that would pass against the
mutant too. This tool performs all three runs itself and writes the exits to
``.autonomous/evidence.json``:

  * red: the new/changed test fails against the surviving mutant (or, for a
    defect, against the unmodified base in a throwaway worktree);
  * green: the same test passes on the clean tree;
  * pass-to-pass: the neighboring suite is unaffected.

``orchestrator.py verify-gate`` validates the artifact (``check``): the base
must still be an ancestor of HEAD and the covered source/test bytes must be
exactly the ones the runs executed, so stale evidence cannot ride along.

Usage:
  python3 scripts/autonomous/evidence.py mutant --engine-json <path> --index <N> \
      --test-cmd "..." [--pass-to-pass-cmd "..."]
  python3 scripts/autonomous/evidence.py defect --test-file <path> --test-cmd "..." \
      [--base origin/main] [--pass-to-pass-cmd "..."]
  python3 scripts/autonomous/evidence.py check [--evidence <path>]
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    EXIT_NEGATIVE,
    EXIT_OK,
    EnvError,
    Fatal,
    UsageError,
    format_exit,
    git,
    is_dirty,
    load_config,
    rel_path,
    repo_root,
    write_json_atomic,
)
from mutation_engine import SourceGuard

SCHEMA = 1
DEFAULT_EVIDENCE_PATH = ".autonomous/evidence.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _split_cmd(command: str, flag: str) -> List[str]:
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise UsageError(f"cannot parse {flag}: {exc}")
    if not argv:
        raise UsageError(f"{flag} is empty")
    return argv


def _bust_pycache(source: Path) -> None:
    """Drop cached bytecode for a Python source about to be re-run.

    A same-size edit (``==`` -> ``!=``) inside the same second keeps the
    (mtime, size) pair a .pyc validates against, so the interpreter would
    silently execute the unmutated code and the red run would lie.
    """
    cache_dir = source.parent / "__pycache__"
    if cache_dir.is_dir():
        for cached in cache_dir.glob(source.stem + ".*.pyc"):
            cached.unlink(missing_ok=True)


def _run(argv: List[str], cwd: Path, timeout: float) -> Dict[str, Any]:
    """Run one evidence command; record exit, duration and a summary line."""
    start = time.time()
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError:
        raise EnvError(f"required tool not found: {argv[0]}")
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        out, err = proc.communicate()
        code = -9
    lines = [line.strip() for line in ((out or "") + "\n" + (err or "")).splitlines() if line.strip()]
    return {
        "exit": code,
        "timed_out": timed_out,
        "duration_s": round(time.time() - start, 3),
        "summary": (lines[-1][:200] if lines else ""),
        # What actually ran, so the artifact outlives the invocation (issue #14).
        "argv": list(argv),
    }


def _rev_parse(root: Path, ref: str) -> Optional[str]:
    proc = git(["rev-parse", ref], cwd=root, quiet=True)
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return None


def _base_sha(root: Path, base_ref: str) -> str:
    """Resolve the base ref, or refuse: evidence without a pin-able origin
    would make the ancestor check in ``validate_artifact`` trivially true."""
    sha = _rev_parse(root, base_ref)
    if not sha:
        raise UsageError(f"cannot resolve base ref: {base_ref}")
    return sha


def _is_ancestor(root: Path, ancestor: str, descendant: str = "HEAD") -> bool:
    proc = git(["merge-base", "--is-ancestor", ancestor, descendant], cwd=root, quiet=True)
    return proc.returncode == 0


def _evidence_path(root: Path, explicit: Optional[str]) -> Path:
    path = Path(explicit) if explicit else root / DEFAULT_EVIDENCE_PATH
    return path if path.is_absolute() else (root / path)


def _verdict(kind: str, runs: Dict[str, Dict[str, Any]]) -> str:
    if runs["green"]["exit"] != 0 or runs["pass_to_pass"]["exit"] != 0:
        return "invalid"
    if runs["red"]["exit"] == 0:
        return "not-killed" if kind == "mutant" else "not-reproduced"
    return "killed" if kind == "mutant" else "reproduced-and-fixed"


def _write_artifact(root: Path, args: argparse.Namespace, artifact: Dict[str, Any]) -> Path:
    path = _evidence_path(root, args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, artifact)
    return path


def _report(runs: Dict[str, Dict[str, Any]], verdict: str, path: Path) -> int:
    print(f"[*] red:         exit {runs['red']['exit']} — {runs['red']['summary']}")
    print(f"[*] green:       exit {runs['green']['exit']} — {runs['green']['summary']}")
    print(f"[*] pass-to-pass: exit {runs['pass_to_pass']['exit']} — {runs['pass_to_pass']['summary']}")
    print(f"[+] Evidence written to {path} (verdict: {verdict})")
    return EXIT_OK if verdict in ("killed", "reproduced-and-fixed") else EXIT_NEGATIVE


def cmd_mutant(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root, args.config)
    report_path = Path(args.engine_json)
    if not report_path.is_absolute():
        report_path = root / report_path
    if not report_path.exists():
        raise Fatal(f"engine report not found: {report_path}")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Fatal(f"invalid JSON in {report_path}: {exc}")
    survived = report.get("survived") if isinstance(report, dict) else None
    if not isinstance(survived, list) or not (0 <= args.index < len(survived)):
        raise UsageError(
            f"--index {args.index} out of range: report has "
            f"{len(survived) if isinstance(survived, list) else 0} survived mutants"
        )
    entry = survived[args.index]
    source_rel = rel_path(root, str(entry.get("file") or report.get("source") or ""))
    source_path = (root / source_rel).resolve()
    try:
        source_path.relative_to(root.resolve())
    except ValueError:
        raise UsageError(f"mutant source is outside the repository: {source_rel}")
    if not source_path.exists():
        raise Fatal(f"source file not found: {source_rel}")
    if is_dirty(root, source_rel) and not args.allow_dirty:
        raise UsageError(
            f"{source_rel} has uncommitted changes; commit/stash it, "
            "or pass --allow-dirty to evidence the working copy"
        )
    line_no = int(entry.get("line") or 0)
    original = str(entry.get("original") or "")
    mutated = str(entry.get("mutated") or "")
    raw = source_path.read_bytes()
    lines = raw.decode("utf-8").splitlines(keepends=True)
    if not (1 <= line_no <= len(lines)) or lines[line_no - 1].rstrip("\r\n") != original:
        raise UsageError(
            f"mutant no longer applies: {source_rel}:{line_no} does not match "
            "the original text in the engine report (re-run the mutation engine)"
        )

    test_argv = _split_cmd(args.test_cmd, "--test-cmd")
    p2p_argv = _split_cmd(args.pass_to_pass_cmd or args.test_cmd, "--pass-to-pass-cmd")

    runs: Dict[str, Dict[str, Any]] = {}
    _bust_pycache(source_path)
    runs["green"] = _run(test_argv, root, args.timeout)
    runs["pass_to_pass"] = _run(p2p_argv, root, args.timeout)
    ending = "\n" if lines[line_no - 1].endswith("\n") else ""
    with SourceGuard(source_path):
        mutated_lines = list(lines)
        mutated_lines[line_no - 1] = mutated + ending
        source_path.write_text("".join(mutated_lines), encoding="utf-8")
        _bust_pycache(source_path)
        runs["red"] = _run(test_argv, root, args.timeout)

    verdict = _verdict("mutant", runs)
    artifact = {
        "schema": SCHEMA,
        "kind": "mutant",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "base_ref": args.base or cfg.get("base_ref") or "origin/main",
        "base_sha": _base_sha(root, args.base or cfg.get("base_ref") or "origin/main"),
        "head_sha": _rev_parse(root, "HEAD"),
        "test_cmd": args.test_cmd,
        "pass_to_pass_cmd": args.pass_to_pass_cmd or args.test_cmd,
        "mutant": {
            "file": source_rel,
            "line": line_no,
            "original": original,
            "mutated": mutated,
            "description": str(entry.get("description") or ""),
            "source_sha256": sha256_bytes(raw),
        },
        "runs": runs,
        "verdict": verdict,
    }
    # A *generated* mutant is reproducible only through its seed; a hand-named
    # one has none, and the artifact then carries nothing (the spec's own rule:
    # "seed/scope if generated").
    seed = report.get("seed")
    if seed is not None:
        artifact["seed"] = seed
    path = _write_artifact(root, args, artifact)
    return _report(runs, verdict, path)


def cmd_defect(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root, args.config)
    base_ref = args.base or cfg.get("base_ref") or "origin/main"
    base_sha = _rev_parse(root, base_ref)
    if not base_sha:
        raise UsageError(f"cannot resolve base ref: {base_ref}")
    test_files: List[Dict[str, str]] = []
    for raw_path in args.test_file:
        rel = rel_path(root, raw_path)
        path = root / rel
        if not path.exists():
            raise Fatal(f"test file not found: {rel}")
        test_files.append({"path": rel, "sha256": sha256_bytes(path.read_bytes())})

    test_argv = _split_cmd(args.test_cmd, "--test-cmd")
    p2p_argv = _split_cmd(args.pass_to_pass_cmd or args.test_cmd, "--pass-to-pass-cmd")

    runs: Dict[str, Dict[str, Any]] = {}
    runs["green"] = _run(test_argv, root, args.timeout)
    runs["pass_to_pass"] = _run(p2p_argv, root, args.timeout)

    with tempfile.TemporaryDirectory(prefix="evidence-base-") as tmp:
        base_worktree = Path(tmp) / "base"
        proc = git(
            ["worktree", "add", "--detach", str(base_worktree), base_sha],
            cwd=root,
            quiet=True,
        )
        if proc.returncode != 0:
            raise Fatal(f"could not create base worktree at {base_ref}: {proc.stderr.strip()}")
        try:
            for entry in test_files:
                dest = base_worktree / entry["path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / entry["path"], dest)
            runs["red"] = _run(test_argv, base_worktree, args.timeout)
        finally:
            git(["worktree", "remove", "--force", str(base_worktree)], cwd=root, quiet=True)
            git(["worktree", "prune"], cwd=root, quiet=True)

    verdict = _verdict("defect", runs)
    artifact = {
        "schema": SCHEMA,
        "kind": "defect",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "base_ref": base_ref,
        "base_sha": base_sha,
        "head_sha": _rev_parse(root, "HEAD"),
        "test_cmd": args.test_cmd,
        "pass_to_pass_cmd": args.pass_to_pass_cmd or args.test_cmd,
        "test_files": test_files,
        "runs": runs,
        "verdict": verdict,
    }
    path = _write_artifact(root, args, artifact)
    return _report(runs, verdict, path)


def validate_artifact(root: Path, data: Any) -> List[str]:
    """Return the list of reasons an artifact must not be trusted (empty = valid)."""
    problems: List[str] = []
    if not isinstance(data, dict):
        return ["artifact is not a JSON object"]
    if data.get("schema") != SCHEMA:
        problems.append(f"unsupported schema: {data.get('schema')!r}")
    kind = data.get("kind")
    if kind not in ("mutant", "defect"):
        problems.append(f"unknown kind: {kind!r}")
        return problems
    expected = "killed" if kind == "mutant" else "reproduced-and-fixed"
    if data.get("verdict") != expected:
        problems.append(f"verdict is {data.get('verdict')!r}, expected {expected!r}")
    runs = data.get("runs") or {}
    for name in ("red", "green", "pass_to_pass"):
        if not isinstance(runs.get(name), dict) or not isinstance(runs[name].get("exit"), int):
            problems.append(f"missing run record: {name}")
    if problems:
        return problems
    if runs["green"]["exit"] != 0:
        problems.append("green run did not pass on the clean tree")
    if runs["pass_to_pass"]["exit"] != 0:
        problems.append("pass-to-pass run did not pass")
    if runs["red"]["exit"] == 0:
        problems.append("red run passed: the test does not discriminate")
    base_sha = str(data.get("base_sha") or "")
    if not base_sha or not _is_ancestor(root, base_sha):
        problems.append("base_sha is not an ancestor of HEAD (stale base)")
    if kind == "mutant":
        mutant = data.get("mutant") or {}
        source = root / str(mutant.get("file") or "")
        if not source.exists():
            problems.append(f"source file missing: {mutant.get('file')}")
        elif sha256_bytes(source.read_bytes()) != mutant.get("source_sha256"):
            problems.append("source changed since evidence was generated")
    else:
        for entry in data.get("test_files") or []:
            path = root / str(entry.get("path") or "")
            if not path.exists():
                problems.append(f"test file missing: {entry.get('path')}")
            elif sha256_bytes(path.read_bytes()) != entry.get("sha256"):
                problems.append(f"test file changed since evidence was generated: {entry.get('path')}")
    return problems


def validate_evidence_file(root: Path, path: Path) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    if not path.exists():
        return None, [f"evidence artifact not found: {path}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [f"evidence artifact is not valid JSON: {exc}"]
    return data, validate_artifact(root, data)


def cmd_check(args: argparse.Namespace) -> int:
    root = repo_root()
    path = _evidence_path(root, args.evidence)
    data, problems = validate_evidence_file(root, path)
    if problems:
        for problem in problems:
            print(f"[!] evidence invalid: {problem}", file=sys.stderr)
        return EXIT_NEGATIVE
    assert data is not None
    print(
        f"[+] Evidence valid: {data['kind']} · verdict {data['verdict']} · "
        f"base {str(data.get('base_sha'))[:9]}"
    )
    return EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evidence.py",
        description="Produce and validate fail-to-pass evidence artifacts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_mutant = subparsers.add_parser("mutant", help="evidence that a test kills a surviving mutant")
    p_mutant.add_argument("--engine-json", required=True, help="mutation_engine --output-json report")
    p_mutant.add_argument("--index", type=int, default=0, help="index into survived[] (default: 0)")
    p_mutant.add_argument("--test-cmd", required=True, help="command running the new/changed test")
    p_mutant.add_argument("--pass-to-pass-cmd", help="neighboring suite (default: --test-cmd)")
    p_mutant.add_argument("--base", help="base ref to record (default: config base_ref)")
    p_mutant.add_argument("--output", help=f"artifact path (default: {DEFAULT_EVIDENCE_PATH})")
    p_mutant.add_argument("--config", help="path to config.json")
    p_mutant.add_argument("--timeout", type=float, default=120.0, help="per-run timeout seconds")
    p_mutant.add_argument("--allow-dirty", action="store_true", help="evidence a dirty working copy")

    p_defect = subparsers.add_parser("defect", help="evidence that a repro is red on base, green here")
    p_defect.add_argument("--test-file", action="append", required=True, help="repro test file (repeatable)")
    p_defect.add_argument("--test-cmd", required=True, help="command running the repro test")
    p_defect.add_argument("--pass-to-pass-cmd", help="neighboring suite (default: --test-cmd)")
    p_defect.add_argument("--base", help="base ref the repro must fail on (default: config base_ref)")
    p_defect.add_argument("--output", help=f"artifact path (default: {DEFAULT_EVIDENCE_PATH})")
    p_defect.add_argument("--config", help="path to config.json")
    p_defect.add_argument("--timeout", type=float, default=120.0, help="per-run timeout seconds")

    p_check = subparsers.add_parser("check", help="validate an evidence artifact against this tree")
    p_check.add_argument("--evidence", help=f"artifact path (default: {DEFAULT_EVIDENCE_PATH})")
    p_check.add_argument("--config", help="path to config.json")

    args = parser.parse_args(argv)
    try:
        if args.command == "mutant":
            return cmd_mutant(args)
        if args.command == "defect":
            return cmd_defect(args)
        if args.command == "check":
            return cmd_check(args)
        raise UsageError(f"unknown command: {args.command}")
    except (UsageError, EnvError, Fatal) as exc:
        return format_exit(exc)


if __name__ == "__main__":
    sys.exit(main())
