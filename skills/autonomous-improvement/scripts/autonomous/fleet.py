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


def _resolve_state_dir(root: Path, explicit: Optional[str] = None) -> Path:
    if explicit:
        path = Path(explicit)
        return path if path.is_absolute() else (root / path).resolve()
    env_dir = os.environ.get("ORCH_STATE_DIR")
    if env_dir:
        return Path(env_dir).resolve()
    default_dir = Path.home() / ".local" / "share" / "orchestrator"
    if default_dir.exists():
        return default_dir
    return root / ".autonomous" / "state"


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


def _doctor_check(name: str, severity: str, ok: bool, detail: str) -> Dict[str, Any]:
    return {"name": name, "severity": severity, "ok": bool(ok), "detail": detail}


def _journal_age(path: Path) -> str:
    """``journal YYYY-MM-DD`` or ``no journal`` — the tie-breaker a human reads."""
    journal = path / "journal.md"
    if not journal.is_file():
        return "no journal"
    return "journal " + time.strftime("%Y-%m-%d", time.localtime(journal.stat().st_mtime))


def _doctor_run(argv: List[str], timeout: int = 20) -> Optional[subprocess.CompletedProcess]:
    """Run a doctor probe; None when the tool is missing."""
    try:
        return run(argv, quiet=True, timeout=timeout)
    except Exception:  # missing tool / timeout
        return None


def _origin_repo_slug(root: Path) -> Optional[str]:
    proc = _doctor_run(["git", "remote", "get-url", "origin"], timeout=10)
    if proc is None or proc.returncode != 0:
        return None
    url = (proc.stdout or "").strip()
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", url)
    return match.group(1) if match else None


def run_doctor_checks(root: Path, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Read-only blast-radius preflight (issue #19): advice becomes checks.

    Hard failures are dispatch blockers; soft findings are warnings the
    Orchestrator reports and continues past (documented per check).
    """
    fleet_cfg = cfg.get("fleet", {}) or {}
    doctor_cfg = fleet_cfg.get("doctor", {}) or {}
    merge_policy = str(fleet_cfg.get("merge_policy") or "never")
    checks: List[Dict[str, Any]] = []

    # 1. gh auth identity
    auth = _doctor_run(["gh", "auth", "status"], timeout=20)
    auth_text = ((auth.stdout or "") + (auth.stderr or "")) if auth else ""
    account_match = re.search(r"account\s+([A-Za-z0-9._-]+)", auth_text)
    checks.append(
        _doctor_check(
            "gh-auth",
            "hard",
            bool(auth and auth.returncode == 0 and account_match),
            (
                f"logged in as {account_match.group(1)}"
                if account_match
                else "gh auth status failed or no account line (gh missing, logged out, or offline)"
            ),
        )
    )

    # 2. Token scopes vs the merge policy
    forbidden = set(
        doctor_cfg.get("forbidden_token_scopes")
        or ["admin:org", "delete_repo", "admin:public_key", "admin:repo_hook"]
    )
    scopes_match = re.search(r"Token scopes:\s*(.*)", auth_text)
    scopes = (
        [s.strip().strip("'\"") for s in scopes_match.group(1).split(",") if s.strip()]
        if scopes_match
        else []
    )
    overbroad = sorted(forbidden.intersection(scopes))
    checks.append(
        _doctor_check(
            "token-scopes",
            "hard",
            not overbroad,
            (
                f"scopes {scopes or 'unknown'} carry no merge/admin-capable entry"
                if not overbroad
                else f"token scopes include {overbroad} while merge_policy={merge_policy!r}; "
                "re-scope the token to plain repo access"
            ),
        )
    )

    # 3. Branch protection on the base branch (merge_policy=never requires the
    #    agent to be structurally unable to merge, not merely instructed not to)
    branch = str(cfg.get("base_ref") or "origin/main").split("/")[-1]
    slug = _origin_repo_slug(root)
    if slug:
        prot = _doctor_run(["gh", "api", f"repos/{slug}/branches/{branch}/protection"], timeout=20)
        prot_ok = bool(prot and prot.returncode == 0 and "required" in (prot.stdout or ""))
        require = bool(doctor_cfg.get("require_branch_protection", merge_policy == "never"))
        checks.append(
            _doctor_check(
                "branch-protection",
                "hard" if require else "soft",
                prot_ok or not require,
                (
                    f"required reviews present on {slug}@{branch}"
                    if prot_ok
                    else f"no protection with required reviews readable on {slug}@{branch}"
                ),
            )
        )
    else:
        checks.append(
            _doctor_check(
                "branch-protection",
                "hard" if doctor_cfg.get("require_branch_protection") else "soft",
                False if doctor_cfg.get("require_branch_protection") else True,
                "no origin remote to verify branch protection against",
            )
        )

    # 4. Linger (a user session that dies with SSH kills the timers)
    os_user = str(doctor_cfg.get("os_user") or os.environ.get("USER") or "unknown")
    linger = _doctor_run(["loginctl", "show-user", os_user, "-p", "Linger", "--value"], timeout=10)
    linger_value = (linger.stdout or "").strip().lower() if linger else ""
    checks.append(
        _doctor_check(
            "linger",
            "hard",
            linger_value == "yes",
            (
                f"loginctl reports Linger={linger_value or 'unknown'} for {os_user}"
                + ("" if linger_value == "yes" else "; enable it (loginctl enable-linger) or timers die with the session")
            ),
        )
    )

    # 5. Watchdog timer active (soft by default: dev boxes may not install it)
    timer = _doctor_run(["systemctl", "--user", "is-active", "orchestrator-watchdog.timer"], timeout=10)
    timer_state = (timer.stdout or "").strip() if timer else ""
    timer_required = bool(doctor_cfg.get("require_timer", False))
    checks.append(
        _doctor_check(
            "watchdog-timer",
            "hard" if timer_required else "soft",
            timer_state == "active" or not timer_required,
            f"orchestrator-watchdog.timer is {timer_state or 'unavailable'}",
        )
    )

    # 5b. Environment profile vs live keep-alive machinery. The fleet stack
    #     (beats, watchdog, wake timers) is elective: on a laptop — the
    #     default profile — a running timer means it was installed without,
    #     or beyond, an explicit owner ask. Name it; do not block on it.
    environment = str((cfg.get("session") or {}).get("environment") or "laptop")
    if environment == "laptop" and timer_state == "active":
        checks.append(
            _doctor_check(
                "environment-profile",
                "soft",
                False,
                "session.environment is laptop but orchestrator-watchdog.timer is "
                "active: keep-alive machinery is elective on a personal machine — "
                "uninstall it (install-watchdog.sh --uninstall) or set "
                "session.environment to vm/ci deliberately",
            )
        )

    # 5c. Keep-alive consent (issue #91). Arming beats/timers is an owner
    #     decision made at least once and recorded in the config — never an
    #     inference from the profile, a prior deployment's leftover files, or
    #     a busy queue. Two real deployments flooded a frozen session (84 and
    #     90 queued nudges) because the stack was already installed and nobody
    #     had been asked again. An active timer without a record is a hard
    #     failure: dispatch stops until the owner is asked.
    session_cfg = cfg.get("session") or {}
    consent = session_cfg.get("keepalive_consent") or {}
    consent_shape = str(consent.get("shape") or "")
    consent_ok = (
        bool(consent.get("granted_by"))
        and bool(consent.get("date"))
        and consent_shape in {"watchdog", "stateless-wake", "beats"}
    )
    if timer_state == "active" or environment in {"vm", "ci"}:
        checks.append(
            _doctor_check(
                "keepalive-consent",
                "hard" if timer_state == "active" else "soft",
                consent_ok,
                (
                    f"keep-alive consent recorded ({consent_shape})"
                    if consent_ok
                    else "keep-alive machinery needs a recorded owner ask: after "
                    "asking, set session.keepalive_consent={granted_by, date, "
                    "shape: watchdog|stateless-wake|beats}; never arm timers on "
                    "inference from the profile or a prior deployment"
                ),
            )
        )

    # 6a. Candidate state dirs (issue #93). The doc's `<project>-oracle`
    #     heuristic and the code's resolution used to disagree, letting a cold
    #     start adopt a journal that had been dead for days while the live
    #     fleet state sat elsewhere. Two distinct candidates are a hard stop:
    #     read the mtimes, archive the stale one, or set ORCH_STATE_DIR.
    candidates = state_dir_candidates()
    if os.environ.get("ORCH_STATE_DIR"):
        # An explicit choice settles it: the scan is only for readers who
        # have not chosen, and two live deployments on one host are legal.
        checks.append(
            _doctor_check(
                "state-dir-candidates",
                "soft",
                True,
                "explicit state dir configured via ORCH_STATE_DIR; candidate scan skipped",
            )
        )
    elif len(candidates) > 1:
        described = "; ".join(
            f"{path} ({_journal_age(path)})" for path in candidates
        )
        checks.append(
            _doctor_check(
                "state-dir-candidates",
                "hard",
                False,
                f"{len(candidates)} candidate state dirs — {described}. Archive "
                "the stale one out of the glob (rename it so it no longer ends in "
                "-oracle) or set ORCH_STATE_DIR; never pick among them.",
            )
        )
    else:
        checks.append(
            _doctor_check(
                "state-dir-candidates",
                "soft",
                True,
                (
                    f"one candidate: {candidates[0]}"
                    if candidates
                    else "no deployed state dirs under ~/.local/share (first deployment)"
                ),
            )
        )

    # 6. State-dir consistency (or stateless wake configured)
    if bool(fleet_cfg.get("stateless_wake")):
        checks.append(_doctor_check("state-dir", "soft", True, "stateless wake configured; no session state required"))
    else:
        state_dir = _resolve_state_dir(root)
        sid = state_dir / "opencode_session_id"
        checks.append(
            _doctor_check(
                "state-dir",
                "soft",
                sid.exists() or state_dir.exists(),
                (
                    f"state dir {state_dir} present"
                    if sid.exists() or state_dir.exists()
                    else f"no state dir at {state_dir} (first wake?)"
                ),
            )
        )

    # 7. Disk headroom for N worktrees
    min_free_gb = float(doctor_cfg.get("min_free_gb", 5))
    usage = shutil.disk_usage(str(root))
    free_gb = usage.free / (1024**3)
    checks.append(
        _doctor_check(
            "disk-headroom",
            "hard",
            free_gb >= min_free_gb,
            f"{free_gb:.1f} GiB free (floor {min_free_gb} GiB)",
        )
    )

    # 8. Clock skew against the snapshot job's reference (when one exists)
    ref_path = _resolve_state_dir(root) / "clock_reference"
    if ref_path.exists():
        try:
            reference = float(ref_path.read_text(encoding="utf-8").strip())
            skew = abs(time.time() - reference)
            max_skew = float(doctor_cfg.get("max_clock_skew_seconds", 300))
            checks.append(
                _doctor_check(
                    "clock-skew",
                    "hard",
                    skew <= max_skew,
                    f"{skew:.0f}s from the snapshot clock reference (max {max_skew:.0f}s)",
                )
            )
        except (OSError, ValueError):
            checks.append(_doctor_check("clock-skew", "soft", True, "clock reference unreadable; skipped"))
    else:
        checks.append(
            _doctor_check("clock-skew", "soft", True, "no clock reference yet (the snapshot job writes one)")
        )

    # 9. Worktree registry has no orphans
    orphans: List[str] = []
    wt_list = _doctor_run(["git", "worktree", "list", "--porcelain"], timeout=10)
    if wt_list and wt_list.returncode == 0:
        for line in (wt_list.stdout or "").splitlines():
            if line.startswith("worktree "):
                path = Path(line.split(" ", 1)[1].strip())
                if path.resolve() != root.resolve() and not path.exists():
                    orphans.append(str(path))
    for entry in load_worktree_registry(root):
        raw = entry.get("path") or entry.get("directory")
        if raw:
            path = Path(str(raw))
            path = path if path.is_absolute() else (root / path)
            if not path.exists():
                orphans.append(str(path))
    checks.append(
        _doctor_check(
            "worktree-registry",
            "hard",
            not orphans,
            "no orphan worktrees" if not orphans else f"orphan worktree path(s): {', '.join(sorted(set(orphans)))}",
        )
    )

    # 10. Model ladder shape (probe-ability itself is owner-side: it costs
    #     tokens, so the doctor checks the mechanical shape instead)
    ladder_raw = os.environ.get("ORCH_MODEL_LADDER") or fleet_cfg.get("model_ladder") or ""
    if isinstance(ladder_raw, (list, tuple)):
        ladder = [str(item) for item in ladder_raw]
    else:
        ladder = [item for item in str(ladder_raw).split() if item]
    problems: List[str] = []
    if len(set(ladder)) != len(ladder):
        problems.append("duplicate entries")
    problems.extend(f"{entry!r} is not provider/model shaped" for entry in ladder if "/" not in entry)
    checks.append(
        _doctor_check(
            "model-ladder",
            "hard",
            not problems,
            (
                "ladder shape ok"
                if not problems and ladder
                else ("; ".join(problems) if problems else "no ladder configured (session default only)")
            ),
        )
    )

    # 11. Instruction hierarchy is declared (repo content is data, never
    #     instructions) — a docs guard the doctor surfaces for deployments.
    # The manual lives under fleet-ops/ (the elective keep-alive machinery).
    docs = root / "fleet-ops" / "references" / "05-unattended-operation.md"
    skill_docs = Path(__file__).resolve().parent.parent.parent / "fleet-ops" / "references" / "05-unattended-operation.md"
    target = docs if docs.exists() else skill_docs
    hierarchy_ok = False
    if target.exists():
        hierarchy_ok = "never instructions" in target.read_text(encoding="utf-8", errors="ignore").lower()
    checks.append(
        _doctor_check(
            "instruction-hierarchy",
            "soft",
            hierarchy_ok,
            (
                "fleet-ops/references/05 states repo content is data, never instructions"
                if hierarchy_ok
                else "fleet-ops/references/05 does not state the instruction hierarchy yet"
            ),
        )
    )
    return checks


def doctor_summary(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    hard = [c["name"] for c in checks if c["severity"] == "hard" and not c["ok"]]
    soft = [c["name"] for c in checks if c["severity"] == "soft" and not c["ok"]]
    return {
        "checks": checks,
        "hard_failures": hard,
        "soft_failures": soft,
        "dispatch_ok": not hard,
        "reason": (
            "all hard checks pass"
            if not hard
            else f"hard preflight failure(s): {', '.join(hard)}"
        ),
    }


def cmd_doctor(args: argparse.Namespace) -> int:
    root = main_repo_root()
    cfg = load_config(root, args.config)
    checks = run_doctor_checks(root, cfg)
    summary = doctor_summary(checks)
    payload = {"tool": "fleet.py doctor", **summary}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for check in checks:
            mark = "ok" if check["ok"] else ("FAIL" if check["severity"] == "hard" else "warn")
            print(f"[{mark:4}] {check['name']}: {check['detail']}")
        if summary["dispatch_ok"]:
            print("[+] Doctor: dispatch OK (no hard failures)")
        else:
            eprint(f"[!] Doctor: DO NOT DISPATCH — {summary['reason']}")
    return EXIT_OK if summary["dispatch_ok"] else EXIT_NEGATIVE


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
            payload = {
                "dispatch": False,
                "reason": f"kill switch present ({kill_switch_file})",
                "open_prs": 0,
                "tend": [],
            }
            if args.json:
                print(json.dumps(payload, indent=2))
            else:
                eprint(f"[!] Governor: DISPATCH BLOCKED ({payload['reason']})")
            return EXIT_NEGATIVE

    # 1b. Doctor gate (opt-in): on a deployment with doctor.enforce=true, a
    #     hard preflight failure blocks dispatch the same way the kill switch
    #     does — blocked-with-reason, never a silent continue.
    if (fleet_cfg.get("doctor", {}) or {}).get("enforce"):
        doctor = doctor_summary(run_doctor_checks(root, cfg))
        if not doctor["dispatch_ok"]:
            payload = {
                "dispatch": False,
                "reason": f"doctor preflight failed ({', '.join(doctor['hard_failures'])})",
                "open_prs": 0,
                "tend": [],
                "doctor": doctor,
            }
            if args.json:
                print(json.dumps(payload, indent=2))
            else:
                eprint(f"[!] Governor: DISPATCH BLOCKED ({payload['reason']})")
                eprint("    Fix the doctor failure or set fleet.doctor.enforce=false deliberately.")
            return EXIT_NEGATIVE

    # 2. Query open PRs
    prs_data, gh_warn = fetch_gh_prs(root, state="open", require_gh=args.require_gh)
    if gh_warn:
        eprint(f"[warn] {gh_warn}")
    open_states = [evaluate_pr_checks(p) for p in prs_data]
    open_count = len(open_states)

    tend: List[int] = []
    for s in open_states:
        pr_num = s.get("pr")
        if pr_num is not None:
            if s.get("checks") == "red" or s.get("owner_ask_pending"):
                tend.append(pr_num)
    tend = sorted(tend)

    # 3. Check open PR limit
    if open_count >= max_open_prs:
        payload = {
            "dispatch": False,
            "reason": f"open fleet PR cap reached ({open_count} >= {max_open_prs})",
            "open_prs": open_count,
            "tend": tend,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            eprint(f"[!] Governor: DISPATCH BLOCKED ({payload['reason']})")
            if tend:
                eprint(f"    Tend queue: {', '.join(f'PR #{n}' for n in tend)}")
        return EXIT_NEGATIVE

    # 4. Check new PRs per day (last 24 hours)
    now = datetime.datetime.now(datetime.timezone.utc)
    count_24h = 0
    for p in prs_data:
        created_str = p.get("createdAt")
        if created_str:
            try:
                dt = datetime.datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                if (now - dt).total_seconds() <= 86400:
                    count_24h += 1
            except (ValueError, TypeError):
                pass
    if count_24h >= max_new_prs_per_day:
        payload = {
            "dispatch": False,
            "reason": f"daily new PR cap reached ({count_24h} >= {max_new_prs_per_day})",
            "open_prs": open_count,
            "tend": tend,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            eprint(f"[!] Governor: DISPATCH BLOCKED ({payload['reason']})")
        return EXIT_NEGATIVE

    # 5. Acceptance-rate floor (last 14 days). Only enforced against settled
    # verdicts (merged vs closed-unmerged) in the window: a fresh fleet with
    # no settled PRs has no evidence yet and must keep dispatching -- the
    # governor measures outcomes, it does not prevent the first one.
    if min_acceptance_rate_14d > 0:
        settled_data, settle_warn = fetch_gh_prs(
            root, state="all", limit=200, require_gh=args.require_gh
        )
        if settle_warn:
            eprint(f"[warn] {settle_warn}")
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
            payload = {
                "dispatch": False,
                "reason": (
                    f"acceptance floor missed "
                    f"(14d: {merged_14d}/{settled_14d} = {rate_14d:.2f} "
                    f"< {min_acceptance_rate_14d:.2f})"
                ),
                "open_prs": open_count,
                "tend": tend,
                "acceptance_rate_14d": round(rate_14d, 4),
                "min_acceptance_rate_14d": min_acceptance_rate_14d,
            }
            if args.json:
                print(json.dumps(payload, indent=2))
            else:
                eprint(f"[!] Governor: DISPATCH BLOCKED ({payload['reason']})")
            return EXIT_NEGATIVE

    payload = {
        "dispatch": True,
        "reason": "within limits",
        "open_prs": open_count,
        "tend": tend,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"[+] Governor: DISPATCH ALLOWED (open PRs: {open_count}/{max_open_prs})")
        if tend:
            print(f"    Tend queue: {', '.join(f'PR #{n}' for n in tend)}")
    return EXIT_OK


def _wake_reasons(open_states: List[Dict[str, Any]]) -> List[str]:
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
    if not open_states:
        reasons.append("queue-idle (no open fleet PRs; generate or widen targets)")
    return reasons


def _wake_digest(open_states: List[Dict[str, Any]], reasons: List[str]) -> str:
    basis = "\n".join(
        [f"{s.get('pr')}|{s.get('head_sha')}|{s.get('checks')}|{s.get('checks_sha')}" for s in open_states]
        + list(reasons)
    )
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
    reasons = _wake_reasons(open_states)
    kill_switch_file = str((cfg.get("fleet", {}) or {}).get("kill_switch_file") or ".autonomous/STOP")
    kill_switch_paths = [
        root / kill_switch_file,
        Path.cwd() / kill_switch_file,
        root / ".autonomous" / "STOP",
    ]
    if any(path.exists() for path in kill_switch_paths):
        reasons.append("kill-switch-present (tend the queue only; do not generate)")
    digest = _wake_digest(open_states, reasons)

    wake_path = state_dir / "wake.json"
    previous: Dict[str, Any] = {}
    if wake_path.is_file():
        try:
            previous = json.loads(wake_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            eprint(f"[warn] wake state unreadable, treating as first wake: {wake_path}")
            previous = {}

    unchanged = bool(previous) and previous.get("digest") == digest and not args.force
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
                "digest": digest,
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
    can_dispatch = len(open_states) < max_open
    if can_dispatch:
        reasons.append("queue-headroom")
    return {
        "needs_brain": (not killed) and bool(reasons),
        "reasons": ["kill-switch"] if killed else reasons,
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
    lines = [
        "You are a FRESH autonomous-improvement Orchestrator wake. You have no",
        "memory beyond the files named here; that is by design. Work from them,",
        "leave state behind in them, and exit when this wake's duties are done.",
        "",
        f"State dir: {state_dir}",
        f"- Snapshot (just refreshed, deterministic): {state_dir / 'snapshot.json'}",
        f"- Journal (append your decisions + one state line per open PR): {state_dir / 'journal.md'}",
        f"- Ledger: {root / ledger}",
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
    lines += [
        "",
        "Duties, in order:",
        "1. Run `python3 scripts/autonomous/fleet.py doctor`. On any hard",
        "   failure, record it in the journal and STOP: blocked-with-reason",
        "   beats dispatching from a drifted deployment.",
        "2. Run `python3 scripts/autonomous/fleet.py govern --json` and obey it.",
        "3. Tend before generating: every red or owner-ask PR above gets fixed,",
        "   answered, or re-asked (references/04 CI green loop).",
        "4. Only if govern allows dispatch: pick work with",
        "   `ledger.py next --stream A --rank risk`, claim it with",
        "   `fleet.py claim` BEFORE working, and finish through evidence.py +",
        "   `fleet.py publish`. Never edit production code in a test PR.",
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
            launched = False
            for model in ladder:
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
    stream = normalize_stream(args.stream)
    target = rel_path(root, args.target)
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
    }
    if args.json:
        print(json.dumps(payload))
    else:
        print(f"[+] Claimed {target} as draft PR #{number} ({payload['url']})")
        print(f"    worktree: {worktree} · branch: {branch} · composite: {composite}")
    return EXIT_OK


def _render_evidence_body(data: Dict[str, Any]) -> str:
    runs = data["runs"]
    lines = ["## Summary", ""]
    if data["kind"] == "mutant":
        mutant = data["mutant"]
        lines.append(
            f"Kills the surviving mutant at `{mutant['file']}:{mutant['line']}`"
            + (f" ({mutant['description']})" if mutant.get("description") else "")
            + " with a test-only change."
        )
        lines += [
            "",
            "```diff",
            f"-{mutant['original']}",
            f"+{mutant['mutated']}",
            "```",
        ]
        red_label = "mutant applied"
    else:
        files = ", ".join(f"`{item['path']}`" for item in data.get("test_files") or [])
        lines.append(f"Fixes a defect reproduced on the unmodified base. Repro: {files}.")
        red_label = "unmodified base"
    lines += [
        "",
        "## Evidence (`.autonomous/evidence.json`, schema 1)",
        "",
        f"- Base: `{data.get('base_sha')}` ({data.get('base_ref')})",
        f"- Red ({red_label}): exit {runs['red']['exit']} — {runs['red'].get('summary', '')}",
        f"- Green (this branch): exit {runs['green']['exit']} — {runs['green'].get('summary', '')}",
        f"- Pass-to-pass: exit {runs['pass_to_pass']['exit']} — {runs['pass_to_pass'].get('summary', '')}",
        f"- Test cmd: `{data.get('test_cmd')}`",
        f"- Verdict: **{data.get('verdict')}**",
        "",
        "Rendered by `fleet.py publish` from the evidence artifact; "
        "re-validate with `python3 scripts/autonomous/evidence.py check`.",
    ]
    return "\n".join(lines) + "\n"


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


def _ai_policy_hit(root: Path) -> bool:
    """True when the repo's own docs forbid AI-generated contributions.

    Guarded import: conventions.py is a sibling script; when it is absent or
    unreadable the trust tier alone decides.
    """
    try:
        import conventions

        return bool(conventions.read_conventions(root).get("ai_policy", {}).get("found"))
    except Exception:  # missing helper, missing gh, unreadable docs: never crash publish
        return False


def _referee_incidents(root: Path) -> int:
    """Owner-maintained incident count (.autonomous/referee-incidents.txt)."""
    path = root / ".autonomous" / "referee-incidents.txt"
    if not path.is_file():
        return 0
    try:
        return int((path.read_text(encoding="utf-8").strip() or "0"))
    except ValueError:
        return 0


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


def _opencode_model_ids(warnings: List[str]) -> List[str]:
    """Model ids as `opencode models` prints them; [] with a warning when the
    CLI is unavailable. Never fabricates: an unavailable source is skipped."""
    try:
        proc = subprocess.run(
            ["opencode", "models"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        warnings.append("dynamic source 'opencode-models': the opencode CLI is unavailable; skipped")
        return []
    if proc.returncode != 0:
        warnings.append(f"dynamic source 'opencode-models': opencode models exited {proc.returncode}; skipped")
        return []
    ids = [line.strip() for line in proc.stdout.splitlines()]
    return [i for i in ids if i and re.fullmatch(r"[A-Za-z0-9._/:-]+", i)]


def _keep_matches(model_id: str, keep: Dict[str, Any]) -> bool:
    """Owner-configured keep rules for a dynamic source. A source without
    rules keeps NOTHING (conservative: an unfiltered source would silently
    widen the spend surface)."""
    if not isinstance(keep, dict) or not keep:
        return False
    if "provider" in keep and model_id.split("/")[0] != str(keep["provider"]):
        return False
    if "suffix" in keep and not model_id.endswith(str(keep["suffix"])):
        return False
    if "contains" in keep and str(keep["contains"]) not in model_id:
        return False
    return True


def _resolve_model_pool(cfg: Dict[str, Any]) -> Tuple[List[str], List[Dict[str, str]], List[str]]:
    """Resolve the recorded model pool into an ordered ladder (issue #103).

    The pool is an ALLOWLIST: rotation candidates come only from it, so a
    premium model the owner did not list is unreachable. Dynamic sources are
    mechanisms configured by the owner, never hardcoded lists. Returns
    (ordered ids, annotations, warnings); free tier first, then premium in
    listed order.
    """
    pool = ((cfg.get("fleet") or {}).get("model_pool") or {})
    if not isinstance(pool, dict):
        return [], [], ["fleet.model_pool is not an object; ignoring"]
    annotations: List[Dict[str, str]] = []
    warnings: List[str] = []
    free: List[str] = []
    premium: List[str] = []

    def add(entry_id: str, tier: str, source: str, note: str = "") -> None:
        if any(a["id"] == entry_id for a in annotations):
            return
        record = {"id": entry_id, "tier": tier, "source": source}
        if note:
            record["note"] = note
        annotations.append(record)
        (free if tier == "free" else premium).append(entry_id)

    for src in pool.get("dynamic") or []:
        if not isinstance(src, dict):
            warnings.append("model_pool dynamic entry is not an object; skipped")
            continue
        name = src.get("source")
        if name == "opencode-models":
            keep = src.get("keep") if isinstance(src.get("keep"), dict) else {}
            if not keep:
                warnings.append(
                    "dynamic source 'opencode-models' has no keep rules; it keeps nothing (configure fleet.model_pool.dynamic[].keep)"
                )
                continue
            for entry_id in _opencode_model_ids(warnings):
                if _keep_matches(entry_id, keep):
                    add(entry_id, "free", f"dynamic:{name}")
        else:
            warnings.append(f"unknown dynamic source {name!r}; skipped")

    for entry in pool.get("entries") or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            warnings.append("model_pool entry without an id; skipped")
            continue
        # A static entry without an explicit tier is premium: conservative,
        # least likely to be spent by accident.
        add(str(entry["id"]), str(entry.get("tier") or "premium"), "static", str(entry.get("note") or ""))

    return free + premium, annotations, warnings


def cmd_pool(args: argparse.Namespace) -> int:
    """Show the recorded model pool, or emit the rotation ladder (#103)."""
    root = main_repo_root()
    cfg = load_config(root, args.config)
    ordered, annotations, warnings = _resolve_model_pool(cfg)
    if args.action == "ladder":
        for warning in warnings:
            eprint(f"[warn] {warning}")
        if args.json:
            print(json.dumps({"schema": "model-pool/1", "ladder": ordered, "warnings": warnings}))
        else:
            for entry_id in ordered:
                print(entry_id)
        return EXIT_OK
    if args.json:
        print(json.dumps({"schema": "model-pool/1", "ladder": ordered, "entries": annotations, "warnings": warnings}, indent=2))
        return EXIT_OK
    if not annotations:
        eprint("model pool is empty: no dynamic sources and no entries are configured.")
        eprint(f"record the owner's accepted pool in {config_path(root, args.config)} under fleet.model_pool")
        eprint("(ask the owner on first deployment; premium models stay unreachable unless listed)")
    for record in annotations:
        line = f"{record['id']}  tier={record['tier']}  source={record['source']}"
        if record.get("note"):
            line += f"  note={record['note']}"
        eprint(line)
    for warning in warnings:
        eprint(f"[warn] {warning}")
    if annotations:
        eprint("ladder order: " + (" -> ".join(ordered) if ordered else "(empty)"))
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
    p_gov.add_argument("--json", action="store_true", help="emit JSON")
    p_gov.add_argument("--require-gh", action="store_true", help="fail if gh is unavailable")

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
    p_claim = subparsers.add_parser("claim", help="open a draft PR claiming a target")
    p_claim.add_argument("--config", help="path to config.json")
    p_claim.add_argument("--target", required=True, help="repo-relative target path")
    p_claim.add_argument(
        "--stream", required=True, help="available stream letter (1/2 alias A/B)"
    )
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
        if args.command == "needs-brain":
            return cmd_needs_brain(args)
        if args.command == "wake":
            return cmd_wake(args)
        if args.command == "doctor":
            return cmd_doctor(args)
        if args.command == "claim":
            return cmd_claim(args)
        if args.command == "publish":
            return cmd_publish(args)
        if args.command == "pool":
            return cmd_pool(args)
        raise UsageError(f"unknown command: {args.command}")
    except (UsageError, EnvError, Fatal) as exc:
        return format_exit(exc)


if __name__ == "__main__":
    sys.exit(main())
