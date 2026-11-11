#!/usr/bin/env python3
"""Master Cockpit: dashboard, persistent owner directives, and subagent worker registry.

Provides the deterministic state spine for an interactive Master Orchestrator
thread (and its coordination with background stateless wakes):
  * directive: persistent owner steering bus (.autonomous/directives.json);
  * worker: compact subagent lifecycle registry (.autonomous/workers.json);
  * dashboard: 0-token single-command overview of governor status, active
    directives, subagents/worktrees/run-records, open PR matrix, model pool
    health, and next backlog candidates.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common import (
    EXIT_OK,
    UsageError,
    eprint,
    load_run_records,
    load_worktree_registry,
    main_repo_root,
    run,
    write_json_atomic,
)
from fleet_doctor import _resolve_min_free_gb
from fleet_pool import _resolve_model_pool

DIRECTIVES_SCHEMA = "directives/1"
WORKERS_SCHEMA = "workers/1"
DASHBOARD_SCHEMA = "cockpit-dashboard/1"
WORKER_STATUSES = ("running", "completed", "failed", "blocked")
PRIORITIES = ("high", "normal", "low")


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Evidence markdown helper (shared with fleet.py publish)
# ---------------------------------------------------------------------------


def _ai_policy_hit(root: Path) -> bool:
    """True when the repo's own docs forbid AI-generated contributions."""
    try:
        import conventions

        return bool(conventions.read_conventions(root).get("ai_policy", {}).get("found"))
    except (ImportError, OSError, ValueError, RuntimeError):
        return False


def _referee_incidents(root: Path) -> int:
    """Owner-maintained incident count (.autonomous/referee-incidents.txt)."""
    path = root / ".autonomous" / "referee-incidents.txt"
    if not path.is_file():
        return 0
    try:
        return int(path.read_text(encoding="utf-8").strip() or "0")
    except ValueError:
        return 0


def _render_evidence_body(data: dict[str, Any]) -> str:
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
        (
            "Rendered by `fleet.py publish` from the evidence artifact; "
            "re-validate with `python3 scripts/autonomous/evidence.py check`."
        ),
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Directives Bus (.autonomous/directives.json)
# ---------------------------------------------------------------------------


def directives_path(root: Path) -> Path:
    return root / ".autonomous" / "directives.json"


def load_directives_doc(root: Path) -> dict[str, Any]:
    path = directives_path(root)
    if not path.is_file():
        return {"schema": DIRECTIVES_SCHEMA, "updated_at": None, "directives": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": DIRECTIVES_SCHEMA, "updated_at": None, "directives": []}
    if not isinstance(data, dict):
        return {"schema": DIRECTIVES_SCHEMA, "updated_at": None, "directives": []}
    items = data.get("directives")
    if not isinstance(items, list):
        items = []
    return {
        "schema": DIRECTIVES_SCHEMA,
        "updated_at": data.get("updated_at"),
        "directives": [d for d in items if isinstance(d, dict)],
    }


def save_directives_doc(root: Path, directives: list[dict[str, Any]]) -> dict[str, Any]:
    doc = {
        "schema": DIRECTIVES_SCHEMA,
        "updated_at": _now_iso(),
        "directives": directives,
    }
    write_json_atomic(directives_path(root), doc)
    return doc


def active_directives(root: Path) -> list[dict[str, Any]]:
    doc = load_directives_doc(root)
    return [d for d in doc["directives"] if d.get("status", "active") == "active"]


def active_directive_reasons(root: Path) -> list[str]:
    reasons: list[str] = []
    for item in active_directives(root):
        did = str(item.get("id") or "dir")
        text = str(item.get("text") or "")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        reasons.append(f"owner-directive:{did}:{digest}")
    return reasons


def format_directives_block(root: Path) -> list[str]:
    active = active_directives(root)
    if not active:
        return []
    lines = ["", "Owner directives (active — obey before default queue selection):"]
    for item in active:
        meta: list[str] = [str(item.get("priority") or "normal")]
        if item.get("stream"):
            meta.append(f"stream {item['stream']}")
        if item.get("target"):
            meta.append(f"target {item['target']}")
        lines.append(f"- [{item.get('id')}] ({', '.join(meta)}): {item.get('text')}")
    return lines


def _next_directive_id(directives: list[dict[str, Any]]) -> str:
    highest = 0
    for item in directives:
        match = re.match(r"^dir-(\d+)$", str(item.get("id") or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"dir-{highest + 1:04d}"


def cmd_directive(args: argparse.Namespace) -> int:
    root = main_repo_root()
    action = getattr(args, "directive_action", "list") or "list"
    doc = load_directives_doc(root)
    items = list(doc["directives"])

    if action in ("add", "set"):
        text = (getattr(args, "text", None) or "").strip()
        if not text:
            raise UsageError(f"directive {action} requires non-empty instruction text")
        priority = getattr(args, "priority", "normal") or "normal"
        if priority not in PRIORITIES:
            raise UsageError(f"invalid priority {priority!r}; choose from {PRIORITIES}")
        if action == "set":
            now = _now_iso()
            for existing in items:
                if existing.get("status", "active") == "active":
                    existing["status"] = "done"
                    existing["completed_at"] = now
        entry: dict[str, Any] = {
            "id": _next_directive_id(items),
            "ts": _now_iso(),
            "text": text,
            "priority": priority,
            "status": "active",
        }
        if getattr(args, "stream", None):
            entry["stream"] = str(args.stream).strip()
        if getattr(args, "target", None):
            entry["target"] = str(args.target).strip()
        items.append(entry)
        save_directives_doc(root, items)
        if getattr(args, "json", False):
            print(json.dumps({"action": action, "directive": entry, "active": active_directives(root)}, indent=2))
        else:
            print(f"[+] Directive {entry['id']} recorded ({action}): {entry['text']}")
        return EXIT_OK

    if action == "done":
        target_id = (getattr(args, "id", None) or "").strip()
        if not target_id:
            raise UsageError("directive done requires --id <dir-id>")
        matched = False
        now = _now_iso()
        for existing in items:
            if existing.get("id") == target_id:
                existing["status"] = "done"
                existing["completed_at"] = now
                matched = True
        if not matched:
            raise UsageError(f"directive not found: {target_id}")
        save_directives_doc(root, items)
        if getattr(args, "json", False):
            print(json.dumps({"action": "done", "id": target_id, "active": active_directives(root)}, indent=2))
        else:
            print(f"[+] Directive {target_id} marked done.")
        return EXIT_OK

    if action == "clear":
        now = _now_iso()
        cleared = 0
        for existing in items:
            if existing.get("status", "active") == "active":
                existing["status"] = "done"
                existing["completed_at"] = now
                cleared += 1
        save_directives_doc(root, items)
        if getattr(args, "json", False):
            print(json.dumps({"action": "clear", "cleared": cleared, "active": []}, indent=2))
        else:
            print(f"[+] Cleared {cleared} active directive(s).")
        return EXIT_OK

    # Default: list
    show_all = bool(getattr(args, "all", False))
    visible = items if show_all else [d for d in items if d.get("status", "active") == "active"]
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "schema": DIRECTIVES_SCHEMA,
                    "updated_at": doc.get("updated_at"),
                    "directives": visible,
                },
                indent=2,
            )
        )
        return EXIT_OK
    if not visible:
        print("[*] No active owner directives.")
        return EXIT_OK
    print("## Owner Directives")
    for item in visible:
        status = item.get("status", "active")
        meta = [str(item.get("priority") or "normal"), status]
        if item.get("stream"):
            meta.append(f"stream={item['stream']}")
        if item.get("target"):
            meta.append(f"target={item['target']}")
        print(f"- **{item.get('id')}** ({', '.join(meta)}): {item.get('text')}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Subagent Worker Registry (.autonomous/workers.json)
# ---------------------------------------------------------------------------


def workers_path(root: Path) -> Path:
    return root / ".autonomous" / "workers.json"


def load_workers_doc(root: Path) -> dict[str, Any]:
    path = workers_path(root)
    if not path.is_file():
        return {"schema": WORKERS_SCHEMA, "updated_at": None, "workers": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": WORKERS_SCHEMA, "updated_at": None, "workers": []}
    if not isinstance(data, dict):
        return {"schema": WORKERS_SCHEMA, "updated_at": None, "workers": []}
    workers = data.get("workers")
    if not isinstance(workers, list):
        workers = []
    return {
        "schema": WORKERS_SCHEMA,
        "updated_at": data.get("updated_at"),
        "workers": [w for w in workers if isinstance(w, dict)],
    }


def save_workers_doc(root: Path, workers: list[dict[str, Any]]) -> dict[str, Any]:
    doc = {
        "schema": WORKERS_SCHEMA,
        "updated_at": _now_iso(),
        "workers": workers,
    }
    write_json_atomic(workers_path(root), doc)
    return doc


def cmd_worker(args: argparse.Namespace) -> int:
    root = main_repo_root()
    action = getattr(args, "worker_action", "list") or "list"
    doc = load_workers_doc(root)
    workers = list(doc["workers"])

    if action in ("register", "update"):
        wid = (getattr(args, "id", None) or "").strip()
        if not wid:
            raise UsageError(f"worker {action} requires --id <worker-or-session-id>")
        status = getattr(args, "status", None) or ("running" if action == "register" else None)
        if status and status not in WORKER_STATUSES:
            raise UsageError(f"invalid worker status {status!r}; choose from {WORKER_STATUSES}")
        now = _now_iso()
        existing: dict[str, Any] | None = None
        for item in workers:
            if item.get("id") == wid:
                existing = item
                break
        if existing is None:
            existing = {
                "id": wid,
                "status": status or "running",
                "started_at": now,
                "updated_at": now,
            }
            workers.append(existing)
        else:
            existing["updated_at"] = now
            if status:
                existing["status"] = status
        for field in ("target", "stream", "slug", "model", "summary", "url"):
            val = getattr(args, field, None)
            if val is not None:
                existing[field] = str(val).strip()
        if getattr(args, "pr", None) is not None:
            existing["pr"] = int(args.pr)
        if existing.get("status") in ("completed", "failed", "blocked"):
            existing["finished_at"] = now
        save_workers_doc(root, workers)
        if getattr(args, "json", False):
            print(json.dumps({"action": action, "worker": existing}, indent=2))
        else:
            print(f"[+] Worker {wid} ({existing['status']}): {existing.get('target') or existing.get('slug') or '(no target)'}")
        return EXIT_OK

    if action == "prune":
        keep = max(0, int(getattr(args, "keep", 20) or 20))
        running = [w for w in workers if w.get("status") == "running"]
        terminal = [w for w in workers if w.get("status") != "running"]
        kept_terminal = terminal[-keep:] if keep > 0 else []
        pruned_count = len(terminal) - len(kept_terminal)
        new_workers = running + kept_terminal
        save_workers_doc(root, new_workers)
        if getattr(args, "json", False):
            print(json.dumps({"action": "prune", "pruned": pruned_count, "remaining": len(new_workers)}, indent=2))
        else:
            print(f"[+] Pruned {pruned_count} terminal worker record(s); {len(new_workers)} remaining.")
        return EXIT_OK

    # Default: list
    filter_status = getattr(args, "status", None)
    limit = int(getattr(args, "limit", 20) or 20)
    filtered = [
        w for w in workers if not filter_status or w.get("status") == filter_status
    ]
    if limit > 0:
        filtered = filtered[-limit:]
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "schema": WORKERS_SCHEMA,
                    "updated_at": doc.get("updated_at"),
                    "workers": filtered,
                },
                indent=2,
            )
        )
        return EXIT_OK
    if not filtered:
        print("[*] No tracked subagent workers.")
        return EXIT_OK
    print("## Subagent Workers")
    for w in reversed(filtered):
        pr_str = f" · PR #{w['pr']}" if w.get("pr") else ""
        model_str = f" · {w['model']}" if w.get("model") else ""
        summary_str = f" — {w['summary']}" if w.get("summary") else ""
        target_str = w.get("target") or w.get("slug") or "(unassigned)"
        print(f"- `{w.get('id')}` [{w.get('status')}] `{target_str}`{model_str}{pr_str}{summary_str}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Master Cockpit Dashboard (`fleet.py dashboard`)
# ---------------------------------------------------------------------------


def _load_wake_failures_cooling(state_dir: Path, cooldown_sec: int) -> set[str]:
    """Also read stateless-wake.sh's wake_failures file so both wake shapes show up."""
    cooling: set[str] = set()
    now = datetime.datetime.now(datetime.UTC).timestamp()
    for fname in ("model_failures", "wake_failures"):
        fpath = state_dir / fname
        if not fpath.is_file():
            continue
        try:
            for line in fpath.read_text(encoding="utf-8").splitlines():
                parts = line.strip().split()
                if len(parts) >= 2:
                    try:
                        if now - float(parts[1]) < cooldown_sec:
                            cooling.add(parts[0])
                    except ValueError:
                        continue
        except OSError:
            pass
    return cooling
def _next_candidates_preview(
    root: Path,
    cfg: dict[str, Any],
    claimed_targets: set[str],
    limit: int = 3,
) -> list[dict[str, str]]:
    """Preview next available Stream A/B candidates without spending tokens."""
    previews: list[dict[str, str]] = []
    try:
        import ledger as ledger_mod

        ledger_file = ledger_mod._ledger_path(root, cfg, None)
        audited = ledger_mod.authoritative_rows(ledger_file)
        for stream_code in ("A", "B"):
            if len(previews) >= limit:
                break
            records = ledger_mod._candidate_records(root, cfg, stream_code, audited)
            for cand in records:
                if cand.get("skip") or cand.get("audited"):
                    continue
                target = str(cand.get("target") or "")
                if not target or target in claimed_targets:
                    continue
                previews.append(
                    {
                        "stream": stream_code,
                        "target": target,
                        "reason": "reaudit" if cand.get("last_hash") else "never audited",
                    }
                )
                if len(previews) >= limit:
                    break
    except (ImportError, OSError, ValueError, KeyError, RuntimeError) as exc:
        eprint(f"[debug] candidate preview skipped: {exc}")
    return previews


def list_git_worktrees(root: Path) -> list[dict[str, Any]]:
    run(["git", "worktree", "prune"], cwd=root, quiet=True)
    proc = run(["git", "worktree", "list", "--porcelain"], cwd=root, quiet=True)
    if proc.returncode != 0:
        return []
    worktrees: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in (proc.stdout or "").splitlines():
        if not line.strip():
            if current.get("path"):
                worktrees.append(current)
            current = {}
            continue
        if line.startswith("worktree "):
            current["path"] = line[len("worktree ") :].strip()
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD ") :].strip()[:9]
        elif line.startswith("branch "):
            ref = line[len("branch ") :].strip()
            current["branch"] = ref.removeprefix("refs/heads/")
        elif line.strip() == "detached":
            current["branch"] = "(detached)"
    if current.get("path"):
        worktrees.append(current)
    for wt in worktrees:
        wpath = Path(wt["path"])
        if wpath.is_dir():
            st = run(["git", "status", "--porcelain"], cwd=wpath, quiet=True)
            wt["dirty"] = bool(st.returncode == 0 and st.stdout.strip())
        else:
            wt["dirty"] = False
    return worktrees


def build_dashboard_payload(
    root: Path,
    cfg: dict[str, Any],
    state_dir: Path,
    fetch_prs_fn: Callable[..., tuple[list[dict[str, Any]], str | None]],
    evaluate_pr_fn: Callable[[dict[str, Any]], dict[str, Any]],
    load_cooling_fn: Callable[..., dict[str, int]],
    require_gh: bool = False,
    limit: int = 10,
) -> dict[str, Any]:
    fleet_cfg = cfg.get("fleet", {}) or {}
    max_open_prs = int(fleet_cfg.get("max_open_prs", 5))
    max_new_24h = int(fleet_cfg.get("max_new_prs_per_day", 6))
    max_worktrees = int(fleet_cfg.get("max_worktrees", 5))
    kill_switch_file = str(fleet_cfg.get("kill_switch_file") or ".autonomous/STOP")
    killed = (root / kill_switch_file).exists() or (root / ".autonomous" / "STOP").exists()
    cb_tripped = (state_dir / "CIRCUIT_BREAKER").is_file()

    min_free_gb = _resolve_min_free_gb(cfg)
    try:
        free_gb = round(shutil.disk_usage(str(root)).free / (1024**3), 2)
    except OSError:
        free_gb = min_free_gb
    disk_exhausted = free_gb < min_free_gb

    prs_data, gh_warn = fetch_prs_fn(root, state="open", require_gh=require_gh)
    if gh_warn:
        eprint(f"[warn] {gh_warn}")
    open_states = [evaluate_pr_fn(p) for p in prs_data]

    # Enrich open_states with title and isDraft from raw prs_data
    pr_meta_by_num: dict[int, dict[str, Any]] = {}
    claimed_targets: set[str] = set()
    for p in prs_data:
        num = p.get("number")
        if isinstance(num, int):
            pr_meta_by_num[num] = {
                "title": str(p.get("title") or ""),
                "isDraft": bool(p.get("isDraft", False)),
                "headRefName": str(p.get("headRefName") or ""),
            }
        body = str(p.get("body") or "")
        for match in re.finditer(r"Target:\s*`([^`]+)`", body):
            claimed_targets.add(match.group(1).strip())

    enriched_prs: list[dict[str, Any]] = []
    green_ready: list[int] = []
    red_tending: list[int] = []
    pending_prs: list[int] = []
    owner_ask_prs: list[int] = []
    draft_prs: list[int] = []

    for state in open_states:
        num = state.get("pr")
        meta = pr_meta_by_num.get(num, {}) if isinstance(num, int) else {}
        is_draft = bool(meta.get("isDraft", False))
        checks = state.get("checks") or "pending"
        owner_ask = bool(state.get("owner_ask_pending", False))
        if isinstance(num, int):
            if is_draft:
                draft_prs.append(num)
            if checks == "green" and not is_draft and not owner_ask:
                green_ready.append(num)
            elif checks == "red":
                red_tending.append(num)
            elif checks == "pending":
                pending_prs.append(num)
            if owner_ask:
                owner_ask_prs.append(num)
        enriched_prs.append(
            {
                **state,
                "title": meta.get("title", ""),
                "branch": meta.get("headRefName", ""),
                "is_draft": is_draft,
            }
        )

    worktrees = list_git_worktrees(root)
    wt_registry = load_worktree_registry(root)

    # Governor summary
    block_reasons: list[str] = []
    if killed:
        block_reasons.append(f"kill-switch active ({kill_switch_file})")
    if cb_tripped:
        block_reasons.append("wake circuit breaker tripped")
    if disk_exhausted:
        block_reasons.append(f"disk headroom exhausted ({free_gb:.1f} GiB < {min_free_gb:.1f} GiB)")
    if len(worktrees) > max_worktrees:
        block_reasons.append(f"worktree cap exceeded ({len(worktrees)} > {max_worktrees})")
    if len(open_states) >= max_open_prs:
        block_reasons.append(f"open PR cap reached ({len(open_states)} >= {max_open_prs})")

    governor = {
        "dispatch": len(block_reasons) == 0,
        "reasons": block_reasons if block_reasons else ["within limits"],
        "open_prs": len(open_states),
        "max_open_prs": max_open_prs,
        "max_new_prs_per_day": max_new_24h,
        "worktrees": len(worktrees),
        "max_worktrees": max_worktrees,
        "kill_switch": killed,
        "circuit_breaker": cb_tripped,
        "free_disk_gb": free_gb,
        "tend_queue": sorted(set(red_tending + owner_ask_prs)),
    }

    # Directives
    directives = active_directives(root)

    # Workers & recent run records
    workers_doc = load_workers_doc(root)
    all_workers = workers_doc.get("workers") or []
    for w in all_workers:
        if w.get("status") == "running" and w.get("target"):
            claimed_targets.add(str(w["target"]))
    recent_workers = all_workers[-limit:] if limit > 0 else all_workers

    records = load_run_records(root / ".autonomous" / "run-records")
    recent_records = [
        {
            "id": r.get("id"),
            "ts": r.get("ts"),
            "outcome": r.get("outcome"),
            "chosen": r.get("chosen"),
            "model": r.get("model"),
            "pr_url": (r.get("Mutable_Result") or {}).get("pr_url") or r.get("pr_url"),
            "duration_s": r.get("duration_s") or r.get("wall_s"),
        }
        for r in (records[-min(limit, 5) :] if limit > 0 else records[-5:])
    ]

    # Model pool health
    cooldown_sec = int(
        fleet_cfg.get("model_cooldown")
        or os.environ.get("ORCH_MODEL_COOLDOWN", "3600")
    )
    cooling = set(load_cooling_fn(state_dir, cooldown_sec=cooldown_sec)) | _load_wake_failures_cooling(
        state_dir, cooldown_sec
    )
    ladder, annotations, pool_warnings = _resolve_model_pool(cfg)
    models_status: list[dict[str, Any]] = []
    for item in annotations:
        mid = item["id"]
        models_status.append(
            {
                "id": mid,
                "tier": item.get("tier", "free"),
                "source": item.get("source", "static"),
                "status": "cooling" if mid in cooling else "live",
            }
        )

    next_candidates = _next_candidates_preview(root, cfg, claimed_targets, limit=3)

    return {
        "schema": DASHBOARD_SCHEMA,
        "generated_at": _now_iso(),
        "repo_root": str(root),
        "state_dir": str(state_dir),
        "governor": governor,
        "directives": directives,
        "pr_rollup": {
            "total_open": len(open_states),
            "green_ready": sorted(green_ready),
            "red_tending": sorted(red_tending),
            "pending": sorted(pending_prs),
            "owner_ask": sorted(owner_ask_prs),
            "draft": sorted(draft_prs),
        },
        "open_prs": enriched_prs,
        "workers": recent_workers,
        "worktrees": worktrees,
        "registered_worktrees": wt_registry,
        "recent_runs": recent_records,
        "model_pool": {
            "ladder": ladder,
            "models": models_status,
            "cooling_count": sum(1 for m in models_status if m["status"] == "cooling"),
            "live_count": sum(1 for m in models_status if m["status"] == "live"),
            "warnings": pool_warnings,
        },
        "next_candidates": next_candidates,
    }


def render_dashboard_markdown(payload: dict[str, Any]) -> str:
    gov = payload["governor"]
    rollup = payload["pr_rollup"]
    status_badge = "ALLOWED" if gov["dispatch"] else "BLOCKED"
    lines = [
        "# Autonomous Improvement — Master Cockpit",
        "",
        f"- **Dispatch**: **{status_badge}** ({'; '.join(gov['reasons'])})",
        (
            f"- **Open PRs**: {gov['open_prs']}/{gov['max_open_prs']} "
            f"(green: {len(rollup['green_ready'])}, red: {len(rollup['red_tending'])}, "
            f"pending: {len(rollup['pending'])}, owner-ask: {len(rollup['owner_ask'])}, draft: {len(rollup['draft'])})"
        ),
        f"- **Worktrees**: {gov['worktrees']}/{gov['max_worktrees']} · **Disk Free**: {gov['free_disk_gb']} GiB",
    ]
    if gov["tend_queue"]:
        lines.append(f"- **Tend Queue**: {', '.join(f'PR #{n}' for n in gov['tend_queue'])}")

    # 1. Directives
    lines.append("")
    lines.append("## Active Owner Directives")
    directives = payload.get("directives") or []
    if not directives:
        lines.append("- *(none — use `fleet.py directive add \"<instruction>\"` to steer the fleet)*")
    else:
        for d in directives:
            meta = [str(d.get("priority") or "normal")]
            if d.get("stream"):
                meta.append(f"stream {d['stream']}")
            if d.get("target"):
                meta.append(f"target `{d['target']}`")
            lines.append(f"- **{d.get('id')}** ({', '.join(meta)}): {d.get('text')}")

    # 2. Subagent Workers & Worktrees
    lines.append("")
    lines.append("## Subagent Workers & Active Worktrees")
    workers = payload.get("workers") or []
    if workers:
        lines.append("| Worker / Session | Status | Target / Slug | Model | PR | Summary |")
        lines.append("|---|---|---|---|---|---|")
        for w in reversed(workers):
            pr_cell = f"#{w['pr']}" if w.get("pr") else (w.get("url") or "-")
            target_cell = w.get("target") or w.get("slug") or "-"
            lines.append(
                f"| `{w.get('id')}` | {w.get('status')} | `{target_cell}` | "
                f"{w.get('model') or '-'} | {pr_cell} | {w.get('summary') or '-'} |"
            )
    else:
        lines.append("- No subagent workers registered in `.autonomous/workers.json`.")

    non_main_wts = [w for w in (payload.get("worktrees") or []) if w.get("branch") not in ("main", "master")]
    if non_main_wts:
        lines.append("")
        lines.append("**Linked Git Worktrees**:")
        for wt in non_main_wts:
            dirty_tag = "dirty" if wt.get("dirty") else "clean"
            lines.append(f"- `{wt.get('path')}` (`{wt.get('branch')}`, {dirty_tag})")

    recent_runs = payload.get("recent_runs") or []
    if recent_runs:
        lines.append("")
        lines.append("**Recent Run Records**:")
        for r in reversed(recent_runs):
            dur = f" ({r['duration_s']}s)" if r.get("duration_s") is not None else ""
            chosen = f" · `{r['chosen']}`" if r.get("chosen") else ""
            model = f" · {r['model']}" if r.get("model") else ""
            pr_url = f" · {r['pr_url']}" if r.get("pr_url") else ""
            lines.append(f"- `{r.get('id')}` [{r.get('outcome')}]{chosen}{model}{dur}{pr_url}")

    # 3. Open PR Matrix
    lines.append("")
    lines.append("## Open PR Matrix")
    open_prs = payload.get("open_prs") or []
    if not open_prs:
        lines.append("- *(no open PRs)*")
    else:
        lines.append("| PR | Checks | Draft | Owner Ask | Iter | Title |")
        lines.append("|---|---|---|---|---|---|")
        for pr in open_prs:
            checks = str(pr.get("checks") or "pending")
            if pr.get("red_kind"):
                checks += f" [{pr['red_kind']}]"
            draft = "yes" if pr.get("is_draft") else "no"
            ask = "yes" if pr.get("owner_ask_pending") else "no"
            title = str(pr.get("title") or "").replace("|", "\\|")
            lines.append(
                f"| #{pr.get('pr')} | {checks} | {draft} | {ask} | {pr.get('iterations', 1)} | {title} |"
            )

    # 4. Model Pool
    lines.append("")
    lines.append("## Model Pool Status")
    pool = payload.get("model_pool") or {}
    models = pool.get("models") or []
    if not models:
        lines.append("- *(no model pool configured in `fleet.model_pool`)*")
    else:
        lines.append(
            f"- **Live**: {pool.get('live_count', 0)} · **Cooling**: {pool.get('cooling_count', 0)}"
        )
        live_ids = [m["id"] for m in models if m["status"] == "live"]
        cooling_ids = [m["id"] for m in models if m["status"] == "cooling"]
        if live_ids:
            lines.append(f"- **Ready ladder**: {', '.join(f'`{m}`' for m in live_ids[:6])}"
                         + (f" (+{len(live_ids) - 6} more)" if len(live_ids) > 6 else ""))
        if cooling_ids:
            lines.append(f"- **In cooldown**: {', '.join(f'`{m}`' for m in cooling_ids)}")

    # 5. Next Backlog Candidates
    lines.append("")
    lines.append("## Next Unclaimed Candidates")
    cands = payload.get("next_candidates") or []
    if not cands:
        lines.append("- *(no unclaimed Stream A/B candidates found or candidate globs unconfigured)*")
    else:
        for c in cands:
            lines.append(f"- **Stream {c['stream']}**: `{c['target']}` ({c['reason']})")

    return "\n".join(lines) + "\n"


def register_cockpit_subparsers(subparsers: Any) -> None:
    # dashboard
    p_dash = subparsers.add_parser(
        "dashboard",
        help="0-token Master Cockpit overview (governor, directives, workers, PRs, pool, queue)",
    )
    p_dash.add_argument("--config", help="path to config.json")
    p_dash.add_argument("--state-dir", help="directory for fleet state")
    p_dash.add_argument("--limit", type=int, default=10, help="max workers/runs to display (default: 10)")
    p_dash.add_argument("--require-gh", action="store_true", help="fail if gh is unavailable")
    p_dash.add_argument("--json", action="store_true", help="emit JSON")

    # directive
    p_dir = subparsers.add_parser(
        "directive",
        help="manage persistent owner steering directives (.autonomous/directives.json)",
    )
    p_dir.add_argument(
        "directive_action",
        choices=("list", "add", "set", "done", "clear"),
        nargs="?",
        default="list",
    )
    p_dir.add_argument("text", nargs="?", help="directive instruction text (for add/set)")
    p_dir.add_argument("--id", help="directive id (for done)")
    p_dir.add_argument("--priority", choices=PRIORITIES, default="normal", help="directive priority")
    p_dir.add_argument("--stream", help="optional stream letter filter (e.g. A, B)")
    p_dir.add_argument("--target", help="optional target path or PR reference")
    p_dir.add_argument("--all", action="store_true", help="include completed directives in list")
    p_dir.add_argument("--config", help="path to config.json")
    p_dir.add_argument("--json", action="store_true", help="emit JSON")

    # worker
    p_wrk = subparsers.add_parser(
        "worker",
        help="track spawned subagent workers (.autonomous/workers.json)",
    )
    p_wrk.add_argument(
        "worker_action",
        choices=("list", "register", "update", "prune"),
        nargs="?",
        default="list",
    )
    p_wrk.add_argument("--id", help="worker or subagent session id")
    p_wrk.add_argument("--status", choices=WORKER_STATUSES, help="worker lifecycle status")
    p_wrk.add_argument("--target", help="repo-relative target path")
    p_wrk.add_argument("--stream", help="stream letter")
    p_wrk.add_argument("--slug", help="worktree slug")
    p_wrk.add_argument("--model", help="model id used by the worker")
    p_wrk.add_argument("--pr", type=int, help="PR number opened or updated")
    p_wrk.add_argument("--url", help="PR or issue URL")
    p_wrk.add_argument("--summary", help="compact 1-line completion or progress summary")
    p_wrk.add_argument("--limit", type=int, default=20, help="max workers to list (default: 20)")
    p_wrk.add_argument("--keep", type=int, default=20, help="terminal workers to keep on prune (default: 20)")
    p_wrk.add_argument("--config", help="path to config.json")
    p_wrk.add_argument("--json", action="store_true", help="emit JSON")
