#!/usr/bin/env python3
"""Fleet state accumulator, PR state classifier, and governor.

Deterministic state spine for autonomous-improvement runs:
  * snapshot: records main SHA, open PR statuses, and worktree dirty counts to disk (0 tokens);
  * pr-state: emits references/04 state line per PR with head-sha-vs-run-sha verification;
  * govern: checks kill-switch, WIP PR caps, and acceptance rate floors before dispatch;
  * stats: computes acceptance rates, CI iterations, and renders weekly owner digest.

Usage:
  python3 scripts/autonomous/fleet.py snapshot [--state-dir <dir>]
  python3 scripts/autonomous/fleet.py pr-state [--json] [--pr <num>]
  python3 scripts/autonomous/fleet.py govern [--json]
  python3 scripts/autonomous/fleet.py stats [--json] [--days <N>]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    EXIT_NEGATIVE,
    EXIT_OK,
    EXIT_USAGE,
    EnvError,
    Fatal,
    UsageError,
    eprint,
    format_exit,
    git,
    load_config,
    load_worktree_registry,
    main_repo_root,
    run,
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

    if args.json:
        print(json.dumps(stats_dict, indent=2))
        return EXIT_OK

    print(f"## Fleet Statistics ({'All-time' if days == 0 else f'Last {days} Days'})")
    print(
        f"- **Acceptance Rate**: {acceptance_rate:.1%} "
        f"({total_merged} merged, {total_closed_unmerged} closed-unmerged)"
    )
    print(f"- **Total PRs Opened**: {total_opened}")
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

    # govern
    p_gov = subparsers.add_parser("govern", help="evaluate governor dispatch limits")
    p_gov.add_argument("--config", help="path to config.json")
    p_gov.add_argument("--json", action="store_true", help="emit JSON")
    p_gov.add_argument("--require-gh", action="store_true", help="fail if gh is unavailable")

    # stats
    p_stats = subparsers.add_parser("stats", help="compute fleet acceptance and metrics")
    p_stats.add_argument("--config", help="path to config.json")
    p_stats.add_argument("--days", type=int, default=14, help="lookback window in days (default: 14, 0 = all)")
    p_stats.add_argument("--json", action="store_true", help="emit JSON")
    p_stats.add_argument("--require-gh", action="store_true", help="fail if gh is unavailable")

    args = parser.parse_args(argv)

    try:
        if args.command == "snapshot":
            return cmd_snapshot(args)
        if args.command == "pr-state":
            return cmd_pr_state(args)
        if args.command == "govern":
            return cmd_govern(args)
        if args.command == "stats":
            return cmd_stats(args)
        raise UsageError(f"unknown command: {args.command}")
    except (UsageError, EnvError, Fatal) as exc:
        return format_exit(exc)


if __name__ == "__main__":
    sys.exit(main())
