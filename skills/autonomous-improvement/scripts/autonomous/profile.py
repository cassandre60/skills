#!/usr/bin/env python3
"""Learn this repository's pass profile from run records and ledger rejections.

Phase 5.2 of the generality plan: the survey's priors stop being universal
the moment a repo has evidence of its own. ``update`` reads every run record
(schema run-record/1, one JSON file per run) under --records, adds the
REJECTED/WONTFIX rows of the work ledger (the Stream column names the pass
by its leading letter), and writes --out (schema profile/1) — also printed
to stdout, byte for byte.

Per pass: ``decided`` counts records carrying a verdict plus ledger
rejections; ``accepted`` counts verdicts merged/accepted (every other
verdict — closed, ignored — is a rejection). The rate is Laplace-smoothed,
(accepted + 1) / (decided + 2), so a pass with no data sits at 0.5 and the
``prior`` the survey multiplies by is twice that: 1.0 with no data. Three
consecutive rejections suppress the pass for 30 days from the last decision;
any acceptance clears the streak. Every pass with evidence carries a lesson
(scope = repo slug) that must be revalidated within 90 days of its last
decision — past that it is demoted to ``expired_lessons``, never defended.

Deterministic: with --now pinned, identical inputs give byte-identical
output (sorted keys, no wall clock).

Usage:
  python3 scripts/autonomous/profile.py update [--records DIR]
      [--ledger PATH] [--out PATH] [--now ISO]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    EXIT_OK,
    UsageError,
    eprint,
    format_exit,
    repo_root,
    split_table_row,
)

SCHEMA = "profile/1"
DEFAULT_RECORDS = ".autonomous/run-records"
DEFAULT_LEDGER = "docs/AUTONOMOUS_WORK_LEDGER.md"
DEFAULT_OUT = ".autonomous/profile.json"

ACCEPTED_VERDICTS = {"merged", "accepted"}
REJECTION_STATUSES = {"REJECTED", "WONTFIX"}
SUPPRESSION_REJECTIONS = 3
SUPPRESSION_DAYS = 30
LESSON_VALIDITY_DAYS = 90

# The ledger's Stream column names a pass by its leading letter; records
# carry the tool name in "chosen". Unknown letters are skipped, never guessed.
STREAM_LETTER_TO_TOOL: Dict[str, str] = {
    "A": "mutation",
    "B": "adversarial",
    "C": "fuzz",
    "E": "defect-repair",
    "F": "flake",
    "G": "performance",
    "H": "security",
    "I": "docs-reconcile",
    "J": "simplification",
    "K": "security",
}
KNOWN_TOOLS = sorted(set(STREAM_LETTER_TO_TOOL.values()))

# A decided event: (when, scan order, accepted?, evidence description). Scan
# order breaks timestamp ties deterministically (records in filename order,
# ledger rows in file order).
_Event = Tuple[datetime.datetime, int, bool, str]


def _parse_ts(value: Any) -> Optional[datetime.datetime]:
    """ISO timestamp -> aware datetime (naive is read as UTC); None if hopeless."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _load_records(records_dir: Path) -> List[Dict[str, Any]]:
    """Every readable JSON object under records_dir, in filename order."""
    if not records_dir.is_dir():
        return []
    records: List[Dict[str, Any]] = []
    for path in sorted(records_dir.glob("*.json")):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            eprint(f"[!] skipping unreadable run record {path.name}: {exc}")
            continue
        if isinstance(data, dict):
            records.append(data)
    return records


def _ledger_rejections(ledger_path: Path) -> List[Dict[str, Any]]:
    """REJECTED/WONTFIX ledger rows as decided events for their stream's pass."""
    if not ledger_path.is_file():
        return []
    try:
        lines = ledger_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rows: List[Dict[str, Any]] = []
    for line in lines:
        cells = split_table_row(line)
        if not cells or len(cells) < 5:
            continue
        status = cells[4].strip().upper()
        if status not in REJECTION_STATUSES:
            continue
        when = _parse_ts(cells[0])
        tool = STREAM_LETTER_TO_TOOL.get(cells[1].strip()[:1].upper())
        if when is None or tool is None:
            continue
        rows.append(
            {"ts": when, "tool": tool, "desc": f"ledger {cells[0].strip()} {status}"}
        )
    return rows


def _repo_slug(records: List[Dict[str, Any]]) -> str:
    """Slug of the newest record carrying one; empty when no records do."""
    best: Optional[Tuple[datetime.datetime, str]] = None
    floor = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    for record in records:
        slug = str(record.get("repo_slug") or "").strip()
        if not slug:
            continue
        when = _parse_ts(record.get("ts")) or floor
        if best is None or when > best[0]:
            best = (when, slug)
    return best[1] if best else ""


def build_profile(
    records: List[Dict[str, Any]],
    ledger_rows: List[Dict[str, Any]],
    now: datetime.datetime,
    updated_at: str,
) -> Dict[str, Any]:
    events: Dict[str, List[_Event]] = {}
    seq = 0
    decided_records = 0
    for record in records:
        verdict = str(record.get("verdict") or "").strip().lower()
        chosen = str(record.get("chosen") or "").strip()
        when = _parse_ts(record.get("ts"))
        if not verdict:
            continue  # not decided yet: never counted, never a rejection
        if not chosen or when is None:
            eprint(
                f"[!] skipping decided record without chosen/ts: {record.get('id')!r}"
            )
            continue
        rid = str(record.get("id") or "").strip()
        desc = f"{rid} ({verdict})" if rid else f"record ({verdict})"
        events.setdefault(chosen, []).append(
            (when, seq, verdict in ACCEPTED_VERDICTS, desc)
        )
        seq += 1
        decided_records += 1
    for entry in ledger_rows:
        events.setdefault(entry["tool"], []).append(
            (entry["ts"], seq, False, entry["desc"])
        )
        seq += 1

    slug = _repo_slug(records)
    passes: Dict[str, Dict[str, Any]] = {}
    lessons: List[Dict[str, Any]] = []
    expired: List[Dict[str, Any]] = []
    for tool in sorted(set(KNOWN_TOOLS) | set(events)):
        timeline = sorted(events.get(tool, []), key=lambda event: (event[0], event[1]))
        decided = len(timeline)
        accepted = sum(1 for event in timeline if event[2])
        rate = (accepted + 1) / (decided + 2) if decided else 0.5
        streak = 0
        for event in reversed(timeline):
            if event[2]:
                break
            streak += 1
        last = timeline[-1][0] if timeline else None
        suppressed = None
        if streak >= SUPPRESSION_REJECTIONS and last is not None:
            suppressed = (last + datetime.timedelta(days=SUPPRESSION_DAYS)).isoformat()
        passes[tool] = {
            "decided": decided,
            "accepted": accepted,
            "smoothed_rate": round(rate, 4),
            "prior": round(2 * rate, 4),
            "last_decided": last.isoformat() if last is not None else None,
            "suppressed_until": suppressed,
            "consecutive_rejections": streak,
        }
        if not decided:
            continue
        revalidate_by = last + datetime.timedelta(days=LESSON_VALIDITY_DAYS)
        lesson = {
            "evidence": (
                f"{tool}: {accepted}/{decided} accepted "
                f"(decided events: {', '.join(event[3] for event in timeline)})"
            ),
            "scope": slug,
            "revalidate_by": revalidate_by.isoformat(),
        }
        (expired if now > revalidate_by else lessons).append(lesson)

    if not events:
        note = (
            "no profile evidence yet: 0 decided run records and 0 ledger "
            "rejection rows, so every pass keeps the neutral prior 1.0 "
            "(smoothed rate 0.5)"
        )
    else:
        note = (
            f"{decided_records} decided run record(s) "
            f"({len(records) - decided_records} without a verdict ignored) and "
            f"{len(ledger_rows)} ledger rejection row(s); "
            "prior = 2 * (accepted + 1) / (decided + 2); 3 consecutive "
            "rejections suppress a pass for 30 days"
        )
    def _still_suppressed(entry: dict) -> bool:
        until = entry.get("suppressed_until")
        if not until:
            return False
        parsed = _parse_ts(until)
        # Unparseable dates suppress defensively; expired ones never do.
        return parsed is None or parsed > now

    return {
        "schema": SCHEMA,
        "updated_at": updated_at,
        "passes": passes,
        # Flat views for survey.py: priors multiply every tool's score
        # (missing profile ⇒ 1.0); suppressed tools are skipped entirely —
        # but an expired suppression (past its 30 days) suppresses nothing.
        "priors": {tool: entry["prior"] for tool, entry in passes.items()},
        "suppressed": [
            tool for tool, entry in passes.items() if _still_suppressed(entry)
        ],
        "lessons": lessons,
        "expired_lessons": expired,
        "note": note,
    }


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _now(value: Optional[str]) -> Tuple[str, datetime.datetime]:
    if value:
        parsed = _parse_ts(value)
        if parsed is None:
            raise UsageError(f"--now is not an ISO timestamp: {value}")
        return value, parsed
    current = datetime.datetime.now().astimezone()
    return current.isoformat(timespec="seconds"), current


def cmd_update(args: argparse.Namespace) -> int:
    root = repo_root()
    records = _load_records(_resolve(root, args.records))
    ledger_rows = _ledger_rejections(_resolve(root, args.ledger))
    out_path = _resolve(root, args.out)
    updated_at, now = _now(args.now)

    profile = build_profile(records, ledger_rows, now, updated_at)
    text = json.dumps(profile, indent=2, sort_keys=True) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, out_path)
    print(text, end="")
    return EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    updater = sub.add_parser(
        "update", help="write the per-repo pass profile (schema profile/1)"
    )
    updater.add_argument(
        "--records",
        default=DEFAULT_RECORDS,
        help="run-record directory (default: %(default)s)",
    )
    updater.add_argument(
        "--ledger",
        default=DEFAULT_LEDGER,
        help="work ledger path (default: %(default)s)",
    )
    updater.add_argument(
        "--out", default=DEFAULT_OUT, help="profile output path (default: %(default)s)"
    )
    updater.add_argument(
        "--now",
        default=None,
        help="pin the clock (ISO timestamp) for deterministic output",
    )
    args = parser.parse_args(argv)
    try:
        return cmd_update(args)
    except Exception as exc:
        return format_exit(exc)


if __name__ == "__main__":
    sys.exit(main())
