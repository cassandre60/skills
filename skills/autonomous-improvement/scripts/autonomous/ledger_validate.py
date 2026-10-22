#!/usr/bin/env python3
"""Validate a ledger against the contract the tools actually enforce.

The parser is deliberately strict and silent: a row whose Status cell is not
exactly ``COMPLETED``/``SOUND`` (or a disposition), a row with fewer than eight
cells, a row naming several targets, a row with several hashes -- each is
skipped. A skipped row is invisible to the dedup law, so a file that *looks*
audited can be re-audited forever.

``ledger.py validate`` reports what the parser cannot see, and what the
authoritative row for each target says about the blob at HEAD. It changes
nothing; it only makes the silent part loud.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from common import (
    AUDITED_STATUSES,
    SEPARATOR_RE,
    DISPOSITION_STATUSES,
    EXIT_NEGATIVE,
    EXIT_OK,
    hash_at_head,
    path_at_head,
    split_table_row as split_row,
    test_for_source,
)

# A target cell that names more than one path cannot be looked up by a single
# path, so the row protects nothing.
PATH_RE = re.compile(r"`([^`]+)`")
# A hash cell may hold one hash, or the composite ``source:companion``.
HASH_RE = re.compile(r"`([^`]+)`")


def _table_rows(lines: List[str]) -> List[Tuple[int, List[str]]]:
    """The rows of the ledger table, in file order, with their line numbers.

    The header row and the separator are not rows: the parser starts below the
    separator, so the validator must count the same lines the parser sees.
    """
    header_index: Optional[int] = None
    for index, line in enumerate(lines):
        cells = split_row(line)
        if cells and cells[0].strip().lower() == "date":
            header_index = index
            break
    if header_index is None:
        return []
    separator_index: Optional[int] = None
    for index in range(header_index + 1, len(lines)):
        if SEPARATOR_RE.match(lines[index].strip()):
            separator_index = index
            break
    if separator_index is None:
        return []

    rows: List[Tuple[int, List[str]]] = []
    for index in range(separator_index + 1, len(lines)):
        cells = split_row(lines[index])
        if cells is None or len(cells) < 8:
            continue
        rows.append((index, cells))
    return rows


def _status_token(status: str) -> str:
    return status.strip().strip("*`").upper()


def validate_ledger(root, cfg: Dict[str, Any], ledger_path) -> Dict[str, Any]:
    """Report rows the parser cannot see, and targets whose row is stale.

    ``root`` is the repo root, ``cfg`` the loaded config, ``ledger_path`` the
    ledger file. Returns a report dict; the caller decides the exit code.
    """
    if not ledger_path.exists():
        return {
            "ledger": str(ledger_path),
            "rows_total": 0,
            "rows_visible": 0,
            "invisible": [],
            "stale": [],
            "problems": 0,
        }

    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    rows = _table_rows(lines)
    invisible: List[Dict[str, Any]] = []
    visible_targets: Dict[str, Dict[str, Any]] = {}

    for index, cells in rows:
        status = cells[4].strip()
        token = _status_token(status)
        target_cell = cells[2]
        hash_cell = cells[3]
        pr = cells[5].strip()

        paths = PATH_RE.findall(target_cell)
        hashes = HASH_RE.findall(hash_cell)
        reasons: List[str] = []
        if token not in AUDITED_STATUSES and token not in DISPOSITION_STATUSES:
            reasons.append(f"status {status!r} is not a bare token")
        if len(paths) > 1:
            reasons.append("target cell names more than one path")
        if len(hashes) > 1:
            reasons.append("hash cell holds more than one hash")
        if reasons:
            invisible.append(
                {
                    "line": index + 1,
                    "pr": pr,
                    "target": target_cell.strip(),
                    "status": status,
                    "reasons": reasons,
                }
            )
            continue

        if len(paths) == 1 and token in AUDITED_STATUSES:
            target = paths[0]
            current = visible_targets.get(target)
            if current is None:
                visible_targets[target] = {
                    "target": target,
                    "line": index + 1,
                    "pr": pr,
                    "hash": hash_cell.strip().strip("`").strip(),
                }
            else:
                # newest wins: date desc, then topmost on a tie
                current_date = current.get("date", "")
                row_date = cells[0].strip()
                if (row_date, -index) > (current_date, -current["line"]):
                    current.update(
                        {
                            "line": index + 1,
                            "pr": pr,
                            "hash": hash_cell.strip().strip("`").strip(),
                            "date": row_date,
                        }
                    )

    stale: List[Dict[str, Any]] = []
    for target, row in sorted(visible_targets.items()):
        if not path_at_head(root, target):
            continue
        source = hash_at_head(root, target)
        mapped = test_for_source(root, cfg, target)
        companion = (
            hash_at_head(root, mapped)
            if (mapped and path_at_head(root, mapped))
            else None
        )
        current = f"{source}:{companion or '-'}"
        recorded = row["hash"]
        recorded_source = recorded.split(":", 1)[0].strip()
        recorded_companion = (
            recorded.split(":", 1)[1].strip() if ":" in recorded else None
        )
        same = recorded_source == source and (
            recorded_companion == (companion or "-")
            if recorded_companion is not None
            else True  # legacy source-only row
        )
        if not same:
            stale.append(
                {
                    "target": target,
                    "pr": row["pr"],
                    "line": row["line"],
                    "recorded": recorded,
                    "current": current,
                }
            )

    return {
        "ledger": str(ledger_path),
        "rows_total": len(rows),
        "rows_visible": len(rows) - len(invisible),
        "invisible": invisible,
        "stale": stale,
        "problems": len(invisible) + len(stale),
    }
