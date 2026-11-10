#!/usr/bin/env python3
"""Read-only blast-radius preflight doctor checks for fleet.py."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import (
    EXIT_NEGATIVE,
    EXIT_OK,
    eprint,
    load_config,
    load_worktree_registry,
    main_repo_root,
    run,
    state_dir_candidates,
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


def _resolve_min_free_gb(
    cfg: Dict[str, Any], doctor_cfg: Optional[Dict[str, Any]] = None
) -> float:
    """Resolve disk headroom floor: config takes precedence, then ORCH_MIN_FREE_GB, default 5 GiB."""
    fleet_cfg = cfg.get("fleet", {}) or {}
    explicit = fleet_cfg.get("min_free_gb")
    if explicit is None:
        doc = doctor_cfg if doctor_cfg is not None else (cfg.get("doctor", {}) or {})
        explicit = doc.get("min_free_gb")
    if explicit is not None:
        try:
            return float(explicit)
        except (ValueError, TypeError):
            pass
    env_val = os.environ.get("ORCH_MIN_FREE_GB")
    if env_val is not None:
        try:
            return float(env_val)
        except ValueError:
            pass
    return 5.0


def _doctor_check(name: str, severity: str, ok: bool, detail: str) -> Dict[str, Any]:
    return {"name": name, "severity": severity, "ok": bool(ok), "detail": detail}


def _journal_age(path: Path) -> str:
    """``journal YYYY-MM-DD`` or ``no journal`` — the tie-breaker a human reads."""
    journal = path / "journal.md"
    if not journal.is_file():
        return "no journal"
    return "journal " + time.strftime(
        "%Y-%m-%d", time.localtime(journal.stat().st_mtime)
    )


def _doctor_run(
    argv: List[str], timeout: int = 20
) -> Optional[subprocess.CompletedProcess]:
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

    # 3. Branch protection on the base branch
    branch = str(cfg.get("base_ref") or "origin/main").split("/")[-1]
    slug = _origin_repo_slug(root)
    if slug:
        prot = _doctor_run(
            ["gh", "api", f"repos/{slug}/branches/{branch}/protection"], timeout=20
        )
        prot_ok = bool(
            prot and prot.returncode == 0 and "required" in (prot.stdout or "")
        )
        require = bool(
            doctor_cfg.get("require_branch_protection", merge_policy == "never")
        )
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
    linger = _doctor_run(
        ["loginctl", "show-user", os_user, "-p", "Linger", "--value"], timeout=10
    )
    linger_value = (linger.stdout or "").strip().lower() if linger else ""
    checks.append(
        _doctor_check(
            "linger",
            "hard",
            linger_value == "yes",
            (
                f"loginctl reports Linger={linger_value or 'unknown'} for {os_user}"
                + (
                    ""
                    if linger_value == "yes"
                    else "; enable it (loginctl enable-linger) or timers die with the session"
                )
            ),
        )
    )

    # 5. Watchdog timer active
    timer = _doctor_run(
        ["systemctl", "--user", "is-active", "orchestrator-watchdog.timer"], timeout=10
    )
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

    # 5b. Environment profile vs live keep-alive machinery
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

    # 5c. Keep-alive consent (issue #91)
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

    # 6a. Candidate state dirs (issue #93)
    candidates = state_dir_candidates()
    if os.environ.get("ORCH_STATE_DIR"):
        checks.append(
            _doctor_check(
                "state-dir-candidates",
                "soft",
                True,
                "explicit state dir configured via ORCH_STATE_DIR; candidate scan skipped",
            )
        )
    elif len(candidates) > 1:
        described = "; ".join(f"{path} ({_journal_age(path)})" for path in candidates)
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
        checks.append(
            _doctor_check(
                "state-dir",
                "soft",
                True,
                "stateless wake configured; no session state required",
            )
        )
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
    min_free_gb = _resolve_min_free_gb(cfg, doctor_cfg)
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
            checks.append(
                _doctor_check(
                    "clock-skew", "soft", True, "clock reference unreadable; skipped"
                )
            )
    else:
        checks.append(
            _doctor_check(
                "clock-skew",
                "soft",
                True,
                "no clock reference yet (the snapshot job writes one)",
            )
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
            "no orphan worktrees"
            if not orphans
            else f"orphan worktree path(s): {', '.join(sorted(set(orphans)))}",
        )
    )

    # 10. Model ladder shape
    ladder_raw = (
        os.environ.get("ORCH_MODEL_LADDER") or fleet_cfg.get("model_ladder") or ""
    )
    if isinstance(ladder_raw, (list, tuple)):
        ladder = [str(item) for item in ladder_raw]
    else:
        ladder = [item for item in str(ladder_raw).split() if item]
    problems: List[str] = []
    if len(set(ladder)) != len(ladder):
        problems.append("duplicate entries")
    problems.extend(
        f"{entry!r} is not provider/model shaped"
        for entry in ladder
        if "/" not in entry
    )
    checks.append(
        _doctor_check(
            "model-ladder",
            "hard",
            not problems,
            (
                "ladder shape ok"
                if not problems and ladder
                else (
                    "; ".join(problems)
                    if problems
                    else "no ladder configured (session default only)"
                )
            ),
        )
    )

    # 11. Instruction hierarchy is declared
    docs = root / "fleet-ops" / "references" / "05-unattended-operation.md"
    skill_docs = (
        Path(__file__).resolve().parent.parent.parent
        / "fleet-ops"
        / "references"
        / "05-unattended-operation.md"
    )
    target = docs if docs.exists() else skill_docs
    hierarchy_ok = False
    if target.exists():
        hierarchy_ok = (
            "never instructions"
            in target.read_text(encoding="utf-8", errors="ignore").lower()
        )
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
            mark = (
                "ok"
                if check["ok"]
                else ("FAIL" if check["severity"] == "hard" else "warn")
            )
            print(f"[{mark:4}] {check['name']}: {check['detail']}")
        if summary["dispatch_ok"]:
            print("[+] Doctor: dispatch OK (no hard failures)")
        else:
            eprint(f"[!] Doctor: DO NOT DISPATCH — {summary['reason']}")
    return EXIT_OK if summary["dispatch_ok"] else EXIT_NEGATIVE
