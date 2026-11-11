#!/usr/bin/env python3
"""Model pool resolution and dynamic discovery for fleet.py."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from typing import Any, Dict, List, Tuple

from common import (
    EXIT_OK,
    config_path,
    eprint,
    load_config,
    main_repo_root,
)


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
        warnings.append(
            "dynamic source 'opencode-models': the opencode CLI is unavailable; skipped"
        )
        return []
    if proc.returncode != 0:
        warnings.append(
            f"dynamic source 'opencode-models': opencode models exited {proc.returncode}; skipped"
        )
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


def _resolve_model_pool(
    cfg: Dict[str, Any]
) -> Tuple[List[str], List[Dict[str, str]], List[str]]:
    """Resolve the recorded model pool into an ordered ladder (issue #103).

    The pool is an ALLOWLIST: rotation candidates come only from it, so a
    premium model the owner did not list is unreachable. Dynamic sources are
    mechanisms configured by the owner, never hardcoded lists. Returns
    (ordered ids, annotations, warnings); free tier first, then premium in
    listed order.
    """
    pool = (cfg.get("fleet") or {}).get("model_pool") or {}
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

    for entry in pool.get("entries") or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            warnings.append("model_pool entry without an id; skipped")
            continue
        add(
            str(entry["id"]),
            str(entry.get("tier") or "premium"),
            "static",
            str(entry.get("note") or ""),
        )

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
            print(
                json.dumps(
                    {
                        "schema": "model-pool/1",
                        "ladder": ordered,
                        "warnings": warnings,
                    }
                )
            )
        else:
            for entry_id in ordered:
                print(entry_id)
        return EXIT_OK
    if args.json:
        print(
            json.dumps(
                {
                    "schema": "model-pool/1",
                    "ladder": ordered,
                    "entries": annotations,
                    "warnings": warnings,
                },
                indent=2,
            )
        )
        return EXIT_OK
    if not annotations:
        eprint("model pool is empty: no dynamic sources and no entries are configured.")
        eprint(
            f"record the owner's accepted pool in {config_path(root, args.config)} under fleet.model_pool"
        )
        eprint(
            "(ask the owner on first deployment; premium models stay unreachable unless listed)"
        )
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


def evaluate_track_caps(
    track_caps: Dict[str, int], prs_data: List[Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """Count open PRs per track and report cap status."""
    if not track_caps:
        return {}
    res: Dict[str, Dict[str, Any]] = {}
    for trk, cap in track_caps.items():
        trk_low = trk.lower()
        count = 0
        for p in prs_data:
            title = (p.get("title") or "").lower()
            body = (p.get("body") or "").lower()
            labels = [l.get("name", "").lower() for l in (p.get("labels") or [])]
            if (
                any(l == f"track:{trk_low}" or l == f"track/{trk_low}" or l == trk_low for l in labels)
                or title.startswith(f"{trk_low}:")
                or title.startswith(f"[{trk_low}]")
                or f"track: {trk_low}" in body
            ):
                count += 1
        res[trk] = {"open": count, "cap": cap, "capped": count >= cap}
    return res


def saturation_info(root: Path) -> Dict[str, Any]:
    """Read saturation state from .autonomous/saturation.json."""
    sat_path = root / ".autonomous" / "saturation.json"
    sat_info: Dict[str, Any] = {"saturated": False, "status": "active"}
    if sat_path.is_file():
        try:
            sat_data = json.loads(sat_path.read_text(encoding="utf-8"))
            if sat_data.get("saturated"):
                sat_info = {
                    "saturated": True,
                    "status": "saturated",
                    "timestamp": sat_data.get("timestamp"),
                    "reason": sat_data.get("reason"),
                }
        except Exception:
            pass
    return sat_info


