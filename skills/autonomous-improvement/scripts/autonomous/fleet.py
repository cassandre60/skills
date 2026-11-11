#!/usr/bin/env python3
"""Fleet state accumulator, PR state classifier, and governor.

Deterministic state spine for autonomous-improvement runs:
  * snapshot: records main SHA, open PR statuses, and worktree dirty counts to disk (0 tokens);
  * pr-state: emits references/04 state line per PR with head-sha-vs-run-sha verification;
  * govern: checks kill-switch, WIP PR caps, and acceptance rate floors before dispatch;
  * doctor: read-only blast-radius preflight (auth/scopes, protection, linger, timers, disk, worktrees);
  * stats: computes acceptance rates, CI iterations, and renders weekly owner digest.

Usage:
  python3 scripts/autonomous/fleet.py snapshot [--state-dir <dir>]
  python3 scripts/autonomous/fleet.py pr-state [--json] [--pr <num>]
  python3 scripts/autonomous/fleet.py govern [--json]
  python3 scripts/autonomous/fleet.py doctor [--json]
  python3 scripts/autonomous/fleet.py stats [--json] [--days <N>]
  python3 scripts/autonomous/fleet.py needs-brain [--json]
  python3 scripts/autonomous/fleet.py wake [--state-dir <dir>] [--harness-cmd <argv>]
  python3 scripts/autonomous/fleet.py doctor [--json] [--state-dir <dir>]
  python3 scripts/autonomous/fleet.py claim --target <path> --stream A [--json]
  python3 scripts/autonomous/fleet.py publish --pr <num> [--title <title>]
  python3 scripts/autonomous/fleet.py publish --issues-from <engine.json> [--json]
"""

from __future__ import annotations

import argparse
import datetime
import email.utils
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    EXIT_NEGATIVE,
    EXIT_OK,
    EXIT_USAGE,
    EnvError,
    Fatal,
    UsageError,
    config_path,
    eprint,
    format_exit,
    git,
    hash_at_head,
    load_config,
    load_worktree_registry,
    main_repo_root,
    normalize_stream,
    path_at_head,
    rel_path,
    repo_root,
    run,
    state_dir_candidates,
    stream_label,
    test_for_source,
    write_json_atomic,
)


from fleet_cockpit import (
    _ai_policy_hit,
    _referee_incidents,
    _render_evidence_body,
    active_directive_reasons,
    cmd_directive,
    cmd_work_gate,
    cmd_worker,
    format_directives_block,
    register_cockpit_subparsers,
    run_dashboard_command,
)
from fleet_doctor import (
    _doctor_check,
    _doctor_run,
    _journal_age,
    _origin_repo_slug,
    _resolve_min_free_gb,
    _resolve_state_dir,
    cmd_doctor,
    doctor_summary,
    run_doctor_checks,
)
from fleet_pool import (
    _keep_matches,
    _opencode_model_ids,
    _resolve_model_pool,
    cmd_pool,
    deep_standby_suppressed,
    evaluate_track_caps,
    saturation_info,
)


def fetch_gh_prs(
    root: Path,
    state: str = "open",
    limit: int = 100,
    require_gh: bool = False,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Fetch PRs from gh CLI with fallback for older versions and stubs."""
    # No `commits`: it drags in the authors connection, and on a repo with a
    # few hundred PRs the query exceeds GraphQL's node limit ("requesting up to
    # 1,000,000 possible nodes ... exceeds the maximum limit of 500,000").
    # The failure is silent - the caller falls back to a field set without
    # mergedAt, and then every PR looks unmerged: acceptance_rate reads 0.0.
    fields_full = (
        "number,title,headRefName,headRefOid,statusCheckRollup,"
        "labels,createdAt,updatedAt,mergedAt,closedAt,body"
    )
    argv = [
        "gh",
        "pr",
        "list",
        "--state",
        state,
        "--json",
        fields_full,
        "--limit",
        str(limit),
    ]
    try:
        proc = run(argv, cwd=root, quiet=True)
    except Exception as exc:
        if require_gh:
            raise EnvError(f"gh not available: {exc}")
        return [], f"gh not available ({exc}); skipping GitHub query"

    if proc.returncode != 0:
        # Retry with minimal fields for older gh versions
        fallback_argv = [
            "gh",
            "pr",
            "list",
            "--state",
            state,
            "--json",
            "number,title,headRefName,headRefOid,statusCheckRollup,labels,createdAt",
            "--limit",
            str(limit),
        ]
        proc = run(fallback_argv, cwd=root, quiet=True)
        if proc.returncode != 0:
            minimal_argv = [
                "gh",
                "pr",
                "list",
                "--state",
                state,
                "--json",
                "number,title,headRefName",
                "--limit",
                str(limit),
            ]
            proc = run(minimal_argv, cwd=root, quiet=True)
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout).strip().splitlines()
                msg = detail[0] if detail else f"exit {proc.returncode}"
                if require_gh:
                    raise EnvError(f"gh pr list failed: {msg}")
                return [], f"gh unavailable ({msg}); skipping GitHub query"

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        if require_gh:
            raise EnvError("gh pr list returned invalid JSON")
        return [], "gh returned invalid JSON"

    if not isinstance(data, list):
        if require_gh:
            raise EnvError("gh pr list returned an unexpected payload (not a list)")
        return [], "gh returned an unexpected payload"

    return [item for item in data if isinstance(item, dict)], None


def fetch_gh_issues(
    root: Path,
    labels: Tuple[str, ...] = ("autonomous-improvement", "owner-ask"),
    limit: int = 50,
    require_gh: bool = False,
) -> List[Dict[str, Any]]:
    """Fetch open issues carrying target labels."""
    argv = [
        "gh",
        "issue",
        "list",
        "--state",
        "open",
        "--json",
        "number,title,labels,updatedAt",
        "--limit",
        str(limit),
    ]
    try:
        proc = run(argv, cwd=root, quiet=True)
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout)
            if isinstance(data, list):
                matched = []
                target_labels = [l.lower() for l in labels]
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    item_labels = [
                        (str(l.get("name") or "") if isinstance(l, dict) else str(l)).lower()
                        for l in item.get("labels", [])
                    ]
                    if any(tl in item_labels for tl in target_labels):
                        matched.append(item)
                return matched
    except Exception as exc:
        if require_gh:
            raise EnvError(f"gh not available: {exc}")
    return []


def get_main_sha(root: Path) -> str:
    """Resolve current main/HEAD SHA without network stall."""
    try:
        proc = run(["git", "rev-parse", "HEAD"], cwd=root, quiet=True)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass
    return ""


ORCH_MODEL_COOLDOWN_DEFAULT = 3600  # 1 hour
ORCH_RETRY_AFTER_EXHAUSTION_DEFAULT = 1800  # 30 min
ORCH_FAILURE_TTL_DEFAULT = 7 * 86400  # 7 days

OUTAGE_PATTERN = re.compile(
    r"503|Endpoint is unavailable|upstream request failed|connection reset|overloaded",
    re.IGNORECASE,
)
QUOTA_PATTERN = re.compile(
    r"rate limit|rate_limit|429|too many requests|insufficient balance|balance is at|exceeded your current|usage limit|subscribe to opencode",
    re.IGNORECASE,
)
DEAD_MODEL_PATTERN = re.compile(
    r"Model unavailable|no such model|unknown model|model disabled",
    re.IGNORECASE,
)


def _model_failures_path(state_dir: Path) -> Path:
    return state_dir / "model_failures"


def _load_cooling_models(
    state_dir: Path, cooldown_sec: int = ORCH_MODEL_COOLDOWN_DEFAULT
) -> set[str]:
    fail_file = _model_failures_path(state_dir)
    cooling: set[str] = set()
    if not fail_file.is_file():
        return cooling
    now = time.time()
    try:
        lines = fail_file.read_text(encoding="utf-8").splitlines()
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 2:
                model, ts_str = parts[0], parts[1]
                try:
                    ts = float(ts_str)
                    if now - ts < cooldown_sec:
                        cooling.add(model)
                except ValueError:
                    continue
    except Exception:
        pass
    return cooling


def _record_model_failure(state_dir: Path, model: str, reason: str = "") -> None:
    if not model:
        return
    fail_file = _model_failures_path(state_dir)
    now = int(time.time())
    try:
        entries = []
        if fail_file.is_file():
            for line in fail_file.read_text(encoding="utf-8").splitlines():
                parts = line.strip().split()
                if len(parts) >= 2:
                    try:
                        ts = float(parts[1])
                        if now - ts < ORCH_FAILURE_TTL_DEFAULT:
                            entries.append(f"{parts[0]} {int(ts)}")
                    except ValueError:
                        continue
        entries.append(f"{model} {now}")
        state_dir.mkdir(parents=True, exist_ok=True)
        fail_file.write_text("\n".join(entries) + "\n", encoding="utf-8")
    except Exception as exc:
        eprint(f"[warn] failed to record model failure: {exc}")


def _circuit_breaker_path(state_dir: Path) -> Path:
    return state_dir / "CIRCUIT_BREAKER"


def _check_consecutive_failed_wakes(state_dir: Path) -> bool:
    wakes_file = state_dir / "wakes.jsonl"
    if not wakes_file.is_file():
        return False
    records = []
    try:
        for line in wakes_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    except Exception:
        return False

    turn_records = [
        r
        for r in records
        if r.get("action") not in ("quiet", "cooling", "no-gap", "prompt-only")
    ]
    if len(turn_records) < 3:
        return False

    last_three = turn_records[-3:]
    all_failed = all(r.get("action") == "launch-failed" for r in last_three)
    if all_failed:
        cb_path = _circuit_breaker_path(state_dir)
        cb_path.write_text(
            json.dumps(
                {
                    "schema": "circuit-breaker/1",
                    "tripped_at": datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat(),
                    "reason": "3 consecutive failed wakes (launch-failed)",
                    "last_records": last_three,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return True
    return False


def _auto_clear_circuit_breaker(root: Path, cb_path: Path) -> bool:
    if not cb_path.is_file():
        return False
    try:
        data = json.loads(cb_path.read_text(encoding="utf-8"))
        tripped_str = data.get("tripped_at")
        if tripped_str:
            tripped_dt = datetime.datetime.fromisoformat(tripped_str)
            proc = run(["git", "log", "-1", "--format=%cI"], cwd=root, quiet=True)
            if proc.returncode == 0 and proc.stdout.strip():
                commit_dt = datetime.datetime.fromisoformat(proc.stdout.strip())
                if commit_dt > tripped_dt:
                    cb_path.unlink(missing_ok=True)
                    return True
    except Exception:
        pass
    return False


def cmd_reset_circuit(args: argparse.Namespace) -> int:
    root = main_repo_root()
    state_dir = _resolve_state_dir(root, getattr(args, "state_dir", None))
    cb_path = _circuit_breaker_path(state_dir)
    cleared = False
    if cb_path.is_file():
        cb_path.unlink()
        cleared = True
    payload = {
        "tool": "fleet.py reset-circuit",
        "cleared": cleared,
        "circuit_breaker": str(cb_path),
        "status": "ready" if cleared else "was-not-tripped",
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        if cleared:
            print("[+] Circuit breaker reset: dispatch re-armed")
        else:
            print("[*] Circuit breaker was not tripped")
    return EXIT_OK


def _rollup_item_sha(item: Dict[str, Any]) -> Optional[str]:
    oid = item.get("oid") or item.get("commit_sha") or item.get("headSha")
    if not oid and isinstance(item.get("commit"), dict):
        oid = item["commit"].get("oid")
    return str(oid) if oid else None


def _rollup_sort_key(index: int, item: Dict[str, Any]) -> Tuple[str, int, int]:
    """Order two runs of one context: newest first, and a verdict over a cancellation."""
    stamp = str(
        item.get("completedAt")
        or item.get("startedAt")
        or item.get("createdAt")
        or item.get("updatedAt")
        or ""
    )
    cancelled = str(item.get("conclusion") or "").upper() == "CANCELLED"
    return (stamp, 0 if cancelled else 1, index)


def latest_per_context(
    rollup: List[Dict[str, Any]], head_sha: str
) -> List[Dict[str, Any]]:
    """Collapse re-runs to one entry per check context for the PR head.

    ``gh`` keeps a superseded run in the rollup after a re-run, so judging every
    entry lets a CANCELLED attempt override the verdict that replaced it (the
    fleet called green PRs red on #376 and #398). Entries that name another
    commit are dropped: they are another commit's verdict.
    """
    chosen: Dict[str, Tuple[Tuple[str, int, int], Dict[str, Any]]] = {}
    anonymous: List[Dict[str, Any]] = []
    for index, item in enumerate(rollup or []):
        if not isinstance(item, dict):
            continue
        oid = _rollup_item_sha(item)
        if oid and head_sha and oid != head_sha:
            continue
        context = str(item.get("name") or item.get("context") or "").strip()
        if not context:
            anonymous.append(item)
            continue
        key = _rollup_sort_key(index, item)
        if context not in chosen or key > chosen[context][0]:
            chosen[context] = (key, item)
    return [entry[1] for entry in chosen.values()] + anonymous


def evaluate_pr_checks(pr: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate checks and enforce: 'green for an older sha is another commit's verdict'."""
    head_sha = str(pr.get("headRefOid") or pr.get("head_sha") or "")
    if not head_sha:
        commits = pr.get("commits")
        if isinstance(commits, list) and commits:
            last = commits[-1]
            if isinstance(last, dict):
                head_sha = str(last.get("oid") or "")
    if not head_sha and pr.get("headRefName"):
        head_sha = str(pr.get("headRefName"))

    explicit_checks_sha = pr.get("checks_sha")
    rollup = pr.get("statusCheckRollup") or []

    checks_sha = None
    if explicit_checks_sha is not None:
        checks_sha = str(explicit_checks_sha)
    elif rollup:
        for item in rollup:
            if isinstance(item, dict):
                oid = item.get("oid") or item.get("commit_sha") or item.get("headSha")
                if not oid and isinstance(item.get("commit"), dict):
                    oid = item["commit"].get("oid")
                if oid:
                    checks_sha = str(oid)
                    break
        if checks_sha is None:
            checks_sha = head_sha

    checks = "none"
    red_kind = None

    entries = latest_per_context(rollup, head_sha)
    if entries:
        has_failure = False
        has_pending = False
        failing_checks: List[Dict[str, Any]] = []

        for item in entries:
            status = str(item.get("status") or "").upper()
            conclusion = str(item.get("conclusion") or "").upper()
            state = str(item.get("state") or "").lower()

            if (
                conclusion in ("FAILURE", "TIMED_OUT", "ACTION_REQUIRED")
                or state in ("fail", "error", "failure")
            ):
                has_failure = True
                failing_checks.append(item)
            elif (
                status in ("IN_PROGRESS", "QUEUED", "PENDING")
                or state == "pending"
                or conclusion in ("STALE", "CANCELLED")
            ):
                has_pending = True

        if has_failure:
            checks = "red"
            red_kind = "unknown"
            for item in failing_checks:
                name = str(item.get("name") or "").lower()
                desc = str(item.get("description") or "").lower()
                wf = str(item.get("workflowName") or "").lower()
                text = f"{name} {desc} {wf}"
                conc = str(item.get("conclusion") or "").upper()

                if "referee" in text or "protected" in text or "gate" in text:
                    red_kind = "referee"
                    break
                if conc == "TIMED_OUT" or "infra" in text or "runner" in text:
                    red_kind = "infra"
                    break
                if any(k in text for k in ("test", "check", "lint", "analyze", "format", "unit")):
                    red_kind = "in-diff"
                    break
        elif has_pending:
            checks = "pending"
        else:
            checks = "green"
    elif rollup:
        # Every run belongs to another commit: nothing judges this head yet.
        checks = "pending"

    # Law: "green for an older sha is another commit's verdict"
    if checks_sha and head_sha and checks_sha != head_sha:
        checks = "pending"
        red_kind = None

    iterations = 1
    commits_list = pr.get("commits")
    if isinstance(commits_list, list) and commits_list:
        iterations = max(1, len(commits_list))
    elif "iterations" in pr:
        try:
            iterations = max(1, int(pr["iterations"]))
        except (ValueError, TypeError):
            iterations = 1

    owner_ask_pending = False
    if pr.get("owner_ask_pending") is True:
        owner_ask_pending = True
    else:
        labels = pr.get("labels") or []
        label_names = []
        for label in labels:
            if isinstance(label, dict):
                label_names.append(str(label.get("name", "")).lower())
            elif isinstance(label, str):
                label_names.append(label.lower())
        if any(
            term in name
            for name in label_names
            for term in ("owner", "blocked", "question", "needs-decision", "review")
        ):
            owner_ask_pending = True
        if pr.get("reviewDecision") == "CHANGES_REQUESTED":
            owner_ask_pending = True

    return {
        "pr": pr.get("number"),
        "title": pr.get("title", ""),
        "head_sha": head_sha,
        "checks": checks,
        "checks_sha": checks_sha,
        "red_kind": red_kind,
        "iterations": iterations,
        "owner_ask_pending": owner_ask_pending,
        "updated_at": pr.get("updatedAt") or "",
    }


def cmd_pr_state(args: argparse.Namespace) -> int:
    root = main_repo_root()
    prs_data, gh_warn = fetch_gh_prs(root, state="open", require_gh=args.require_gh)
    if gh_warn:
        eprint(f"[warn] {gh_warn}")

    if args.pr is not None:
        prs_data = [p for p in prs_data if p.get("number") == args.pr]
        if not prs_data and args.require_gh:
            raise Fatal(f"PR #{args.pr} not found in open PRs")

    states = [evaluate_pr_checks(p) for p in prs_data]

    if args.json:
        if args.pr is not None and len(states) == 1:
            print(json.dumps(states[0], indent=2))
        else:
            print(json.dumps(states, indent=2))
        return EXIT_OK

    if not states:
        print("[*] No open PRs found.")
        return EXIT_OK

    for s in states:
        head_disp = (s["head_sha"] or "unknown")[:9]
        checks_disp = s["checks"]
        if s["checks_sha"] and s["checks_sha"] != s["head_sha"]:
            checks_disp += f" (sha: {s['checks_sha'][:9]} != head)"
        red_part = f" [{s['red_kind']}]" if s["red_kind"] else ""
        owner_part = "yes" if s["owner_ask_pending"] else "no"
        print(
            f"PR #{s['pr']} | head: {head_disp} | checks: {checks_disp}{red_part} | "
            f"owner-ask: {owner_part} | iterations: {s['iterations']}"
        )
    return EXIT_OK


def cmd_snapshot(args: argparse.Namespace) -> int:
    root = main_repo_root()
    cfg = load_config(root, args.config)

    base_ref = cfg.get("base_ref") or "origin/main"
    main_sha = None
    for candidate in (base_ref, "main", "master", "HEAD"):
        proc = git(["rev-parse", candidate], cwd=root, quiet=True)
        if proc.returncode == 0 and proc.stdout.strip():
            main_sha = proc.stdout.strip()
            break
    if not main_sha:
        main_sha = "unknown"

    prs_data, gh_warn = fetch_gh_prs(root, state="open", require_gh=args.require_gh)
    if gh_warn:
        eprint(f"[warn] {gh_warn}")
    open_prs = [evaluate_pr_checks(p) for p in prs_data]

    registry = load_worktree_registry(root)
    worktree_states = []
    for entry in registry:
        w_path = Path(entry.get("path", ""))
        w_info = dict(entry)
        if w_path.exists():
            proc_head = git(["rev-parse", "HEAD"], cwd=w_path, quiet=True)
            w_info["head_sha"] = proc_head.stdout.strip() if proc_head.returncode == 0 else None

            base = entry.get("base_sha") or entry.get("base_ref") or "HEAD"
            proc_count = git(["rev-list", "--count", f"{base}..HEAD"], cwd=w_path, quiet=True)
            try:
                w_info["own_commits"] = (
                    int(proc_count.stdout.strip()) if proc_count.returncode == 0 else 0
                )
            except ValueError:
                w_info["own_commits"] = 0

            proc_status = git(["status", "--porcelain"], cwd=w_path, quiet=True)
            dirty_lines = [l for l in proc_status.stdout.splitlines() if l.strip()]
            w_info["dirty_count"] = len(dirty_lines)
            w_info["is_dirty"] = len(dirty_lines) > 0
        else:
            w_info["head_sha"] = None
            w_info["own_commits"] = 0
            w_info["dirty_count"] = 0
            w_info["is_dirty"] = False
        worktree_states.append(w_info)

    snapshot_data = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "main_sha": main_sha,
        "open_prs": open_prs,
        "worktrees": worktree_states,
        "model_probes": {},
    }

    state_dir = _resolve_state_dir(root, args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    snapshot_json_path = state_dir / "snapshot.json"
    write_json_atomic(snapshot_json_path, snapshot_data)

    with open(state_dir / "snapshots.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot_data) + "\n")

    if args.output:
        out_path = Path(args.output)
        if not out_path.is_absolute():
            out_path = (root / out_path).resolve()
        write_json_atomic(out_path, snapshot_data)

    if not args.quiet:
        print(f"[+] Snapshot recorded in {snapshot_json_path}")
        print(
            f"    main: {main_sha[:9]} · open PRs: {len(open_prs)} · "
            f"registered worktrees: {len(worktree_states)}"
        )
    return EXIT_OK


def _block_govern(
    args: argparse.Namespace,
    reason: str,
    open_prs: int = 0,
    tend: Optional[List[int]] = None,
    **extra: Any,
) -> int:
    payload: Dict[str, Any] = {
        "dispatch": False,
        "reason": reason,
        "open_prs": open_prs,
        "tend": tend or [],
    }
    payload.update(extra)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        eprint(f"[!] Governor: DISPATCH BLOCKED ({reason})")
        if tend:
            eprint(f"    Tend queue: {', '.join(f'PR #{n}' for n in tend)}")
    return EXIT_NEGATIVE


def cmd_govern(args: argparse.Namespace) -> int:
    root = main_repo_root()
    cfg = load_config(root, args.config)
    fleet_cfg = cfg.get("fleet", {})

    max_open_prs = int(fleet_cfg.get("max_open_prs", 5))
    max_new_prs_per_day = int(fleet_cfg.get("max_new_prs_per_day", 6))
    min_acceptance_rate_14d = float(fleet_cfg.get("min_acceptance_rate_14d", 0.5))
    kill_switch_file = str(fleet_cfg.get("kill_switch_file") or ".autonomous/STOP")

    # 1. Kill-switch check
    kill_switch_paths = [
        root / kill_switch_file,
        Path.cwd() / kill_switch_file,
        root / ".autonomous" / "STOP",
    ]
    for kp in kill_switch_paths:
        if kp.exists():
            return _block_govern(args, f"kill switch present ({kill_switch_file})")

    # 1a. Circuit-breaker check
    state_dir = _resolve_state_dir(root, getattr(args, "state_dir", None))
    cb_path = _circuit_breaker_path(state_dir)
    if cb_path.is_file() and not _auto_clear_circuit_breaker(root, cb_path):
        return _block_govern(
            args, "circuit breaker tripped (consecutive failed wakes; run reset-circuit)"
        )

    # 1b. Disk headroom floor (issue #115: protect host against out-of-disk failure)
    min_free_gb = _resolve_min_free_gb(cfg)
    try:
        free_gb = shutil.disk_usage(str(root)).free / (1024**3)
    except OSError:
        free_gb = min_free_gb
    if free_gb < min_free_gb:
        return _block_govern(
            args, f"disk headroom exhausted ({free_gb:.1f} GiB free < {min_free_gb:.1f} GiB floor)"
        )

    # 1c. Concurrent worktree cap (issue #115: prevent unbounded worktree buildup)
    max_worktrees = int(fleet_cfg.get("max_worktrees", 5))
    run(["git", "worktree", "prune"], cwd=root, quiet=True)
    wt_proc = run(["git", "worktree", "list", "--porcelain"], cwd=root, quiet=True)
    if wt_proc.returncode == 0:
        wt_count = sum(
            1 for line in (wt_proc.stdout or "").splitlines() if line.startswith("worktree ")
        )
        if wt_count > max_worktrees:
            return _block_govern(
                args, f"active worktree cap exceeded ({wt_count} > {max_worktrees})"
            )

    # 1d. Doctor gate (opt-in): on a deployment with doctor.enforce=true, a
    #     hard preflight failure blocks dispatch the same way the kill switch
    #     does — blocked-with-reason, never a silent continue.
    if (fleet_cfg.get("doctor", {}) or {}).get("enforce"):
        doctor = doctor_summary(run_doctor_checks(root, cfg))
        if not doctor["dispatch_ok"]:
            return _block_govern(
                args,
                f"doctor preflight failed ({', '.join(doctor['hard_failures'])})",
                doctor=doctor,
            )

    # 2. Query open PRs
    prs_data, gh_warn = fetch_gh_prs(root, state="open", require_gh=args.require_gh)
    if gh_warn:
        eprint(f"[warn] {gh_warn}")
    open_states = [evaluate_pr_checks(p) for p in prs_data]
    open_count = len(open_states)

    tend: List[int] = sorted(
        int(s["pr"])
        for s in open_states
        if s.get("pr") is not None and (s.get("checks") == "red" or s.get("owner_ask_pending"))
    )

    track_caps = fleet_cfg.get("track_caps") or {}
    caps_report = evaluate_track_caps(track_caps, prs_data)

    # 3. Check open PR limit
    if open_count >= max_open_prs:
        return _block_govern(
            args,
            f"open fleet PR cap reached ({open_count} >= {max_open_prs})",
            open_prs=open_count,
            tend=tend,
            track_caps=caps_report,
        )

    # 4. Check new PRs per day (last 24 hours, including merged/closed PRs)
    all_prs_data, all_warn = fetch_gh_prs(
        root, state="all", limit=200, require_gh=args.require_gh
    )
    if all_warn and not gh_warn:
        eprint(f"[warn] {all_warn}")
    now = datetime.datetime.now(datetime.timezone.utc)
    count_24h = 0
    for p in all_prs_data or prs_data:
        created_str = p.get("createdAt")
        if created_str:
            try:
                dt = datetime.datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                if (now - dt).total_seconds() <= 86400:
                    count_24h += 1
            except (ValueError, TypeError):
                pass
    if count_24h >= max_new_prs_per_day:
        return _block_govern(
            args,
            f"daily new PR cap reached ({count_24h} >= {max_new_prs_per_day})",
            open_prs=open_count,
            tend=tend,
            track_caps=caps_report,
        )

    # 5. Acceptance-rate floor (last 14 days). Only enforced against settled
    # verdicts (merged vs closed-unmerged) in the window: a fresh fleet with
    # no settled PRs has no evidence yet and must keep dispatching -- the
    # governor measures outcomes, it does not prevent the first one.
    if min_acceptance_rate_14d > 0:
        settled_data = all_prs_data
        now = datetime.datetime.now(datetime.timezone.utc)
        merged_14d = 0
        closed_unmerged_14d = 0
        for p in settled_data:
            created_str = p.get("createdAt")
            if created_str:
                try:
                    dt = datetime.datetime.fromisoformat(
                        created_str.replace("Z", "+00:00")
                    )
                    if (now - dt).total_seconds() > 14 * 86400:
                        continue
                except (ValueError, TypeError):
                    continue
            state = str(p.get("state") or "").upper()
            if p.get("mergedAt") or state == "MERGED":
                merged_14d += 1
            elif state == "CLOSED":
                closed_unmerged_14d += 1
        settled_14d = merged_14d + closed_unmerged_14d
        rate_14d = (merged_14d / settled_14d) if settled_14d > 0 else 0.0
        if settled_14d > 0 and rate_14d < min_acceptance_rate_14d:
            return _block_govern(
                args,
                f"acceptance floor missed (14d: {merged_14d}/{settled_14d} = {rate_14d:.2f} < {min_acceptance_rate_14d:.2f})",
                open_prs=open_count,
                tend=tend,
                acceptance_rate_14d=round(rate_14d, 4),
                min_acceptance_rate_14d=min_acceptance_rate_14d,
                track_caps=caps_report,
            )

    req_track = getattr(args, "track", None)
    if req_track and caps_report.get(req_track, {}).get("capped"):
        cap_val = caps_report[req_track]["cap"]
        return _block_govern(
            args,
            f"track cap reached for {req_track} ({caps_report[req_track]['open']} >= {cap_val})",
            open_prs=open_count,
            tend=tend,
            track_caps=caps_report,
        )

    payload = {
        "dispatch": True,
        "reason": "within limits",
        "open_prs": open_count,
        "tend": tend,
    }
    if caps_report:
        payload["track_caps"] = caps_report
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"[+] Governor: DISPATCH ALLOWED (open PRs: {open_count}/{max_open_prs})")
        if tend:
            print(f"    Tend queue: {', '.join(f'PR #{n}' for n in tend)}")
    return EXIT_OK


def _wake_reasons(
    open_states: List[Dict[str, Any]],
    issues: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    """Reasons a wake would need a model turn, from state alone (issue #18)."""
    reasons: List[str] = []
    for state in sorted(open_states, key=lambda item: int(item.get("pr") or 0)):
        pr = state.get("pr")
        if state.get("owner_ask_pending"):
            reasons.append(f"owner-ask #{pr} (answer or apply the request)")
        if state.get("checks") == "red":
            kind = state.get("red_kind") or "unknown"
            reasons.append(f"red-pr #{pr} (fix or triage; red_kind={kind})")
        elif state.get("checks") == "green":
            reasons.append(f"green-unmerged-pr #{pr} (drive to merge)")
    if issues:
        for iss in sorted(issues, key=lambda item: int(item.get("number") or 0)):
            num = iss.get("number")
            reasons.append(f"issue #{num} (open labelled issue)")
    if not open_states and not issues:
        reasons.append("queue-idle (no open fleet PRs; generate or widen targets)")
    return reasons


def _wake_digest(
    open_states: List[Dict[str, Any]],
    reasons: List[str],
    main_sha: str = "",
    issues: Optional[List[Dict[str, Any]]] = None,
) -> str:
    basis_parts = [
        f"{s.get('pr')}|{s.get('head_sha')}|{s.get('checks')}|{s.get('checks_sha')}|{s.get('updated_at', '')}"
        for s in open_states
    ]
    if main_sha:
        basis_parts.append(f"main:{main_sha}")
    if issues:
        for iss in sorted(issues, key=lambda x: int(x.get("number") or 0)):
            basis_parts.append(f"issue:{iss.get('number')}:{iss.get('updatedAt', '')}")
    basis_parts.extend(reasons)
    basis = "\n".join(basis_parts)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def cmd_needs_brain(args: argparse.Namespace) -> int:
    """Stateless-wake predicate: spend a model turn only when state changed.

    Reads the snapshot inputs (PR state via gh, or a stub; plus the ledger
    indirectly through the queue) and the previous wake decision recorded in
    the state dir. No session id is read or written: a *second fresh wake*
    continues from the recorded digest, which is the whole point of the
    stateless shape (issue #18).
    """
    root = main_repo_root()
    cfg = load_config(root, args.config)
    state_dir = _resolve_state_dir(root, args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    prs_data, gh_warn = fetch_gh_prs(root, state="open", require_gh=args.require_gh)
    if gh_warn:
        eprint(f"[warn] {gh_warn}")
    open_states = [evaluate_pr_checks(p) for p in prs_data]
    issues_data = fetch_gh_issues(root, require_gh=args.require_gh)
    wake_path = state_dir / "wake.json"

    fleet_cfg = cfg.get("fleet", {}) or {}
    min_free_gb = _resolve_min_free_gb(cfg)
    try:
        usage = shutil.disk_usage(str(root))
        free_gb = usage.free / (1024**3)
    except OSError:
        free_gb = min_free_gb
    if free_gb < min_free_gb:
        payload: Dict[str, Any] = {
            "tool": "fleet.py needs-brain",
            "needs_brain": False,
            "reasons": [],
            "previous_reason": None,
            "reason": (
                f"disk headroom exhausted ({free_gb:.1f} GiB free < {min_free_gb:.1f} GiB floor); quiet"
            ),
            "digest": None,
            "previous_digest": None,
            "open_prs": len(open_states),
            "state_file": str(wake_path),
            "note": "stateless: halted by disk headroom floor",
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"[quiet] {payload['reason']}")
        return EXIT_OK

    reasons = _wake_reasons(open_states, issues=issues_data)
    directive_reasons = active_directive_reasons(root)
    reasons.extend(directive_reasons)
    kill_switch_file = str((cfg.get("fleet", {}) or {}).get("kill_switch_file") or ".autonomous/STOP")
    kill_switch_paths = [
        root / kill_switch_file,
        Path.cwd() / kill_switch_file,
        root / ".autonomous" / "STOP",
    ]
    if any(path.exists() for path in kill_switch_paths):
        reasons.append("kill-switch-present (tend the queue only; do not generate)")
    main_sha = get_main_sha(root)
    digest = _wake_digest(open_states, reasons, main_sha=main_sha, issues=issues_data)

    wake_path = state_dir / "wake.json"
    previous: Dict[str, Any] = {}
    if wake_path.is_file():
        try:
            previous = json.loads(wake_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            eprint(f"[warn] wake state unreadable, treating as first wake: {wake_path}")
            previous = {}

    # Deep Standby check (V3 Milestone 5 Honest Halt)
    sat_info = saturation_info(root)
    standby_hours = float((fleet_cfg.get("saturation_standby_hours") or 24))
    standby_quiet, standby_expired = deep_standby_suppressed(
        root,
        sat_info,
        previous,
        main_sha,
        open_states,
        issues_data,
        bool(directive_reasons),
        standby_hours,
    )
    if standby_quiet:
        payload = {
            "tool": "fleet.py needs-brain",
            "needs_brain": False,
            "reasons": [],
            "previous_reason": None,
            "reason": "deep standby: repo is saturated (scheduled wake suppressed; waking on events only)",
            "digest": previous.get("digest") if previous else None,
            "previous_digest": previous.get("digest") if previous else None,
            "open_prs": 0,
            "state_file": str(wake_path),
            "note": "stateless: deep standby honest halt",
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"[quiet] {payload['reason']}")
        return EXIT_OK

    unchanged = bool(previous) and previous.get("digest") == digest and not args.force and not standby_expired
    payload: Dict[str, Any] = {
        "tool": "fleet.py needs-brain",
        "needs_brain": not unchanged,
        "reasons": reasons if not unchanged else [],
        "previous_reason": reasons if unchanged else None,
        "reason": (
            "state unchanged since the last wake (0-token quiet)"
            if unchanged
            else "; ".join(reasons) or "nothing to do"
        ),
        "digest": digest,
        "previous_digest": previous.get("digest"),
        "open_prs": len(open_states),
        "state_file": str(wake_path),
        "note": (
            "stateless: reads files only, writes the decision digest; "
            "no session id is involved"
        ),
    }
    if not unchanged:
        write_json_atomic(
            wake_path,
            {
                "schema": "stateless-wake/1",
                "repo": str(root),
                "digest": digest,
                "main_sha": main_sha,
                "reasons": reasons,
                "open_prs": len(open_states),
            },
        )
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        if unchanged:
            print("[+] needs-brain: quiet (state unchanged since the last wake)")
        else:
            print("[*] needs-brain: a wake is warranted")
            for reason in reasons:
                print(f"    - {reason}")
    return EXIT_OK


def cmd_stats(args: argparse.Namespace) -> int:
    root = main_repo_root()
    prs_data, gh_warn = fetch_gh_prs(root, state="all", limit=200, require_gh=args.require_gh)
    if gh_warn:
        eprint(f"[warn] {gh_warn}")

    days = args.days
    now = datetime.datetime.now(datetime.timezone.utc)

    total_opened = 0
    total_merged = 0
    total_closed_unmerged = 0
    by_stream: Dict[str, Dict[str, int]] = {}
    by_model: Dict[str, Dict[str, int]] = {}
    iterations_list: List[int] = []

    for pr in prs_data:
        created_str = pr.get("createdAt")
        if days > 0 and created_str:
            try:
                dt = datetime.datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                if (now - dt).total_seconds() > days * 86400:
                    continue
            except (ValueError, TypeError):
                pass

        total_opened += 1
        state = str(pr.get("state") or "").upper()
        merged_at = pr.get("mergedAt")

        title = str(pr.get("title", ""))
        body = str(pr.get("body", ""))
        stream = "unknown"
        if "stream a" in title.lower() or "mutation" in title.lower() or "Stream: A" in body:
            stream = "A (Mutation)"
        elif "stream b" in title.lower() or "audit" in title.lower() or "Stream: B" in body:
            stream = "B (Test Audit)"
        else:
            stream = "Other"

        model = "unknown"
        model_match = re.search(r"Model:\s*([^\r\n]+)", body)
        if model_match:
            model = model_match.group(1).strip()

        by_stream.setdefault(stream, {"opened": 0, "merged": 0, "closed": 0})
        by_model.setdefault(model, {"opened": 0, "merged": 0, "closed": 0})
        by_stream[stream]["opened"] += 1
        by_model[model]["opened"] += 1

        if merged_at or state == "MERGED":
            total_merged += 1
            by_stream[stream]["merged"] += 1
            by_model[model]["merged"] += 1
        elif state == "CLOSED":
            total_closed_unmerged += 1
            by_stream[stream]["closed"] += 1
            by_model[model]["closed"] += 1

        commits = pr.get("commits")
        if isinstance(commits, list) and commits:
            iterations_list.append(len(commits))
        elif "iterations" in pr:
            try:
                iterations_list.append(int(pr["iterations"]))
            except (ValueError, TypeError):
                pass

    completed = total_merged + total_closed_unmerged
    acceptance_rate = (total_merged / completed) if completed > 0 else 0.0

    iterations_list.sort()
    median_iterations = (
        iterations_list[len(iterations_list) // 2] if iterations_list else 1
    )

    stats_dict = {
        "period_days": days,
        "prs_opened": total_opened,
        "prs_merged": total_merged,
        "prs_closed_unmerged": total_closed_unmerged,
        "acceptance_rate": round(acceptance_rate, 4),
        "median_iterations": median_iterations,
        "by_stream": by_stream,
        "by_model": by_model,
    }

    decided = total_merged + total_closed_unmerged
    incidents = _referee_incidents(root)
    try:
        from conventions import eligible_for_t2

        t2_eligible, t2_reason = eligible_for_t2(acceptance_rate, decided, incidents)
    except Exception:  # conventions helper absent: same floor, computed inline
        t2_eligible = decided >= 10 and incidents == 0 and acceptance_rate >= 0.6
        t2_reason = f"decided={decided} incidents={incidents} rate={round(acceptance_rate, 3)}"
    stats_dict["decided_items"] = decided
    stats_dict["referee_incidents"] = incidents
    stats_dict["t2_eligible"] = bool(t2_eligible)
    stats_dict["t2_reason"] = t2_reason

    sat_info = saturation_info(root)
    stats_dict["saturation"] = sat_info

    if args.json:
        print(json.dumps(stats_dict, indent=2))
        return EXIT_OK

    print(f"## Fleet Statistics ({'All-time' if days == 0 else f'Last {days} Days'})")
    print(
        f"- **Acceptance Rate**: {acceptance_rate:.1%} "
        f"({total_merged} merged, {total_closed_unmerged} closed-unmerged)"
    )
    print(f"- **Total PRs Opened**: {total_opened}")
    if t2_eligible:
        print(
            "- **Trust tier**: eligible for T2 promotion (owner decision; "
            "fleet.py never promotes itself)"
        )
    print(f"- **Median CI Iterations**: {median_iterations}")
    if by_stream:
        print("\n### By Stream")
        for s_name, counts in sorted(by_stream.items()):
            s_comp = counts["merged"] + counts["closed"]
            s_rate = (counts["merged"] / s_comp) if s_comp > 0 else 0.0
            print(
                f"- **{s_name}**: {counts['merged']}/{s_comp} merged "
                f"({s_rate:.1%}) · {counts['opened']} opened"
            )
    if by_model:
        print("\n### By Model")
        for m_name, counts in sorted(by_model.items()):
            m_comp = counts["merged"] + counts["closed"]
            m_rate = (counts["merged"] / m_comp) if m_comp > 0 else 0.0
            print(f"- **{m_name}**: {counts['merged']}/{m_comp} merged ({m_rate:.1%})")

    print("\n### Saturation Status")
    if sat_info.get("saturated"):
        print(f"- **Status**: Saturated (Honest Halt: {sat_info.get('reason')})")
        print(f"- **Since**: {sat_info.get('timestamp')}")
    else:
        print("- **Status**: Active (candidates or backlog items present)")
    return EXIT_OK


# --- Stateless wakes (issue #18) -------------------------------------------
# The timer is a starter, not a nurse: each wake snapshots (0 tokens), asks
# needs-brain whether anything awaits a decision, and only then launches ONE
# fresh headless Orchestrator run with a self-contained prompt. State lives
# in snapshot.json + ledger + journal; there is no session to immortalize
# and no session-id file whose staleness can strand the fleet.


def _needs_brain_from_states(
    root: Path, cfg: Dict[str, Any], open_states: List[Dict[str, Any]]
) -> Dict[str, Any]:
    fleet_cfg = cfg.get("fleet", {})
    max_open = int(fleet_cfg.get("max_open_prs", 5))
    kill_file = str(fleet_cfg.get("kill_switch_file") or ".autonomous/STOP")
    killed = (root / kill_file).exists() or (root / ".autonomous" / "STOP").exists()
    min_free_gb = _resolve_min_free_gb(cfg)
    try:
        usage = shutil.disk_usage(str(root))
        free_gb = usage.free / (1024**3)
    except OSError:
        free_gb = min_free_gb
    disk_exhausted = free_gb < min_free_gb

    tend: List[int] = []
    green: List[int] = []
    reasons: List[str] = []
    for state in open_states:
        number = state.get("pr")
        if number is None:
            continue
        if state.get("checks") == "red":
            tend.append(number)
            reasons.append(f"red-pr:#{number}")
        if state.get("owner_ask_pending"):
            if number not in tend:
                tend.append(number)
            reasons.append(f"owner-ask:#{number}")
        if state.get("checks") == "green":
            green.append(number)
            reasons.append(f"green-unmerged:#{number}")
    can_dispatch = len(open_states) < max_open and not disk_exhausted
    if can_dispatch:
        reasons.append("queue-headroom")
    reasons.extend(active_directive_reasons(root))
    active_reasons = (
        ["disk-headroom-exhausted"]
        if disk_exhausted
        else (["kill-switch"] if killed else reasons)
    )
    return {
        "needs_brain": (not killed) and (not disk_exhausted) and bool(reasons),
        "reasons": active_reasons,
        "tend": sorted(set(tend)),
        "green_unmerged": sorted(green),
        "open_prs": len(open_states),
        "can_dispatch": can_dispatch and not killed,
    }




def _wake_prompt(
    root: Path,
    cfg: Dict[str, Any],
    state_dir: Path,
    snapshot: Dict[str, Any],
    brain: Dict[str, Any],
) -> str:
    ledger = cfg.get("ledger_path") or "docs/AUTONOMOUS_WORK_LEDGER.md"
    merge_policy = (cfg.get("fleet", {}) or {}).get("merge_policy", "never")
    skill_dir = Path(__file__).resolve().parent
    lines = [
        "You are a FRESH autonomous-improvement Orchestrator wake. You have no",
        "memory beyond the files named here; that is by design. Work from them,",
        "leave state behind in them, and exit when this wake's duties are done.",
        "",
        f"State dir: {state_dir}",
        f"- Snapshot (just refreshed, deterministic): {state_dir / 'snapshot.json'}",
        f"- Journal (append your decisions + one state line per open PR): {state_dir / 'journal.md'}",
        f"- Ledger: {root / ledger}",
        f"- Skill scripts: {skill_dir}",
        f"Main SHA at wake: {snapshot.get('main_sha', 'unknown')}",
        f"Wake reasons: {', '.join(brain['reasons'])}",
        "",
        "Open PRs:",
    ]
    for pr in snapshot.get("open_prs") or []:
        head = str(pr.get("head_sha") or "unknown")[:9]
        red = f" [{pr['red_kind']}]" if pr.get("red_kind") else ""
        ask = "yes" if pr.get("owner_ask_pending") else "no"
        lines.append(
            f"- PR #{pr.get('pr')} | head {head} | checks {pr.get('checks')}{red} "
            f"| owner-ask {ask} | iterations {pr.get('iterations')}"
        )
    if not snapshot.get("open_prs"):
        lines.append("- (none)")
    lines.extend(format_directives_block(root))
    lines += [
        "",
        "Duties, in order:",
        f"1. Run `python3 {skill_dir / 'fleet.py'} doctor`. On any hard",
        "   failure, record it in the journal and STOP: blocked-with-reason",
        "   beats dispatching from a drifted deployment.",
        f"2. Run `python3 {skill_dir / 'fleet.py'} govern --json` and obey it.",
        "3. Tend before generating: every red or owner-ask PR above gets fixed,",
        "   answered, or re-asked (references/04 CI green loop).",
        "4. Only if govern allows dispatch: select the highest-priority eligible",
        f"   work across ALL tracks using `python3 {skill_dir / 'survey.py'} --json`",
        f"   and `python3 {skill_dir / 'backlog.py'} harvest --json` (falling back to",
        f"   `python3 {skill_dir / 'ledger.py'} next --stream A --rank risk` only",
        "   when mutation or test-gap is selected). Claim it with `fleet.py claim`",
        "   BEFORE working, register/update `fleet.py worker`, verify via",
        "   `orchestrator.py verify-gate`, and finish through `fleet.py publish`",
        "   (or issue mode for T1/research/security findings).",
        f"5. Merge policy for this repo: {merge_policy}. When in doubt, do not merge.",
        "6. Append decisions to the journal, then exit. A later fresh wake",
        "   continues from the files — nothing may live only in this session.",
    ]
    return "\n".join(lines) + "\n"


def _render_harness_argv(
    template: List[str], prompt_file: Path, prompt: str, model: str
) -> List[str]:
    argv: List[str] = []
    used_prompt = False
    for part in template:
        if part == "{model}" and not model:
            continue
        if "{prompt_file}" in part or "{prompt}" in part:
            used_prompt = True
        argv.append(
            part.replace("{prompt_file}", str(prompt_file))
            .replace("{prompt}", prompt)
            .replace("{model}", model)
        )
    if not used_prompt:
        # opencode run / claude -p / codex exec all take the prompt as the
        # final positional argument.
        argv.append(prompt)
    return argv


def cmd_wake(args: argparse.Namespace) -> int:
    root = main_repo_root()
    cfg = load_config(root, args.config)
    state_dir = _resolve_state_dir(root, args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    cb_path = _circuit_breaker_path(state_dir)
    if cb_path.is_file():
        if not _auto_clear_circuit_breaker(root, cb_path):
            eprint(
                "[!] Circuit breaker tripped: wake blocked until reset-circuit or new commit on main"
            )
            return EXIT_NEGATIVE

    cmd_snapshot(
        argparse.Namespace(
            config=args.config,
            state_dir=str(state_dir),
            output=None,
            require_gh=args.require_gh,
            quiet=True,
        )
    )
    snapshot_path = state_dir / "snapshot.json"
    snapshot: Dict[str, Any] = {}
    if snapshot_path.exists():
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            snapshot = {}
    brain = _needs_brain_from_states(root, cfg, snapshot.get("open_prs") or [])

    record: Dict[str, Any] = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "needs_brain": brain["needs_brain"],
        "reasons": brain["reasons"],
        "action": "quiet",
    }
    exit_code = EXIT_OK

    if brain["needs_brain"]:
        prompt = _wake_prompt(root, cfg, state_dir, snapshot, brain)
        prompt_file = state_dir / "last-wake-prompt.md"
        prompt_file.write_text(prompt, encoding="utf-8")
        if args.print_prompt:
            print(prompt, end="")

        fleet_cfg = cfg.get("fleet", {}) or {}
        template: Optional[List[str]] = None
        if args.harness_cmd:
            try:
                template = shlex.split(args.harness_cmd)
            except ValueError as exc:
                raise UsageError(f"cannot parse --harness-cmd: {exc}")
        elif fleet_cfg.get("harness_cmd"):
            template = [str(part) for part in fleet_cfg["harness_cmd"]]

        if template:
            if args.model:
                ladder = [args.model]
            elif os.environ.get("ORCH_MODEL_LADDER", "").split():
                ladder = os.environ["ORCH_MODEL_LADDER"].split()
            elif fleet_cfg.get("model_ladder"):
                ladder = [str(m) for m in fleet_cfg["model_ladder"]]
            else:
                ladder = [""]

            cooldown_sec = int(
                fleet_cfg.get("model_cooldown")
                or os.environ.get("ORCH_MODEL_COOLDOWN", ORCH_MODEL_COOLDOWN_DEFAULT)
            )
            cooling = _load_cooling_models(state_dir, cooldown_sec=cooldown_sec)
            candidate_ladder = (
                [m for m in ladder if m not in cooling] if ladder != [""] else ladder
            )
            if ladder != [""] and not candidate_ladder:
                print(
                    f"[*] All models in ladder are in cooldown ({', '.join(ladder)}); quiet"
                )
                record["action"] = "cooling"
                with open(state_dir / "wakes.jsonl", "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record) + "\n")
                print(f"[*] Wake recorded: {record['action']}")
                return EXIT_OK

            launched = False
            for model in candidate_ladder:
                argv = _render_harness_argv(template, prompt_file, prompt, model)
                try:
                    proc = run(argv, cwd=root, quiet=True)
                except EnvError as exc:
                    eprint(f"[warn] harness launch failed: {exc}")
                    continue
                tail = (proc.stdout or "").strip().splitlines()
                if tail:
                    print(f"    harness: {tail[-1]}")
                if proc.returncode == 0:
                    record["action"] = "launched"
                    record["model"] = model or None
                    launched = True
                    break

                combined_out = (proc.stdout or "") + "\n" + (proc.stderr or "")
                if OUTAGE_PATTERN.search(combined_out):
                    eprint(
                        f"[warn] harness exited {proc.returncode}"
                        + (f" on model {model!r}" if model else "")
                        + "; infrastructure outage detected (not parking model)"
                    )
                elif (
                    QUOTA_PATTERN.search(combined_out)
                    or DEAD_MODEL_PATTERN.search(combined_out)
                ):
                    eprint(
                        f"[warn] harness exited {proc.returncode}"
                        + (f" on model {model!r}" if model else "")
                        + "; quota/dead-model error detected; parking in cooldown"
                    )
                    if model:
                        _record_model_failure(state_dir, model)
                else:
                    eprint(
                        f"[warn] harness exited {proc.returncode}"
                        + (f" on model {model!r}" if model else "")
                        + "; trying next ladder entry"
                    )
            if not launched:
                record["action"] = "launch-failed"
                exit_code = EXIT_NEGATIVE
        else:
            record["action"] = "prompt-only"
            print(
                f"[*] Brain needed ({', '.join(brain['reasons'])}); no harness "
                f"configured — wake prompt written to {prompt_file}"
            )

    with open(state_dir / "wakes.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    _check_consecutive_failed_wakes(state_dir)
    print(
        f"[*] Wake recorded: {record['action']}"
        + (f" ({', '.join(brain['reasons'])})" if brain["reasons"] else "")
    )
    return exit_code


# --- Doctor: blast-radius advice as checks (issue #19) ----------------------
# fleet-ops/references/05's deployment rules were prose; nothing noticed when a token
# got re-scoped or linger vanished after a reinstall. doctor is the
# read-only, machine-readable version: hard failures exit nonzero and the
# cold-start rule is "do not dispatch on a hard failure — report instead".






CLAIM_LABEL = "autonomous-improvement"


def _gh(root: Path, argv: List[str], what: str) -> str:
    proc = run(["gh"] + argv, cwd=root, quiet=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise EnvError(f"gh {what} failed: {detail[0] if detail else proc.returncode}")
    return proc.stdout


def _ensure_label(root: Path) -> None:
    # Best effort: the label is a filter aid, never worth failing a claim over.
    run(["gh", "label", "create", CLAIM_LABEL, "--force"], cwd=root, quiet=True)


def _body_file(text: str) -> str:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".md", prefix="fleet-body-", delete=False, encoding="utf-8"
    )
    handle.write(text)
    handle.close()
    return handle.name


def _pr_number_from_url(output: str) -> Optional[int]:
    match = re.search(r"/(\d+)\s*$", output.strip())
    return int(match.group(1)) if match else None


def cmd_claim(args: argparse.Namespace) -> int:
    from ledger import _claimed, _gh_claims, composite_hash

    root = main_repo_root()
    cfg = load_config(root, args.config)

    item_id: Optional[str] = None
    item_fp: Optional[str] = None
    if getattr(args, "item", None):
        from backlog import harvest_backlog, load_open_claims

        backlog_path = root / ".autonomous" / "backlog.json"
        try:
            bdata = json.loads(backlog_path.read_text(encoding="utf-8")) if backlog_path.is_file() else harvest_backlog(root, cfg)
        except Exception:
            bdata = harvest_backlog(root, cfg)
        matched = next((it for it in bdata.get("items", []) if it.get("id") == args.item or it.get("fingerprint") == args.item), None)
        if not matched:
            raise UsageError(f"backlog item not found: {args.item}")

        item_id, item_fp = matched["id"], matched.get("fingerprint")
        target = rel_path(root, matched["target_paths"][0])
        stream = normalize_stream(matched["stream"])
        if item_fp and item_fp in load_open_claims(root)[0]:
            eprint(f"[!] item {item_id} ({item_fp}) is already claimed by an open PR")
            return EXIT_NEGATIVE
    else:
        if not getattr(args, "target", None) or not getattr(args, "stream", None):
            raise UsageError("either --target and --stream or --item is required")
        stream, target = normalize_stream(args.stream), rel_path(root, args.target)

    if not path_at_head(root, target):
        raise UsageError(f"claim target does not exist at HEAD: {target}")

    claims, _warning = _gh_claims(root, require_gh=True)
    test = test_for_source(root, cfg, target) if stream == "A" else None
    claimed_by = _claimed(claims, target, test)
    if claimed_by:
        eprint(f"[!] {target} is already claimed by open PR {claimed_by}")
        return EXIT_NEGATIVE

    slug = args.slug or re.sub(
        r"[^a-z0-9]+", "-", f"{stream.lower()}-{Path(target).stem}".lower()
    ).strip("-")
    registry = load_worktree_registry(root)
    if any(entry.get("slug") == slug for entry in registry):
        raise Fatal(f"slug already claimed locally: {slug}")

    fleet_cfg = cfg.get("fleet", {}) or {}
    min_free_gb = _resolve_min_free_gb(cfg)
    try:
        usage = shutil.disk_usage(str(root))
        free_gb = usage.free / (1024**3)
    except OSError:
        free_gb = min_free_gb
    if free_gb < min_free_gb:
        eprint(
            f"[!] disk headroom exhausted ({free_gb:.1f} GiB free < {min_free_gb:.1f} GiB floor); "
            "refusing to claim"
        )
        return EXIT_NEGATIVE

    max_worktrees = int(fleet_cfg.get("max_worktrees", 5))
    run(["git", "worktree", "prune"], cwd=root, quiet=True)
    wt_proc = run(["git", "worktree", "list", "--porcelain"], cwd=root, quiet=True)
    if wt_proc.returncode == 0:
        wt_count = sum(
            1 for line in (wt_proc.stdout or "").splitlines() if line.startswith("worktree ")
        )
        if wt_count >= max_worktrees:
            eprint(
                f"[!] active worktree cap reached ({wt_count} >= {max_worktrees}); "
                "refusing to claim"
            )
            return EXIT_NEGATIVE

    orchestrator = Path(__file__).with_name("orchestrator.py")
    run(
        [sys.executable, str(orchestrator), "create-worktree",
         "--slug", slug, "--type", "test"],
        cwd=root,
        check=True,
    )
    entry = next(
        item for item in load_worktree_registry(root) if item.get("slug") == slug
    )
    worktree = Path(str(entry["path"]))
    branch = str(entry["branch"])

    source_hash = hash_at_head(root, target) or ""
    test_hash = hash_at_head(root, test) if test and path_at_head(root, test) else None
    composite = composite_hash(source_hash, test_hash)

    try:
        git(["commit", "--allow-empty", "-m", f"wip(autonomous): claim {target}"],
            cwd=worktree, check=True, quiet=True)
        git(["push", "-u", "origin", branch], cwd=worktree, check=True, quiet=True)
    except Fatal as exc:
        run([sys.executable, str(orchestrator), "cleanup-worktree",
             "--slug", slug, "--force", "--delete-branch"], cwd=root, check=False)
        eprint(
            "[!] claim failed before opening the draft; removed the worktree "
            f"and branch it created: {exc}"
        )
        return EXIT_NEGATIVE

    body = (
        "## Claim (WIP)\n\n"
        f"Target: `{target}` · Stream: {stream_label(stream)} · "
        f"Composite: `{composite}`\n\n"
    )
    if item_fp:
        body += f"<!-- autonomous:fingerprint {item_fp} -->\nItem-ID: `{item_id}`\n\n"
    body += (
        "Draft opened at claim time by `fleet.py claim`: it is the collision "
        "claim, the visibility, and the CI trigger in one. The final body is "
        "rendered by `fleet.py publish` from `.autonomous/evidence.json`; "
        "this stub is replaced then, and only then does the PR flip ready.\n"
    )
    _ensure_label(root)
    output = _gh(
        root,
        ["pr", "create", "--draft", "--head", branch,
         "--title", f"WIP: {stream_label(stream)} claim {target}",
         "--body-file", _body_file(body), "--label", CLAIM_LABEL],
        "pr create",
    )
    number = _pr_number_from_url(output)
    payload = {
        "target": target,
        "stream": stream,
        "slug": slug,
        "branch": branch,
        "worktree": str(worktree),
        "composite": composite,
        "pr": number,
        "url": output.strip(),
        "item_id": item_id,
        "fingerprint": item_fp,
    }
    if args.json:
        print(json.dumps(payload))
    else:
        print(f"[+] Claimed {target} as draft PR #{number} ({payload['url']})")
        print(f"    worktree: {worktree} · branch: {branch} · composite: {composite}")
    return EXIT_OK


def cmd_dashboard(args: argparse.Namespace) -> int:
    root = main_repo_root()
    cfg = load_config(root, args.config)
    state_dir = _resolve_state_dir(root, getattr(args, "state_dir", None))
    return run_dashboard_command(
        args,
        root,
        cfg,
        state_dir,
        fetch_prs_fn=fetch_gh_prs,
        evaluate_pr_fn=evaluate_pr_checks,
        load_cooling_fn=_load_cooling_models,
    )


def _list_all(root: Path, kind: str) -> List[Dict[str, Any]]:
    fields = "number,title,body" + (",state" if kind == "pr" else "")
    output = _gh(
        root,
        [kind, "list", "--state", "all", "--json", fields,
         "--limit", "200"],
        f"{kind} list",
    )
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        raise EnvError(f"gh {kind} list returned invalid JSON")
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _find_key(items: List[Dict[str, Any]], key: str) -> Optional[int]:
    for item in items:
        text = f"{item.get('title') or ''}\n{item.get('body') or ''}"
        if key in text and isinstance(item.get("number"), int):
            return item["number"]
    return None


def _open_claim_pr(prs: List[Dict[str, Any]], target: str) -> Optional[int]:
    """The first open PR whose body claims this target (the `fleet.py claim` stub)."""
    marker = f"Target: `{target}`"
    for item in prs:
        if str(item.get("state") or "").upper() != "OPEN":
            continue
        if marker in str(item.get("body") or "") and isinstance(item.get("number"), int):
            return int(item["number"])
    return None


def _survivor_issue_link(root: Path, data: Dict[str, Any]) -> Optional[int]:
    """The issue filed for the evidence's mutant, so the published PR can reference it."""
    if data.get("kind") != "mutant":
        return None
    from mutants import mutant_id

    mutant = data.get("mutant") or {}
    file_rel = rel_path(root, str(mutant.get("file") or ""))
    key = mutant_id(file_rel, int(mutant.get("line") or 0), str(mutant.get("mutated") or ""))
    return _find_key(_list_all(root, "issue"), key)


def _publish_issues(root: Path, args: argparse.Namespace) -> int:
    from mutants import mutant_id

    report_path = Path(args.issues_from)
    if not report_path.is_absolute():
        report_path = root / report_path
    if not report_path.exists():
        raise Fatal(f"engine report not found: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    survived = report.get("survived") if isinstance(report, dict) else None
    if not isinstance(survived, list):
        raise Fatal(f"report has no survived list: {report_path}")

    issues = _list_all(root, "issue")
    prs = _list_all(root, "pr")
    _ensure_label(root)
    outcomes: List[Dict[str, Any]] = []
    for entry in survived:
        file_rel = rel_path(root, str(entry.get("file") or report.get("source") or ""))
        line = int(entry.get("line") or 0)
        mutated = str(entry.get("mutated") or "")
        key = mutant_id(file_rel, line, mutated)
        number = _find_key(issues, key)
        kind = "issue"
        if number is None:
            number = _find_key(prs, key)
            kind = "pr"
        if number is not None:
            outcomes.append({"key": key, "action": "existing", "kind": kind, "number": number})
            continue
        description = str(entry.get("description") or "surviving mutant")
        claim = _open_claim_pr(prs, file_rel)
        body = (
            f"Mutant-Key: `{key}`\n\n"
            f"A mutant survived the test suite at `{file_rel}:{line}` "
            f"({description}):\n\n"
            "```diff\n"
            f"-{entry.get('original') or ''}\n"
            f"+{mutated}\n"
            "```\n\n"
            "Kill it with a test that is red with the mutant applied and green "
            "on clean source (`evidence.py mutant`), or record a triage "
            "disposition with `mutants.py record`. Filed by `fleet.py publish`.\n"
        )
        if claim is not None:
            body += f"\nClaim in progress: #{claim} (`fleet.py claim` draft).\n"
        output = _gh(
            root,
            ["issue", "create", "--title", f"[mutant] {file_rel}:{line} — {description}",
             "--body-file", _body_file(body), "--label", CLAIM_LABEL],
            "issue create",
        )
        number = _pr_number_from_url(output)
        issues.append({"number": number, "title": "", "body": body})
        outcome = {"key": key, "action": "created", "kind": "issue", "number": number}
        if claim is not None:
            outcome["linked_pr"] = claim
        outcomes.append(outcome)

    if args.json:
        print(json.dumps(outcomes))
    else:
        for outcome in outcomes:
            print(f"[*] {outcome['key']}: {outcome['action']} {outcome['kind']} #{outcome['number']}")
    return EXIT_OK


def cmd_publish(args: argparse.Namespace) -> int:
    root = repo_root()
    if args.issues_from:
        return _publish_issues(root, args)
    if args.pr is None:
        raise UsageError("publish needs --pr <num> or --issues-from <engine.json>")

    cfg = load_config(root, args.config)
    tier = (cfg.get("trust") or {}).get("tier")
    policy_hit = _ai_policy_hit(root)
    if tier == "T1" or policy_hit:
        detail = (
            "; this repo's AI-contribution policy forces issue mode"
            if policy_hit and tier != "T1"
            else ""
        )
        raise UsageError(
            f"trust tier {tier or 'T1 (policy)'}: publish is issue-mode only "
            f"(--issues-from){detail}. Promotion to T2 is the owner's; "
            "`fleet.py stats` reports eligibility."
        )

    from evidence import validate_evidence_file

    evidence_path = Path(args.evidence) if args.evidence else root / ".autonomous" / "evidence.json"
    if not evidence_path.is_absolute():
        evidence_path = root / evidence_path
    data, problems = validate_evidence_file(root, evidence_path)
    if problems or data is None:
        listing = "\n  ".join(problems or ["no artifact"])
        raise Fatal(
            "evidence artifact is not publishable; the draft stays draft:\n  " + listing
        )
    body = _render_evidence_body(data)
    link = _survivor_issue_link(root, data)
    if link is not None:
        body = body.rstrip("\n") + f"\n\nTracks the survivor issue #{link}.\n"
    if args.dry_run:
        print(body, end="")
        return EXIT_OK
    edit = ["pr", "edit", str(args.pr), "--body-file", _body_file(body)]
    if args.title:
        edit += ["--title", args.title]
    _gh(root, edit, "pr edit")
    _gh(root, ["pr", "ready", str(args.pr)], "pr ready")
    print(f"[+] PR #{args.pr} published: evidence rendered, flipped ready.")
    return EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fleet.py",
        description="Autonomous improvement fleet state and governance.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # snapshot
    p_snap = subparsers.add_parser("snapshot", help="record state snapshot")
    p_snap.add_argument("--config", help="path to config.json")
    p_snap.add_argument("--state-dir", help="directory for snapshots")
    p_snap.add_argument("--output", help="write snapshot JSON to explicit path")
    p_snap.add_argument("--require-gh", action="store_true", help="fail if gh is unavailable")
    p_snap.add_argument("--quiet", action="store_true", help="suppress human output")

    # pr-state
    p_state = subparsers.add_parser("pr-state", help="emit references/04 PR state line")
    p_state.add_argument("--config", help="path to config.json")
    p_state.add_argument("--pr", type=int, help="filter by PR number")
    p_state.add_argument("--json", action="store_true", help="emit JSON")
    p_state.add_argument("--require-gh", action="store_true", help="fail if gh is unavailable")

    # doctor
    p_doc = subparsers.add_parser("doctor", help="read-only blast-radius preflight")
    p_doc.add_argument("--config", help="path to config.json")
    p_doc.add_argument("--json", action="store_true", help="emit JSON")

    # govern
    p_gov = subparsers.add_parser("govern", help="evaluate governor dispatch limits")
    p_gov.add_argument("--config", help="path to config.json")
    p_gov.add_argument("--state-dir", help="directory for fleet state")
    p_gov.add_argument("--json", action="store_true", help="emit JSON")
    p_gov.add_argument("--require-gh", action="store_true", help="fail if gh is unavailable")
    p_gov.add_argument("--track", help="evaluate dispatch for a specific track")

    # needs-brain
    p_brain = subparsers.add_parser(
        "needs-brain", help="0-token quiet-hours predicate for stateless wakes"
    )
    p_brain.add_argument("--config", help="path to config.json")
    p_brain.add_argument("--state-dir", help="directory holding wake.json")
    p_brain.add_argument("--force", action="store_true", help="ignore the recorded decision digest")
    p_brain.add_argument("--require-gh", action="store_true", help="fail if gh is unavailable")
    p_brain.add_argument("--json", action="store_true", help="emit JSON")

    # stats
    p_stats = subparsers.add_parser("stats", help="compute fleet acceptance and metrics")
    p_stats.add_argument("--config", help="path to config.json")
    p_stats.add_argument("--days", type=int, default=14, help="lookback window in days (default: 14, 0 = all)")
    p_stats.add_argument("--json", action="store_true", help="emit JSON")
    p_stats.add_argument("--require-gh", action="store_true", help="fail if gh is unavailable")

    # claim
    p_claim = subparsers.add_parser("claim", help="open a draft PR claiming a target or backlog item")
    p_claim.add_argument("--config", help="path to config.json")
    p_claim.add_argument("--target", help="repo-relative target path")
    p_claim.add_argument(
        "--stream", help="available stream letter (1/2 alias A/B)"
    )
    p_claim.add_argument("--item", help="backlog item id or fingerprint to claim")
    p_claim.add_argument("--slug", help="worktree slug (default: derived from target)")
    p_claim.add_argument("--json", action="store_true", help="emit JSON as the last line")

    # wake
    p_wake = subparsers.add_parser(
        "wake", help="stateless wake: snapshot, needs-brain gate, one fresh harness run"
    )
    p_wake.add_argument("--config", help="path to config.json")
    p_wake.add_argument("--state-dir", help="directory for snapshots and wake records")
    p_wake.add_argument(
        "--harness-cmd",
        help="headless harness argv template ({prompt_file}/{prompt}/{model} substituted)",
    )
    p_wake.add_argument("--model", help="single model override for {model}")
    p_wake.add_argument("--print-prompt", action="store_true", help="also print the wake prompt")
    p_wake.add_argument("--require-gh", action="store_true", help="fail if gh is unavailable")

    # publish
    p_pub = subparsers.add_parser(
        "publish", help="render evidence into a PR and flip it ready, or file survivor issues"
    )
    p_pub.add_argument("--config", help="path to config.json")
    p_pub.add_argument("--pr", type=int, help="draft PR number to publish")
    p_pub.add_argument("--title", help="final PR title")
    p_pub.add_argument("--evidence", help="evidence artifact path")
    p_pub.add_argument("--dry-run", action="store_true", help="print the rendered body only")
    p_pub.add_argument("--issues-from", help="engine JSON: file one issue per survivor (deduped)")
    p_pub.add_argument("--json", action="store_true", help="emit JSON as the last line")

    # pool
    p_pool = subparsers.add_parser(
        "pool", help="show the recorded model pool or emit the rotation ladder (issue #103)"
    )
    p_pool.add_argument("action", choices=("show", "ladder"), nargs="?", default="show")
    p_pool.add_argument("--config", help="path to config.json")
    p_pool.add_argument("--json", action="store_true")

    # reset-circuit
    p_reset = subparsers.add_parser(
        "reset-circuit", help="re-arm the wake circuit breaker after consecutive failures"
    )
    p_reset.add_argument("--config", help="path to config.json")
    p_reset.add_argument("--state-dir", help="directory holding CIRCUIT_BREAKER")
    p_reset.add_argument("--json", action="store_true", help="emit JSON")

    register_cockpit_subparsers(subparsers)

    args = parser.parse_args(argv)

    try:
        if args.command == "snapshot":
            return cmd_snapshot(args)
        if args.command == "pr-state":
            return cmd_pr_state(args)
        if args.command == "doctor":
            return cmd_doctor(args)
        if args.command == "govern":
            return cmd_govern(args)
        if args.command == "needs-brain":
            return cmd_needs_brain(args)
        if args.command == "stats":
            return cmd_stats(args)
        if args.command == "wake":
            return cmd_wake(args)
        if args.command == "claim":
            return cmd_claim(args)
        if args.command == "publish":
            return cmd_publish(args)
        if args.command == "pool":
            return cmd_pool(args)
        if args.command == "reset-circuit":
            return cmd_reset_circuit(args)
        if args.command == "dashboard":
            return cmd_dashboard(args)
        if args.command == "directive":
            return cmd_directive(args)
        if args.command == "worker":
            return cmd_worker(args)
        if args.command == "work-gate":
            return cmd_work_gate(args, fetch_gh_prs, evaluate_pr_checks)
        raise UsageError(f"unknown command: {args.command}")
    except (UsageError, EnvError, Fatal) as exc:
        return format_exit(exc)


if __name__ == "__main__":
    sys.exit(main())
