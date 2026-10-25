#!/usr/bin/env python3
"""Stream L (capped): replay-gated fleet self-improvement proposals.

references/08-recursive-self-improvement.md, stream L — meta
self-improvement with every non-negotiable enforced mechanically:

* **Kill switch (independent of the fleet switch).** L runs only when
  ``fleet.stream_l_enabled`` is true in config AND no
  ``.autonomous/STOP-STREAM-L`` file exists. The fleet switch does not
  imply the L switch; both must be off-able separately.
* **Replay, not folklore.** Every quantitative claim in an L3 proposal is
  recomputed against the recorded fleet history fixture (rows with
  value/owner-minutes/CI/tokens). A claim whose "before" does not match
  the recorded median is rejected as ungrounded.
* **L4 surfaces are issue-only, always.** Proposals touching the
  crown-jewel suffix list (weakening tripwire, eval gates, tier floors,
  merge policy, ...) are refused as PR candidates even when their own
  numbers would pass the numeric replay — a loop that can edit its
  evaluator optimizes the evaluator. The refusal is journaled.
* **Multi-objective with floors.** The pass verdict requires non-positive
  deltas on cost objectives (owner-minutes, CI, tokens) and an
  acceptance estimate above the floor — never PR count. A passing
  proposal yields a DRAFT PR text (T2 cap), never a self-merge ladder.
* **Lessons expire.** Mined lessons carry evidence + scope +
  re-validate-by; expired or evidence-free lessons are demoted in the
  owner digest rather than accruing forever.

Usage:
  python3 scripts/autonomous/meta.py replay \
      --fixture-dir evals/fixtures/meta-fleet \
      --proposal evals/fixtures/meta-fleet/proposals/good-diff-budget.json --json
  python3 scripts/autonomous/meta.py lessons \
      --fixture-dir evals/fixtures/meta-fleet --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import EXIT_OK, Fatal, eprint, load_config  # noqa: E402

SCHEMA = "stream-l/1"
STOP_FILE = "STOP-STREAM-L"
DEFAULT_CROWN_JEWELS = (
    "weakening_tripwire",
    "eval_set",
    "eval_gates",
    "held_out_cases",
    "five_laws",
    "tier_floors",
    "merge_policy",
    "referee_isolation",
    "stream_contract",
    "acceptance_floors",
)


def kill_switch_problem(root: Path, cfg: Dict[str, Any]) -> Optional[str]:
    """Stream L's own kill switch, independent of the fleet switch."""
    fleet = cfg.get("fleet", {}) or {}
    if not bool(fleet.get("stream_l_enabled")):
        return (
            "stream L kill switch: L is gated and disabled "
            "(set fleet.stream_l_enabled=true in config.json to request it; "
            "independent of the fleet switch)"
        )
    for candidate in (root / ".autonomous" / STOP_FILE, Path.cwd() / ".autonomous" / STOP_FILE):
        if candidate.exists():
            return f"stream L kill switch present ({STOP_FILE}); L stays off while it exists"
    return None


def load_history(fixture_dir: Path) -> Dict[str, Any]:
    path = fixture_dir / "history.json"
    if not path.is_file():
        raise Fatal(f"history not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_proposal(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise Fatal(f"proposal not found: {path}")
    proposal = json.loads(path.read_text(encoding="utf-8"))
    if proposal.get("schema") != "stream-l-proposal/1":
        raise Fatal(f"proposal schema must be stream-l-proposal/1, got {proposal.get('schema')!r}")
    return proposal


def proposal_digest(proposal: Dict[str, Any]) -> str:
    payload = json.dumps(proposal, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def journal(
    state_path: Path,
    digest: str,
    verdict: str,
    proposal_path: str,
) -> Dict[str, Any]:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    data: Dict[str, Any] = {"schema": SCHEMA, "proposals": []}
    if state_path.is_file():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            eprint(f"[!] journal unreadable, starting fresh: {state_path}")
            data = {"schema": SCHEMA, "proposals": []}
    duplicates = 0
    for entry in data["proposals"]:
        if entry.get("digest") == digest:
            duplicates += 1
    record = {
        "digest": digest,
        "verdict": verdict,
        "proposal": proposal_path,
        "date": time.strftime("%Y-%m-%d"),
    }
    data["proposals"].append(record)
    state_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {"duplicates_seen": duplicates, "record": record}


def touches_crown_jewels(proposal: Dict[str, Any], gates: Dict[str, Any]) -> List[str]:
    crown = set(gates.get("crown_jewels") or DEFAULT_CROWN_JEWELS)
    hits: List[str] = []
    for touch in proposal.get("touches") or []:
        text = str(touch)
        for key in crown:
            if key in text:
                hits.append(key)
    return sorted(set(hits))


def recorded_median(
    history: Dict[str, Any], claims: Dict[str, Any]
) -> Optional[float]:
    """Recompute the claimed 'before' from the recorded rows."""
    filt = dict(claims.get("filter") or {})
    metric = str(claims.get("metric", "median_owner_minutes"))
    field = metric.removeprefix("median_")
    values: List[float] = []
    for row in history.get("rows") or []:
        if row.get("outcome") not in {"accepted", "rejected", "rework"}:
            continue
        if str(row.get("stream")) != str(claims.get("stream")):
            continue
        if all(row.get(key) == value if key != "rework_reason" else value in str(row.get("rework_reason", "")) for key, value in filt.items()):
            values.append(float(row.get(field, 0)))
    if not values:
        return None
    return float(statistics.median(values))


def replay_claims(
    history: Dict[str, Any],
    proposal: Dict[str, Any],
) -> Dict[str, Any]:
    """Recompute the 'before' from rows; verify the claimed improvement.

    Returns a verdict payload; consistency failures become rejections.
    """
    claims = proposal.get("claims") or {}
    claims_replay: Dict[str, Any] = {}
    recomputed = recorded_median(history, claims)
    claimed_before = claims.get("before")
    claimed_after = claims.get("after")
    if recomputed is None:
        return {
            "verdict": "rejected",
            "reasons": [
                "claim not grounded: no recorded rows match the proposal's "
                "filter — replay has no recorded history to verify 'before' against",
            ],
            "claims_replay": claims_replay,
        }
    claims_replay["median_owner_minutes"] = {
        "before_recorded": recomputed,
        "before_claimed": claimed_before,
        "after_claimed": claimed_after,
    }
    if claimed_before is None or abs(float(claimed_before) - recomputed) > 1e-9:
        return {
            "verdict": "rejected",
            "reasons": [
                f"claim before={claimed_before} does not match the recorded median {recomputed} "
                "(replay uses the rows, not the folklore)",
            ],
            "claims_replay": claims_replay,
        }
    if claimed_after is None or float(claimed_after) >= recomputed:
        return {
            "verdict": "rejected",
            "reasons": [
                f"claim after={claimed_after} is not an improvement over the recorded median {recomputed}",
            ],
            "claims_replay": claims_replay,
        }
    return {"verdict": "undecided", "reasons": [], "claims_replay": claims_replay}


def multi_objective_check(history: Dict[str, Any], proposal: Dict[str, Any]) -> Dict[str, Any]:
    """Multi-objective with floors; PR count is never a target."""
    objectives = proposal.get("multi_objective") or {}
    floors = (history.get("floors") or {}).get("min_acceptance_rate_14d", 0.5)
    reasons: List[str] = []
    passes = True
    for key in ("owner_minutes_delta", "ci_minutes_delta", "tokens_delta"):
        delta = objectives.get(key)
        if delta is not None and float(delta) > 0:
            passes = False
            reasons.append(f"multi-objective floor: {key} increases ({delta})")
    acceptance = objectives.get("acceptance_rate_estimate_14d")
    if acceptance is None or float(acceptance) < float(floors):
        passes = False
        reasons.append(
            f"acceptance floor: estimate {acceptance} below recorded floor {floors}"
        )
    if not objectives:
        passes = False
        reasons.append("multi-objective block missing: PR count is never a target")
    return {"passes_floors": passes, "reasons": reasons}


def draft_pr_text(
    proposal: Dict[str, Any],
    claims_replay: Dict[str, Any],
    objective: Dict[str, Any],
) -> Dict[str, str]:
    before = claims_replay["median_owner_minutes"]["before_recorded"]
    after = claims_replay["median_owner_minutes"]["after_claimed"]
    basis = proposal.get("basis", "")
    title = f"DRAFT PR (T2 cap): stream-L proposal {proposal.get('kind', '')}"
    body = (
        "Evidence-grounded fleet change proposed under stream L (T2 cap: open "
        "as a draft; the owner merges after replay + canary).\n\n"
        f"Basis: {basis}\n\n"
        f"Target metric: median owner-minutes {before} -> {after} "
        "(recomputed from the recorded rows).\n"
        f"Multi-objective: owner_minutes_delta={objective.get('owner_minutes_delta')} "
        f"ci_minutes_delta={objective.get('ci_minutes_delta')} "
        f"tokens_delta={objective.get('tokens_delta')} "
        f"acceptance_estimate={objective.get('acceptance_rate_estimate_14d')} "
        "(all cost deltas non-positive, acceptance above floor).\n\n"
        "This draft body is verbatim from meta.py replay; no numbers were "
        "hand-edited."
    )
    return {"title": title, "body": body, "cap": "T2-draft"}


def cmd_replay(args: argparse.Namespace) -> int:
    root = Path.cwd()
    cfg = load_config(root, args.config)
    problem = kill_switch_problem(root, cfg)
    if problem:
        eprint(f"[!] {problem}")
        return 2

    fixture_dir = Path(args.fixture_dir) if Path(args.fixture_dir).is_absolute() else root / args.fixture_dir
    history = load_history(fixture_dir)
    gates_path = fixture_dir / "eval-gates.json"
    gates: Dict[str, Any] = {}
    if gates_path.is_file():
        gates = json.loads(gates_path.read_text(encoding="utf-8"))
    proposal = load_proposal(root / args.proposal if not Path(args.proposal).is_absolute() else Path(args.proposal))
    digest = proposal_digest(proposal)

    # L4 divergence first: no numeric pass can excuse a crown-jewel change.
    hits = touches_crown_jewels(proposal, gates)
    if hits:
        body = (
            f"L4 surface(s) touched: {', '.join(hits)}. "
            "Issue-only, always: the fleet may not edit its evaluator"
            "(five laws, eval gates, tier floors, merge policy, referee "
            "isolation) even when the numbers would pass — file the "
            "proposal as an issue for the owner instead."
        )
        journal_result = journal(root / ".autonomous" / "l-journal.json", digest, "rejected", args.proposal)
        payload = {
            "schema": SCHEMA,
            "tool": "meta.py replay",
            "prefix": f"{digest}",
            "verdict": "rejected",
            "issue-only": True,
            "l4": True,
            "reasons": [body],
            "journal": journal_result,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            eprint(f"[stream-l] rejected (L4): {body}")
            eprint("[stream-l] journaled as rejected; re-proposing this surface forever is on you")
        return 1

    result = replay_claims(history, proposal)
    if result["verdict"] == "rejected":
        journal_result = journal(root / ".autonomous" / "l-journal.json", digest, "rejected", args.proposal)
        payload = {
            "schema": SCHEMA,
            "tool": "meta.py replay",
            "verdict": "rejected",
            "reasons": result["reasons"],
            "claims_replay": result["claims_replay"],
            "journal": journal_result,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            for reason in result["reasons"]:
                eprint(f"[stream-l] rejected: {reason}")
        return 1

    objective = multi_objective_check(history, proposal)
    if not objective["passes_floors"]:
        journal_result = journal(root / ".autonomous" / "l-journal.json", digest, "rejected", args.proposal)
        payload = {
            "schema": SCHEMA,
            "tool": "meta.py replay",
            "verdict": "rejected",
            "multi_objective": objective,
            "claims_replay": result["claims_replay"],
            "reasons": objective["reasons"],
            "journal": journal_result,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            for reason in objective["reasons"]:
                eprint(f"[stream-l] rejected (multi-objective): {reason}")
        return 1

    journal_result = journal(root / ".autonomous" / "l-journal.json", digest, "pass", args.proposal)
    draft = draft_pr_text(proposal, result["claims_replay"], proposal.get("multi_objective") or {})
    payload = {
        "schema": SCHEMA,
        "tool": "meta.py replay",
        "verdict": "pass",
        "claims_replay": result["claims_replay"],
        "multi_objective": objective,
        "draft_pr": draft,
        "journal": journal_result,
        "note": "T2 cap: draft only; canary + eval replay are owner steps",
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        eprint(f"[stream-l] pass: {draft['title']}")
        eprint("[stream-l] draft body is tool-generated; open as DRAFT, do not self-merge")
    return EXIT_OK


TOOL_TO_STREAM = {
    "mutation": "A",
    "adversarial": "B",
    "fuzz": "C",
    "defect-repair": "E",
    "flake": "F",
    "performance": "G",
    "security": "H",
    "docs-reconcile": "I",
    "simplification": "J",
}


def records_to_history_rows(records_dir: Path) -> List[Dict[str, Any]]:
    """Convert run-record/1 files into fleet-history/1 row shape.

    Counts and ids only, like the records themselves: value/costs the
    record cannot know are 0, never invented.
    """
    rows: List[Dict[str, Any]] = []
    for path in sorted(records_dir.glob("rr-*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        verdict = str(record.get("verdict") or "").strip().lower()
        if verdict in ("merged", "accepted"):
            outcome = "accepted"
        elif verdict in ("closed", "ignored"):
            outcome = "rejected"
        else:
            outcome = "open"
        rows.append(
            {
                "id": record.get("id"),
                "stream": TOOL_TO_STREAM.get(str(record.get("chosen") or ""), "?"),
                "kind": str(record.get("source") or record.get("outcome") or "run"),
                "files_touched": len(record.get("artifacts") or []),
                "value": 0,
                "owner_minutes": 0,
                "ci_minutes": 0,
                "tokens": int(record.get("tokens") or 0),
                "outcome": outcome,
            }
        )
    return rows


def cmd_lessons(args: argparse.Namespace) -> int:
    root = Path.cwd()
    if args.records:
        records_dir = (
            Path(args.records)
            if Path(args.records).is_absolute()
            else root / args.records
        )
        if not records_dir.is_dir():
            raise Fatal(f"records dir not found: {records_dir}")
        payload = {
            "schema": "fleet-history/1",
            "recorded_session": "run-records",
            "rows": records_to_history_rows(records_dir),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if not args.fixture_dir:
        raise Fatal("lessons needs --fixture-dir (lessons.json) or --records (run records)")
    cfg = load_config(root, args.config)
    problem = kill_switch_problem(root, cfg)
    if problem:
        eprint(f"[!] {problem}")
        return 2
    fixture_dir = Path(args.fixture_dir) if Path(args.fixture_dir).is_absolute() else root / args.fixture_dir
    lessons_path = fixture_dir / "lessons.json"
    if not lessons_path.is_file():
        raise Fatal(f"lessons not found: {lessons_path}")
    data = json.loads(lessons_path.read_text(encoding="utf-8"))
    as_of = args.as_of or time.strftime("%Y-%m-%d")
    kept: List[Dict[str, Any]] = []
    demoted: List[Dict[str, Any]] = []
    for lesson in data.get("lessons") or []:
        reasons: List[str] = []
        if not str(lesson.get("evidence") or "").strip():
            reasons.append("no evidence recorded")
        re_validate_by = str(lesson.get("re_validate_by") or "")
        if re_validate_by and re_validate_by < as_of:
            reasons.append(f"re-validate-by passed ({re_validate_by} < {as_of})")
        if reasons:
            demoted.append(dict(lesson, reasons=reasons))
        else:
            kept.append(lesson)
    digest_text = (
        f"[stream-l lessons digest] kept {len(kept)}; demoted {len(demoted)} "
        "for owner review (expired re-validate-by or missing evidence — "
        f"re-validate by {as_of} to stay accreted)."
    )
    payload = {
        "schema": SCHEMA,
        "tool": "meta.py lessons",
        "as_of": as_of,
        "kept": kept,
        "demoted": demoted,
        "digest": digest_text,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(digest_text)
        for entry in demoted:
            eprint(f"[-] {entry['id']}: {'; '.join(entry['reasons'])}")
        for entry in kept:
            print(f"    kept: {entry['id']} (scope: {entry.get('scope')})")
    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    sub = parser.add_subparsers(dest="cmd", required=True)

    replay_p = sub.add_parser("replay", help="replay an L3 proposal against recorded fleet history")
    replay_p.add_argument("--fixture-dir", required=True, help="directory holding history.json + eval-gates.json")
    replay_p.add_argument("--proposal", required=True)
    replay_p.add_argument("--config", help="path to config.json (default: scripts/autonomous/config.json)")
    replay_p.add_argument("--json", action="store_true")
    replay_p.set_defaults(func=cmd_replay)

    lessons_p = sub.add_parser("lessons", help="demote expired lessons, or convert run records to history rows")
    lessons_p.add_argument("--fixture-dir", default=None, help="directory holding lessons.json (or use --records)")
    lessons_p.add_argument("--records", default=None, help="run-records dir: emit fleet-history/1 rows instead")
    lessons_p.add_argument("--as-of", help="digest date override for deterministic runs")
    lessons_p.add_argument("--config", help="path to config.json (default: scripts/autonomous/config.json)")
    lessons_p.add_argument("--json", action="store_true")
    lessons_p.set_defaults(func=cmd_lessons)

    args = parser.parse_args()
    try:
        return args.func(args)
    except Fatal as exc:
        eprint(f"[!] {exc}")
        return 2
    except (json.JSONDecodeError, OSError) as exc:
        eprint(f"[!] {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
