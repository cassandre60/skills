#!/usr/bin/env python3
"""Worktree lifecycle and gate orchestration.

Safety properties:
  * never rewrites an ``origin`` remote unless explicitly asked with
    ``--set-origin --yes``;
  * every command is an argv list (no shell);
  * cleanup only touches worktrees registered by this tool, refuses dirty or
    unpushed worktrees, and never force-removes unless ``--force`` is passed;
  * all project commands (setup/format/analyze/gate) come from config.

Usage:
  python3 scripts/autonomous/orchestrator.py create-worktree --slug my-task --type test
  python3 scripts/autonomous/orchestrator.py verify-gate --worktree ../repo-my-task
  python3 scripts/autonomous/orchestrator.py cleanup-worktree --slug my-task
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    EXIT_OK,
    EnvError,
    Fatal,
    UsageError,
    command,
    configured_commands,
    eprint,
    format_exit,
    git,
    git_common_dir,
    load_config,
    main_repo_root,
    repo_root,
    run,
    write_json_atomic,
)

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
TYPE_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


def _slug(value: str) -> str:
    if not SLUG_RE.match(value):
        raise UsageError(
            f"invalid slug {value!r}: use 1-64 chars of [a-z0-9._-], starting alphanumeric"
        )
    return value


def _registry_path(root: Path) -> Path:
    return git_common_dir(root) / "autonomous-worktrees.json"


def _load_registry(root: Path) -> List[Dict[str, Any]]:
    path = _registry_path(root)
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        raise Fatal(f"cannot read worktree registry {path}: {exc}")
    if not isinstance(data, list):
        raise Fatal(f"worktree registry is not a JSON list: {path}")
    return [item for item in data if isinstance(item, dict)]


def _save_registry(root: Path, entries: List[Dict[str, Any]]) -> None:
    write_json_atomic(_registry_path(root), entries)


def _find_entry(entries: List[Dict[str, Any]], slug: str) -> Optional[Dict[str, Any]]:
    for entry in entries:
        if entry.get("slug") == slug:
            return entry
    return None


def _remote_url(root: Path) -> Optional[str]:
    proc = git(["remote", "get-url", "origin"], cwd=root, quiet=True)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _resolve_base(root: Path, cfg: Dict[str, Any], override: Optional[str]) -> Tuple[str, str]:
    """Return (git ref to branch from, branch name) without touching remotes."""
    base = override or cfg.get("base_ref") or "origin/main"
    origin = _remote_url(root)
    if base.startswith("origin/"):
        branch = base[len("origin/") :]
        if origin:
            run(["git", "fetch", "origin", branch], cwd=root, check=True)
            return base, branch
        # No origin: degrade to a local branch if one exists, else HEAD.
        for candidate in (branch, "main", "master"):
            proc = git(["rev-parse", "--verify", f"refs/heads/{candidate}"], cwd=root, quiet=True)
            if proc.returncode == 0:
                eprint(f"[warn] no origin remote; branching from local {candidate}")
                return candidate, candidate
        eprint("[warn] no origin remote and no main/master branch; branching from HEAD")
        return "HEAD", branch
    proc = git(["rev-parse", "--verify", base], cwd=root, quiet=True)
    if proc.returncode != 0:
        raise Fatal(f"base ref does not exist: {base}")
    return base, base


def _maybe_set_origin(root: Path, cfg: Dict[str, Any], enabled: bool, yes: bool) -> None:
    if not enabled:
        return
    target = cfg.get("origin_url")
    if not target:
        raise UsageError("--set-origin requires the 'origin_url' config key")
    current = _remote_url(root)
    print(f"[*] origin: {current or '(none)'} -> {target}")
    if not yes:
        raise UsageError("refusing to rewrite origin without --yes")
    if current is None:
        run(["git", "remote", "add", "origin", str(target)], cwd=root, check=True)
    else:
        run(["git", "config", "remote.origin.url", str(target)], cwd=root, check=True)


def cmd_create_worktree(args: argparse.Namespace) -> int:
    root = main_repo_root()
    cfg = load_config(root, args.config)
    slug = _slug(args.slug)
    branch_type = args.type
    if not TYPE_RE.match(branch_type):
        raise UsageError(f"invalid branch type {branch_type!r}")

    worktree_path = root.parent / f"{root.name}-{slug}"
    branch = f"{branch_type}/{slug}"

    if worktree_path.exists():
        raise Fatal(f"worktree path already exists: {worktree_path}")
    proc = git(["rev-parse", "--verify", f"refs/heads/{branch}"], cwd=root, quiet=True)
    if proc.returncode == 0:
        raise Fatal(f"branch already exists: {branch}")

    _maybe_set_origin(root, cfg, args.set_origin, args.yes)
    base_ref, base_branch = _resolve_base(root, cfg, args.base)
    base_sha = git(["rev-parse", base_ref], cwd=root, check=True, quiet=True).stdout.strip()

    print(f"[*] Creating {worktree_path} on {branch} from {base_ref} ({base_sha[:12]})")
    run(["git", "worktree", "add", str(worktree_path), "-b", branch, base_ref], cwd=root, check=True)

    entries = _load_registry(root)
    entries = [entry for entry in entries if entry.get("slug") != slug]
    entries.append(
        {
            "slug": slug,
            "path": str(worktree_path),
            "branch": branch,
            "base_ref": base_ref,
            "base_sha": base_sha,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
    )
    _save_registry(root, entries)
    print(f"[+] Registered worktree in {_registry_path(root)}")

    setup = configured_commands(cfg.get("setup_cmds"), "setup_cmds")
    if not setup:
        print("[skip] setup_cmds not configured")
    for argv in setup:
        run(argv, cwd=worktree_path, check=True)
    print(f"[+] Worktree ready at {worktree_path}")
    return EXIT_OK


def _base_for_diff(root: Path, cfg: Dict[str, Any]) -> str:
    configured = cfg.get("base_ref") or "origin/main"
    for candidate in (configured, "main", "master", "HEAD"):
        proc = git(["rev-parse", "--verify", candidate], cwd=root, quiet=True)
        if proc.returncode == 0:
            if candidate != configured:
                eprint(f"[warn] {configured} not found; using {candidate} as diff base")
            return candidate
    return "HEAD"


def _zsplit(output: str) -> List[str]:
    return [part for part in output.split("\0") if part]


def _changed_files(root: Path, base: str) -> List[str]:
    committed = git(
        ["diff", "--name-only", "--diff-filter=ACMR", "-z", f"{base}...HEAD"],
        cwd=root,
        quiet=True,
    )
    uncommitted = git(
        ["diff", "--name-only", "--diff-filter=ACMR", "-z", "HEAD"], cwd=root, quiet=True
    )
    return sorted(set(_zsplit(committed.stdout) + _zsplit(uncommitted.stdout)))


def _sidecars(root: Path) -> List[Path]:
    tracked = git(["ls-files", "-z"], cwd=root, quiet=True)
    untracked = git(["ls-files", "-o", "-z"], cwd=root, quiet=True)
    paths = _zsplit(tracked.stdout) + _zsplit(untracked.stdout)
    return [
        root / path
        for path in paths
        if path.endswith(".mutbak") or path.endswith(".mutlock")
    ]


def _run_step(name: str, argv: Optional[List[str]], cwd: Path) -> None:
    if not argv:
        print(f"[skip] {name} not configured")
        return
    proc = run(argv, cwd=cwd)
    if proc.returncode != 0:
        raise Fatal(
            f"{name} failed (exit {proc.returncode}): {' '.join(argv)}\n"
            f"{(proc.stderr or proc.stdout).strip()}"
        )


def _record_weakening_hits(root: Path, findings: List[Dict[str, Any]], mode: str) -> None:
    """Persist the verify-gate's weakening signals into the evidence artifact.

    The gate's verdict must outlive the gate (issue #14): the print evaporates
    with the invocation, and the fail-mode exception is visible only to
    whoever reruns it. The artifact is the durable place. No artifact, no
    record — the warning says so instead of fabricating a schema-less file
    that the evidence gate itself would then reject.
    """
    artifact_path = root / ".autonomous" / "evidence.json"
    if not artifact_path.exists():
        print("[warn] weakening hits not recorded: no evidence artifact yet")
        return
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print("[warn] weakening hits not recorded: artifact is not valid JSON")
        return
    if not isinstance(artifact, dict):
        print("[warn] weakening hits not recorded: artifact is not an object")
        return
    gate = artifact.get("verify_gate")
    if not isinstance(gate, dict):
        gate = {}
    gate["weakening_hits"] = [
        {
            "kind": finding.get("kind"),
            "file": finding.get("file"),
            "line": finding.get("line"),
            "text": finding.get("text"),
        }
        for finding in findings
    ]
    gate["weakening_mode"] = mode
    artifact["verify_gate"] = gate
    write_json_atomic(artifact_path, artifact)
    print(f"[*] weakening hits recorded into {artifact_path}")


def cmd_verify_gate(args: argparse.Namespace) -> int:
    worktree = Path(args.worktree).resolve()
    if not worktree.exists():
        raise Fatal(f"worktree does not exist: {worktree}")
    root = repo_root(worktree)
    cfg = load_config(root, args.config)

    sidecars = _sidecars(root)
    if sidecars:
        listing = "\n  ".join(str(path) for path in sidecars)
        raise Fatal(
            "found mutation sidecar files; a previous mutation run was interrupted:\n  "
            + listing
            + "\nrun the mutation engine again to recover, or remove them after restoring the source"
        )

    base = _base_for_diff(root, cfg)
    touched = _changed_files(root, base)
    extensions = [str(ext) for ext in (cfg.get("analyze_extensions") or [])]
    if extensions:
        analyzable = [path for path in touched if any(path.endswith(ext) for ext in extensions)]
    else:
        analyzable = touched

    print(f"[*] Diff base: {base}")
    print(f"[*] Touched files: {len(touched)} ({len(analyzable)} analyzable)")

    _run_step("format check", command(cfg.get("format_check_cmd"), "format_check_cmd"), worktree)

    analyze = command(cfg.get("analyze_cmd"), "analyze_cmd")
    if not analyze:
        print("[skip] analyze not configured")
    elif not analyzable:
        print("[skip] analyze: no changed files matching analyze_extensions")
    else:
        _run_step("analyze", analyze + analyzable, worktree)

    _run_step("gate", command(cfg.get("gate_cmd"), "gate_cmd"), worktree)

    # The referee checks (issue #14): a format-clean diff that weakens tests
    # or rides on stale evidence must not pass just because analyze is happy.
    vg = cfg.get("verify_gate") or {}
    weakening_mode = str(vg.get("weakening", "fail"))
    if weakening_mode != "off":
        import test_weakening_check as twc

        findings = twc.check(root, base, twc._test_globs(cfg))
        if findings:
            _record_weakening_hits(root, findings, weakening_mode)
            listing = "\n  ".join(
                f"{f['kind']}: {f['file']}" + (f" — {f.get('text')}" if f.get("text") else "")
                for f in findings
            )
            if weakening_mode == "warn":
                print(f"[warn] test-weakening signals against {base}:\n  {listing}")
            else:
                raise Fatal(
                    f"test-weakening signals against {base} "
                    "(set verify_gate.weakening to warn/off to downgrade):\n  " + listing
                )
        else:
            print(f"[*] weakening check: clean against {base}")

    evidence_mode = str(vg.get("evidence", "warn"))
    if evidence_mode != "off":
        from evidence import validate_evidence_file

        evidence_path = root / ".autonomous" / "evidence.json"
        data, problems = validate_evidence_file(root, evidence_path)
        if data is None and evidence_mode != "fail":
            print(
                "[warn] no evidence artifact (.autonomous/evidence.json); "
                "generate one with evidence.py before claiming the contract"
            )
        elif problems:
            # A present-but-invalid artifact always fails: stale evidence is
            # worse than none, because it looks like proof.
            raise Fatal("evidence artifact invalid:\n  " + "\n  ".join(problems))
        else:
            print(f"[*] evidence: valid ({data['kind']}, verdict {data['verdict']})")

    print("[+] Local gates passed cleanly.")
    return EXIT_OK


def _unpushed(root: Path, cfg: Dict[str, Any]) -> List[str]:
    upstream = git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=root, quiet=True
    )
    if upstream.returncode == 0 and upstream.stdout.strip():
        count = git(
            ["rev-list", "--count", f"{upstream.stdout.strip()}..HEAD"], cwd=root, quiet=True
        )
        try:
            number = int(count.stdout.strip() or "0")
        except ValueError:
            number = 0
        if number > 0:
            log = git(["log", "--oneline", f"{upstream.stdout.strip()}..HEAD"], cwd=root, quiet=True)
            return [line for line in log.stdout.splitlines() if line.strip()]
        return []

    base = cfg.get("base_ref") or "origin/main"
    resolved = git(["rev-parse", "--verify", base], cwd=root, quiet=True).returncode == 0
    if not resolved:
        for candidate in ("origin/main", "origin/master", "main", "master"):
            if git(["rev-parse", "--verify", candidate], cwd=root, quiet=True).returncode == 0:
                base = candidate
                resolved = True
                break
    if not resolved:
        return ["cannot verify push status: no upstream or base ref found"]
    ancestor = git(["merge-base", "--is-ancestor", "HEAD", base], cwd=root, quiet=True)
    if ancestor.returncode == 0:
        return []
    log = git(["log", "--oneline", "HEAD", "--not", base], cwd=root, quiet=True)
    return [line for line in log.stdout.splitlines() if line.strip()]


def cmd_cleanup_worktree(args: argparse.Namespace) -> int:
    root = main_repo_root()
    cfg = load_config(root, args.config)
    slug = _slug(args.slug)
    entries = _load_registry(root)
    entry = _find_entry(entries, slug)
    if entry is None:
        raise Fatal(
            f"worktree {slug!r} was not created by this tool (not in "
            f"{_registry_path(root)}); refusing to remove it"
        )

    path = Path(str(entry.get("path", ""))).resolve()
    branch = str(entry.get("branch") or "")
    if not path.exists():
        print(f"[-] Worktree {path} no longer exists; pruning registry entry.")
        git(["worktree", "prune"], cwd=root, quiet=True)
        _save_registry(root, [item for item in entries if item.get("slug") != slug])
        return EXIT_OK

    status = git(["status", "--porcelain", "-z"], cwd=path, quiet=True)
    dirty = [part for part in status.stdout.split("\0") if part.strip()]
    unpushed = _unpushed(path, cfg)

    if (dirty or unpushed) and not args.force:
        lines = [f"refusing to remove {path}"]
        if dirty:
            lines.append("uncommitted changes:")
            lines.extend("  " + item for item in dirty[:20])
        if unpushed:
            lines.append("unpushed commits:")
            lines.extend("  " + item for item in unpushed[:20])
        lines.append("pass --force to discard them (this prints what it discards)")
        raise Fatal("\n".join(lines))

    if args.force:
        if dirty:
            print(f"[!] --force discards {len(dirty)} uncommitted change(s) in {path}")
        if unpushed:
            print(f"[!] --force discards {len(unpushed)} unpushed commit(s):")
            for line in unpushed:
                print(f"    {line}")
        run(["git", "worktree", "remove", "--force", str(path)], cwd=root, check=True)
    else:
        print(f"[*] Removing clean worktree {path}")
        run(["git", "worktree", "remove", str(path)], cwd=root, check=True)

    git(["worktree", "prune"], cwd=root, quiet=True)
    _save_registry(root, [item for item in entries if item.get("slug") != slug])
    print(f"[+] Removed {path}")

    if args.delete_branch:
        if not branch:
            print("[warn] registry entry has no branch; not deleting a branch")
        else:
            proc = git(["branch", "-D", branch], cwd=root)
            if proc.returncode != 0:
                eprint(f"[warn] could not delete branch {branch}: {proc.stderr.strip()}")
            else:
                print(f"[+] Deleted branch {branch}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Worktree and gate orchestrator (config-driven)")
    common_parent = argparse.ArgumentParser(add_help=False)
    common_parent.add_argument(
        "--config", help="path to config.json (default: scripts/autonomous/config.json)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser(
        "create-worktree", parents=[common_parent], help="create an isolated worktree"
    )
    create_parser.add_argument("--slug", required=True, help="task slug")
    create_parser.add_argument("--type", default="fix", help="branch type, e.g. fix/test/chore")
    create_parser.add_argument("--base", help="base ref (default: config base_ref)")
    create_parser.add_argument(
        "--set-origin",
        action="store_true",
        help="opt in to rewriting origin to config origin_url",
    )
    create_parser.add_argument("--yes", action="store_true", help="confirm --set-origin")
    create_parser.set_defaults(func=cmd_create_worktree)

    verify_parser = subparsers.add_parser(
        "verify-gate", parents=[common_parent], help="run configured gates on touched files"
    )
    verify_parser.add_argument("--worktree", required=True, help="path to the worktree")
    verify_parser.set_defaults(func=cmd_verify_gate)

    cleanup_parser = subparsers.add_parser(
        "cleanup-worktree", parents=[common_parent], help="remove a registered worktree"
    )
    cleanup_parser.add_argument("--slug", required=True, help="task slug")
    cleanup_parser.add_argument("--force", action="store_true", help="discard dirty/unpushed work")
    cleanup_parser.add_argument("--delete-branch", action="store_true", help="delete the branch")
    cleanup_parser.set_defaults(func=cmd_cleanup_worktree)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        return format_exit(exc)


if __name__ == "__main__":
    sys.exit(main())
