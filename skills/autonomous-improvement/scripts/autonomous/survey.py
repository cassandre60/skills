#!/usr/bin/env python3
"""Autonomous survey oracle (Phase 3): rank improvement passes from 0-token signals.

Derives a ranked survey of candidate tools before reaching for heavy AST or LLM passes.
Schema: survey/1.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# Ensure scripts/autonomous is on sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from common import (
        DEFAULT_CONFIG,
        EXIT_OK,
        STREAMS,
        load_config,
        repo_root,
    )
except ImportError:
    DEFAULT_CONFIG = {}
    EXIT_OK = 0
    STREAMS = {}

    def repo_root(cwd: Path | None = None) -> Path:
        return Path(cwd or Path.cwd()).resolve()

    def load_config(root: Path, path: Path | None = None) -> dict[str, Any]:
        return {}


SCHEMA = "survey/1"
THRESHOLD = 0.25

DEFAULT_WEIGHTS: dict[str, float] = {
    "defect-repair": 1.0,
    "security": 0.9,
    "docs-reconcile": 0.8,
    "flake": 0.7,
    "adversarial": 0.6,
    "mutation": 0.5,
    "simplification": 0.3,
    "fuzz": 0.3,
    "performance": 0.0,
}

TOOL_METADATA: dict[str, dict[str, Any]] = {
    "defect-repair": {
        "stream": "E",
        "finder_cmd": ["python3", "scripts/autonomous/repair.py", "intake"],
        "oracle_cmd": ["python3", "scripts/autonomous/repair.py", "verify-red"],
        "tier": "T3",
        "needs_config": False,
    },
    "security": {
        "stream": "H",
        "finder_cmd": ["python3", "scripts/autonomous/advisory.py", "secrets"],
        "oracle_cmd": ["python3", "scripts/autonomous/advisory.py", "secrets"],
        "tier": "T2",
        "needs_config": False,
    },
    "docs-reconcile": {
        "stream": "I",
        "finder_cmd": ["python3", "scripts/autonomous/docs_truth.py", "check"],
        "oracle_cmd": ["python3", "scripts/autonomous/docs_truth.py", "check"],
        "tier": "T3",
        "needs_config": False,
    },
    "flake": {
        "stream": "F",
        "finder_cmd": ["python3", "scripts/autonomous/flakes.py", "finder"],
        "oracle_cmd": ["python3", "scripts/autonomous/flakes.py", "oracle"],
        "tier": "T2",
        "needs_config": False,
    },
    "adversarial": {
        "stream": "B",
        "finder_cmd": ["python3", "scripts/autonomous/stream_b_scan.py"],
        "oracle_cmd": ["python3", "scripts/autonomous/orchestrator.py", "verify-gate"],
        "tier": "T3",
        "needs_config": False,
    },
    "mutation": {
        "stream": "A",
        "finder_cmd": ["python3", "scripts/autonomous/mutation_engine.py"],
        "oracle_cmd": ["python3", "scripts/autonomous/evidence.py", "mutant"],
        "tier": "T3",
        "needs_config": True,
    },
    "simplification": {
        "stream": "J",
        "finder_cmd": ["python3", "scripts/autonomous/simplify_gate.py", "finder"],
        "oracle_cmd": ["python3", "scripts/autonomous/simplify_gate.py", "oracle"],
        "tier": "T1",
        "needs_config": False,
    },
    "fuzz": {
        "stream": "C",
        "finder_cmd": ["python3", "scripts/autonomous/fuzz_shrink.py", "run"],
        "oracle_cmd": ["python3", "scripts/autonomous/fuzz_shrink.py", "replay"],
        "tier": "T1",
        "needs_config": True,
    },
    "performance": {
        "stream": "G",
        "finder_cmd": ["python3", "scripts/autonomous/perf_gate.py"],
        "oracle_cmd": ["python3", "scripts/autonomous/perf_gate.py"],
        "tier": "T2",
        "needs_config": True,
    },
}


def _run_cmd(argv: list[str], cwd: Path) -> tuple[int, str]:
    try:
        env = dict(os.environ)
        # Ensure standard system paths are in PATH so shell scripts with
        # basic coreutils (cat, sh, etc.) work reliably.
        current_path = env.get("PATH", "")
        parts = current_path.split(os.pathsep) if current_path else []
        for sp in ("/usr/bin", "/bin", "/usr/sbin", "/sbin"):
            if sp not in parts:
                parts.append(sp)
        env["PATH"] = os.pathsep.join(parts)
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env=env,
        )
        return proc.returncode, proc.stdout
    except Exception:
        return 1, ""


def find_repo_root(start: Path | None = None) -> Path:
    p = (start or Path.cwd()).resolve()
    curr = p
    while True:
        if (curr / ".git").exists():
            return curr
        if curr.parent == curr:
            break
        curr = curr.parent
    try:
        return repo_root(p)
    except Exception:
        return p


def check_red_ci(root: Path) -> tuple[float, dict[str, Any]]:
    """Check for failing CI via gh or local bug reports."""
    gh_bin = shutil.which("gh")
    signals: dict[str, Any] = {}
    if gh_bin:
        code, out = _run_cmd(
            [
                gh_bin,
                "pr",
                "list",
                "--state",
                "open",
                "--json",
                "number,title,headRefName,headRefOid,statusCheckRollup,labels,createdAt",
            ],
            root,
        )
        if code == 0 and out.strip():
            try:
                prs = json.loads(out)
                if isinstance(prs, list):
                    for pr in prs:
                        rollup = pr.get("statusCheckRollup") or []
                        for check in rollup:
                            conc = str(check.get("conclusion") or "").upper()
                            status = str(check.get("status") or "").upper()
                            state = str(check.get("state") or "").lower()
                            if (
                                conc in ("FAILURE", "TIMED_OUT", "ACTION_REQUIRED")
                                or state in ("fail", "error", "failure")
                                or (status == "COMPLETED" and conc == "FAILURE")
                            ):
                                signals["red_ci"] = pr.get("number", 1)
                                return 1.0, signals
            except Exception:
                pass

    # Check local bug reports
    bug_dir = root / "docs" / "bugreports"
    if bug_dir.is_dir():
        bugs = list(bug_dir.glob("*.md"))
        if bugs:
            signals["bug_reports"] = len(bugs)
            return 1.0, signals

    return 0.0, signals


def check_docs_drift(root: Path) -> tuple[float, dict[str, Any]]:
    """Check for doc drift via docs_truth.py check --json."""
    dt_script = SCRIPT_DIR / "docs_truth.py"
    if not dt_script.is_file():
        return 0.0, {}
    code, out = _run_cmd([sys.executable, str(dt_script), "check", "--json"], root)
    if out.strip():
        try:
            data = json.loads(out)
            candidates = data.get("candidates", [])
            if candidates:
                return 1.0, {"drift_count": len(candidates)}
        except Exception:
            pass
    return 0.0, {}


def check_security(root: Path) -> tuple[float, dict[str, Any]]:
    """Check for secrets or advisories via advisory.py."""
    adv_script = SCRIPT_DIR / "advisory.py"
    if not adv_script.is_file():
        return 0.0, {}
    code, out = _run_cmd([sys.executable, str(adv_script), "secrets", "--json"], root)
    if out.strip():
        try:
            data = json.loads(out)
            candidates = data.get("candidates", [])
            if candidates:
                return 1.0, {"secret_hits": len(candidates)}
        except Exception:
            pass
    return 0.0, {}


def check_adversarial(root: Path, cfg: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """Check for test smells via stream_b_scan.py."""
    scan_script = SCRIPT_DIR / "stream_b_scan.py"
    if not scan_script.is_file():
        return 0.0, {}
    code, out = _run_cmd([sys.executable, str(scan_script), "--json"], root)
    if out.strip():
        try:
            data = json.loads(out)
            candidates = data.get("candidates", [])
            if candidates:
                return 1.0, {"smell_count": len(candidates)}
        except Exception:
            pass
    return 0.0, {}


def _is_test_cmd_green(root: Path, test_cmd: list[str]) -> bool:
    """Check whether the configured test command runs and exits 0."""
    if not test_cmd:
        return False
    # If the first executable isn't on PATH and isn't a file, return False
    first = test_cmd[0]
    if first == "python3":
        cmd = [sys.executable] + test_cmd[1:]
    else:
        if not shutil.which(first) and not (root / first).is_file():
            return False
        cmd = list(test_cmd)
    code, _ = _run_cmd(cmd, root)
    return code == 0


def _is_file_audited_in_ledger(ledger_text: str, rel_path: str) -> bool:
    """Check if rel_path is recorded in the ledger with SOUND or COMPLETED status."""
    for line in ledger_text.splitlines():
        if f"`{rel_path}`" in line or f" {rel_path} " in line:
            parts = [p.strip() for p in line.split("|")]
            # Table columns: | Date | Stream | Target File | Content Hash | Status | ...
            if len(parts) >= 6:
                status = parts[5].strip().upper()
                if "SOUND" in status or "COMPLETED" in status:
                    return True
    return False


def check_mutation(
    root: Path, cfg: dict[str, Any], has_cfg_file: bool
) -> tuple[float, bool, dict[str, Any]]:
    """Check mutation stream A readiness and unaudited targets."""
    cand_a = cfg.get("candidates", {}).get("A", {}).get("include", [])
    test_cmd = cfg.get("test_cmd")
    if not has_cfg_file or not cand_a or not test_cmd:
        return 0.0, True, {}

    # Must have a baseline-green test command
    if not _is_test_cmd_green(root, test_cmd):
        return 0.0, False, {}

    # Check ledger coverage: if all candidate files are already audited
    # with SOUND or COMPLETED status.
    ledger_path = root / cfg.get("ledger_path", "docs/AUTONOMOUS_WORK_LEDGER.md")
    if ledger_path.is_file():
        l_text = ledger_path.read_text(encoding="utf-8", errors="replace")
        # Check files matching candidates.A.include
        all_audited = True
        has_any_candidate = False
        for inc_glob in cand_a:
            for f in root.glob(inc_glob):
                if f.is_file():
                    has_any_candidate = True
                    rel = str(f.relative_to(root))
                    if not _is_file_audited_in_ledger(l_text, rel):
                        all_audited = False
                        break
            if not all_audited:
                break
        if has_any_candidate and all_audited:
            return 0.0, False, {}

    # Check ledger targets
    ledger_script = SCRIPT_DIR / "ledger.py"
    if ledger_script.is_file():
        code, out = _run_cmd(
            [
                sys.executable,
                str(ledger_script),
                "next",
                "--stream",
                "A",
                "--limit",
                "1",
            ],
            root,
        )
        if "NEXT TARGET FOUND:" in out:
            return 1.0, False, {"unaudited_targets": 1}

    return 0.0, False, {}


def check_flake(root: Path) -> tuple[float, dict[str, Any]]:
    flake_json = root / ".autonomous" / "flakes.json"
    if flake_json.is_file():
        try:
            data = json.loads(flake_json.read_text(encoding="utf-8"))
            if data:
                return 1.0, {"flake_count": len(data)}
        except Exception:
            pass
    return 0.0, {}


def check_benchmarks(root: Path) -> tuple[float, dict[str, Any]]:
    for candidate in ("benchmarks", "benches", "perf"):
        if (root / candidate).is_dir():
            return 1.0, {"bench_dir": candidate}
    return 0.0, {}


def survey_repo(
    root: Path,
    profile_path: Path | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    # Check config presence
    cfg_file = None
    for cand in (
        root / ".autonomous" / "config.json",
        root / "scripts" / "autonomous" / "config.json",
    ):
        if cand.is_file():
            cfg_file = cand
            break

    try:
        cfg = load_config(root, cfg_file) if cfg_file else {}
    except Exception:
        cfg = {}

    # Load profile priors (flat {tool: prior} map written by profile.py;
    # a missing profile leaves every prior at the neutral 1.0) and the
    # suppression list (3 consecutive rejections ⇒ 30 days, per profile.py).
    priors: dict[str, float] = {}
    suppressed: set[str] = set()
    profile_source = (
        profile_path
        if profile_path and profile_path.is_file()
        else (root / ".autonomous" / "profile.json" if (root / ".autonomous" / "profile.json").is_file() else None)
    )
    if profile_source is not None:
        try:
            pdata = json.loads(profile_source.read_text(encoding="utf-8"))
            priors = pdata.get("priors", {})
            suppressed = set(pdata.get("suppressed", []))
        except Exception:
            pass

    # Gather signals
    red_ci_strength, red_ci_sigs = check_red_ci(root)
    drift_strength, drift_sigs = check_docs_drift(root)
    sec_strength, sec_sigs = check_security(root)
    smell_strength, smell_sigs = check_adversarial(root, cfg)
    mut_strength, mut_needs_cfg, mut_sigs = check_mutation(root, cfg, bool(cfg_file))
    flake_strength, flake_sigs = check_flake(root)
    bench_strength, bench_sigs = check_benchmarks(root)

    signal_strengths: dict[str, tuple[float, dict[str, Any]]] = {
        "defect-repair": (red_ci_strength, red_ci_sigs),
        "security": (sec_strength, sec_sigs),
        "docs-reconcile": (drift_strength, drift_sigs),
        "flake": (flake_strength, flake_sigs),
        "adversarial": (smell_strength, smell_sigs),
        "mutation": (mut_strength, mut_sigs),
        "simplification": (0.0, {}),
        "fuzz": (0.0, {}),
        "performance": (bench_strength if bench_strength > 0 else 0.0, bench_sigs),
    }

    picks: list[dict[str, Any]] = []
    for tool, meta in TOOL_METADATA.items():
        if tool in suppressed:
            continue  # suppressed in this repo (3 consecutive rejections, 30 days)
        stream_letter = meta["stream"]
        # Skip if stream absent in common.py STREAMS registry
        if STREAMS and stream_letter in STREAMS:
            st_info = STREAMS[stream_letter]
            if st_info.get("status") not in ("available", "gated"):
                continue

        weight = DEFAULT_WEIGHTS.get(tool, 0.0)
        if tool == "performance" and bench_strength > 0:
            weight = 0.4

        strength, sigs = signal_strengths.get(tool, (0.0, {}))
        prior = float(priors.get(tool, 1.0))
        needs_cfg = meta["needs_config"]
        if tool == "mutation":
            needs_cfg = mut_needs_cfg

        if needs_cfg:
            score = 0.0
        else:
            score = round(weight * strength * prior, 4)

        picks.append(
            {
                "tool": tool,
                "stream": stream_letter,
                "score": score,
                "signals": sigs,
                "finder_cmd": list(meta["finder_cmd"]),
                "oracle_cmd": list(meta["oracle_cmd"]),
                "needs_config": bool(needs_cfg),
                "tier": str(meta["tier"]),
            }
        )

    # Sort picks deterministically: score desc, weight desc, tool name asc
    picks.sort(
        key=lambda p: (
            -p["score"],
            -DEFAULT_WEIGHTS.get(p["tool"], 0.0),
            p["tool"],
        )
    )

    chosen: dict[str, Any] | None = None
    runner_up: dict[str, Any] | None = None
    reason = "no tool scored above threshold 0.25"

    if picks and picks[0]["score"] >= THRESHOLD:
        chosen = picks[0]
        tool_name = chosen["tool"]
        if tool_name == "defect-repair":
            reason = "defect-repair chosen: failing checks or bug reports detected"
        elif tool_name == "docs-reconcile":
            reason = "docs-reconcile chosen: documentation drift detected"
        elif tool_name == "security":
            reason = "security chosen: secrets or advisories detected"
        elif tool_name == "flake":
            reason = "flake chosen: test flakes recorded"
        elif tool_name == "adversarial":
            reason = "adversarial chosen: test smells detected"
        elif tool_name == "mutation":
            reason = "mutation chosen: unaudited targets present"
        else:
            reason = f"{tool_name} chosen: highest scoring pass"

        if len(picks) > 1 and picks[1]["score"] >= THRESHOLD:
            runner_up = picks[1]

    if limit is not None and limit > 0:
        picks = picks[:limit]

    return {
        "schema": SCHEMA,
        "picks": picks,
        "chosen": chosen,
        "runner_up": runner_up,
        "reason": reason,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="output survey as JSON")
    parser.add_argument(
        "--limit", type=int, default=None, help="limit number of picks in output"
    )
    parser.add_argument(
        "--profile", type=Path, default=None, help="path to profile JSON"
    )
    args = parser.parse_args()

    root = find_repo_root()
    survey = survey_repo(root, profile_path=args.profile, limit=args.limit)

    if args.json:
        print(json.dumps(survey, indent=2))
    else:
        chosen = survey["chosen"]
        print(f"Autonomous Survey ({survey['schema']}):")
        if chosen:
            t = chosen["tool"]
            st = chosen["stream"]
            sc = chosen["score"]
            print(f"  Chosen: {t} (stream {st}, score {sc})")
            if survey["runner_up"]:
                ru = survey["runner_up"]
                rt = ru["tool"]
                rst = ru["stream"]
                rsc = ru["score"]
                print(f"  Runner-up: {rt} (stream {rst}, score {rsc})")
        else:
            print("  Chosen: none (no tool above threshold)")
        print(f"  Reason: {survey['reason']}")
        print("  Picks:")
        for pick in survey["picks"]:
            cfg_note = " [needs config]" if pick["needs_config"] else ""
            pt = pick["tool"]
            pst = pick["stream"]
            psc = pick["score"]
            print(f"    - {pt} ({pst}): score {psc}{cfg_note}")

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
