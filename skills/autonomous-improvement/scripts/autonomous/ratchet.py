#!/usr/bin/env python3
"""The ratchet stream (Phase 7): measurable proxies, strict-improvement oracle.

Stream contract instantiation (references/08): the finder is a configured
metric command run on the base ref and at HEAD (deterministic, 0 tokens);
the oracle is mechanical — a diff is accepted only when it stays inside the
ratchet's scope, keeps the targeted tests green, and makes the metric
STRICTLY better (equal is not improving); blast radius is bounded by the
scope globs; the referee is isolated — a diff touching the ratchet's own
config is refused. Wave 1 is T1: ``scaffold-issue`` prints a proposal for a
human; the stream never commits, never merges, never edits a file.

Config (in .autonomous/config.json):

  "ratchets": [
    {"name": "todos",
     "cmd": ["grep", "-c", "TODO", "--", "src"],
     "parse": {"regex": "^(\\\\d+)$", "group": 1},
     "direction": "minimize",
     "scope": ["src/**"],
     "targeted_test_cmd": ["python3", "-m", "unittest", "discover", "-s", "tests", "-t", "."]}
  ]

Usage:
  python3 scripts/autonomous/ratchet.py scan --ratchet <name>
  python3 scripts/autonomous/ratchet.py check --ratchet <name> [--diff <patch>]
  python3 scripts/autonomous/ratchet.py scaffold-issue --ratchet <name>
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import eprint, load_config, repo_root  # noqa: E402

SCHEMA = "ratchet/1"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True, check=False
    )


def _worktree_of(root: Path, ref: str, parent: Path) -> Optional[Path]:
    path = parent / "wt"
    proc = _git(root, "worktree", "add", "--detach", str(path), ref)
    if proc.returncode != 0:
        return None
    return path


def _drop_worktree(root: Path, path: Path) -> None:
    _git(root, "worktree", "remove", "--force", str(path))
    _git(root, "worktree", "prune")


def _metric(cfg: Dict[str, Any], where: Path) -> Tuple[Optional[int], Optional[str]]:
    """Run the metric command in `where`; return (value, error)."""
    try:
        proc = subprocess.run(
            [str(a) for a in cfg["cmd"]],
            cwd=str(where),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"metric command failed: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[:300]
        return None, f"metric command exited {proc.returncode}: {detail}"
    spec = cfg.get("parse") or {}
    pattern = re.compile(str(spec.get("regex") or r"^(\d+)$"))
    group = int(spec.get("group") or 1)
    for line in (proc.stdout or "").splitlines():
        match = pattern.match(line.strip())
        if match:
            try:
                return int(match.group(group)), None
            except (IndexError, ValueError):
                break
    return None, f"metric output did not match {pattern.pattern!r}: {(proc.stdout or '')[:120]!r}"


def _get_ratchet(cfg: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    for ratchet in cfg.get("ratchets") or []:
        if ratchet.get("name") == name:
            return ratchet
    return None


def _measure_pair(
    root: Path, cfg: Dict[str, Any], ratchet: Dict[str, Any]
) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    base_ref = cfg.get("base_ref") or "main"
    with tempfile.TemporaryDirectory(prefix="ratchet-") as tmp:
        base_wt = _worktree_of(root, base_ref, Path(tmp))
        if base_wt is None:
            return None, None, f"cannot create worktree of base ref {base_ref!r}"
        base_value, err = _metric(ratchet, base_wt)
        _drop_worktree(root, base_wt)
        if base_value is None:
            return None, None, f"base: {err}"
    head_value, err = _metric(ratchet, root)
    if head_value is None:
        return None, None, f"head: {err}"
    return base_value, head_value, None


def _improves(direction: str, base: int, head: int) -> bool:
    if direction == "maximize":
        return head > base
    return head < base


def cmd_scan(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root, None)
    ratchet = _get_ratchet(cfg, args.ratchet)
    if ratchet is None:
        eprint(f"[!] no ratchet named {args.ratchet!r} in .autonomous/config.json")
        return 2
    base_value, head_value, err = _measure_pair(root, cfg, ratchet)
    if err:
        eprint(f"[!] {err}")
        return 2
    payload = {
        "schema": SCHEMA,
        "name": args.ratchet,
        "base_value": base_value,
        "head_value": head_value,
        "delta": head_value - base_value,
        "direction": ratchet.get("direction") or "minimize",
        "improving": _improves(
            ratchet.get("direction") or "minimize", base_value, head_value
        ),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _diff_paths(patch_text: str) -> List[str]:
    return [
        line.split(" b/", 1)[1]
        for line in patch_text.splitlines()
        if line.startswith("diff --git a/")
    ]


def cmd_check(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root, None)
    ratchet = _get_ratchet(cfg, args.ratchet)
    if ratchet is None:
        eprint(f"[!] no ratchet named {args.ratchet!r} in .autonomous/config.json")
        return 2
    if args.diff:
        patch_text = Path(args.diff).read_text(encoding="utf-8")
    else:
        patch_text = sys.stdin.read()
    paths = _diff_paths(patch_text)
    if any(p.startswith(".autonomous/") for p in paths):
        eprint(
            "[!] diff touches .autonomous/ (the ratchet's own config): "
            "self-modification is refused"
        )
        return 2
    scope = [str(s) for s in ratchet.get("scope") or []]
    outside = [p for p in paths if not any(fnmatch.fnmatch(p, s) for s in scope)]
    if outside:
        eprint(f"[!] diff touches paths outside the ratchet scope {scope}: {outside}")
        return 2
    if not paths:
        eprint("[!] empty diff: nothing to check")
        return 2

    base_ref = cfg.get("base_ref") or "main"
    patch_file = Path(args.diff) if args.diff else None
    with tempfile.TemporaryDirectory(prefix="ratchet-check-") as tmp:
        holder = Path(tmp) / "change.patch"
        holder.write_text(patch_text, encoding="utf-8")
        base_wt = _worktree_of(root, base_ref, Path(tmp) / "base")
        if base_wt is None:
            eprint(f"[!] cannot create worktree of base ref {base_ref!r}")
            return 2
        try:
            base_value, err = _metric(ratchet, base_wt)
            if base_value is None:
                eprint(f"[!] base: {err}")
                return 2
            apply_wt = _worktree_of(root, "HEAD", Path(tmp) / "apply")
            if apply_wt is None:
                eprint("[!] cannot create worktree of HEAD")
                return 2
            applied = _git(apply_wt.parent.parent, "apply", "--whitespace=nowarn", str(holder)) \
                if False else subprocess.run(
                ["git", "apply", "--whitespace=nowarn", str(holder)],
                cwd=str(apply_wt), capture_output=True, text=True, check=False,
            )
            if applied.returncode != 0:
                eprint(f"[!] patch does not apply: {applied.stderr.strip()[:300]}")
                return 2
            test_cmd = [str(a) for a in ratchet.get("targeted_test_cmd") or []]
            if not test_cmd:
                eprint("[!] ratchet has no targeted_test_cmd")
                return 2
            tests = subprocess.run(
                test_cmd, cwd=str(apply_wt), capture_output=True, text=True,
                timeout=600, check=False,
            )
            if tests.returncode != 0:
                eprint(
                    "[!] tests red under the ratchet change "
                    f"(exit {tests.returncode}); refusing"
                )
                return 1
            new_value, err = _metric(ratchet, apply_wt)
            if new_value is None:
                eprint(f"[!] under change: {err}")
                return 2
        finally:
            _drop_worktree(root, Path(tmp) / "base" / "wt")
            _drop_worktree(root, Path(tmp) / "apply" / "wt")

    direction = ratchet.get("direction") or "minimize"
    if not _improves(direction, base_value, new_value):
        eprint(
            f"[!] metric did not improve ({direction}: {base_value} -> {new_value}); "
            "equal is not improving — refusing"
        )
        return 1
    payload = {
        "schema": SCHEMA,
        "name": args.ratchet,
        "base_value": base_value,
        "new_value": new_value,
        "delta": new_value - base_value,
        "tests": "pass",
        "verdict": "improving",
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_scaffold_issue(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root, None)
    ratchet = _get_ratchet(cfg, args.ratchet)
    if ratchet is None:
        eprint(f"[!] no ratchet named {args.ratchet!r} in .autonomous/config.json")
        return 2
    base_value, head_value, err = _measure_pair(root, cfg, ratchet)
    if err:
        eprint(f"[!] {err}")
        return 2
    direction = ratchet.get("direction") or "minimize"
    print(
        f"## Ratchet proposal: {args.ratchet} ({direction})\n\n"
        f"- base (`{cfg.get('base_ref') or 'main'}`): **{base_value}**\n"
        f"- HEAD: **{head_value}**\n"
        f"- delta: **{head_value - base_value}** "
        f"({'improving' if _improves(direction, base_value, head_value) else 'not improving'})\n\n"
        "Proposal only — a human applies and merges; the ratchet stream never "
        "commits. Verify with:\n\n"
        "```sh\n"
        f"python3 scripts/autonomous/ratchet.py scan --ratchet {args.ratchet}\n"
        f"python3 scripts/autonomous/ratchet.py check --ratchet {args.ratchet} --diff <patch>\n"
        "```\n"
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_name(p):
        p.add_argument("--ratchet", required=True, help="ratchet name from config")
        return p

    add_name(sub.add_parser("scan", help="measure the metric on base ref and HEAD"))
    check = add_name(sub.add_parser("check", help="the oracle: scope, tests, strict improvement"))
    check.add_argument("--diff", default=None, help="patch file (default: stdin)")
    add_name(sub.add_parser("scaffold-issue", help="print the T1 proposal body"))

    args = parser.parse_args(argv)
    if args.cmd == "scan":
        return cmd_scan(args)
    if args.cmd == "check":
        return cmd_check(args)
    return cmd_scaffold_issue(args)


if __name__ == "__main__":
    sys.exit(main())
