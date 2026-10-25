#!/usr/bin/env python3
"""Read a repository's own conventions — 0 tokens, all signals optional.

Phase 4.6 of the generality plan: unknown repos get the trust treatment a
stranger deserves. ``read --json`` (schema conventions/1) surfaces the
conventions a publisher must respect: an AI-contribution policy (which
forces trust tier T1 — issue mode only), CODEOWNERS, issue/PR templates,
DCO sign-off requirements, the commit-subject style of the last 20 commits,
and the label list when gh exists. Missing files degrade to absent keys and
never crash; a missing gh becomes ``labels: null``.

Trust tiers: ``trust.tier`` in config ("T1" the default, "T2" the owner's
promotion). At T1 — or under an AI policy — ``fleet.py publish`` stays in
issue mode and the ledger lives at ``.autonomous/AUTONOMOUS_WORK_LEDGER.md``
(untracked). ``eligible_for_t2`` REPORTS eligibility; it never promotes.

Usage:
  python3 scripts/autonomous/conventions.py read --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import eprint, repo_root  # noqa: E402

SCHEMA = "conventions/1"
ACCEPTANCE_FLOOR = 0.6
T2_MIN_DECIDED = 10

AI_POLICY_KEYWORDS = (
    "ai-generated",
    "ai contributions",
    "no ai",
    "human-authored",
    "must be human",
    "ai-assisted",
)
CONVENTIONAL_RE = re.compile(
    r"^(feat|fix|refactor|chore|docs|test|perf|build|ci)(\(.+\))?!?:\s"
)
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _find_ai_policy(root: Path) -> Dict[str, Any]:
    candidates = [
        root / "CONTRIBUTING.md",
        root / ".github" / "CONTRIBUTING.md",
    ]
    candidates += sorted(root.glob("CONTRIBUTING*"))
    seen = set()
    for path in candidates:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        text = _read_text(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            lowered = line.lower()
            if any(keyword in lowered for keyword in AI_POLICY_KEYWORDS):
                return {
                    "found": True,
                    "file": path.relative_to(root).as_posix(),
                    "line": lineno,
                    "quote": line.strip(),
                }
    return {"found": False, "file": None, "line": None, "quote": None}


def _find_codeowners(root: Path) -> List[Dict[str, Any]]:
    for candidate in (
        root / ".github" / "CODEOWNERS",
        root / "CODEOWNERS",
        root / "docs" / "CODEOWNERS",
    ):
        if not candidate.is_file():
            continue
        entries = []
        for line in (_read_text(candidate) or "").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            entries.append({"pattern": parts[0], "owners": parts[1:]})
        return entries
    return []


def _find_templates(root: Path) -> Dict[str, List[str]]:
    issue_dir = root / ".github" / "ISSUE_TEMPLATE"
    issue = sorted(p.name for p in issue_dir.glob("*.md")) if issue_dir.is_dir() else []
    pr = sorted(p.name for p in (root / ".github").glob("PULL_REQUEST_TEMPLATE*.md"))
    return {"issue": issue, "pr": pr}


def _find_dco(root: Path, templates: Dict[str, List[str]]) -> Dict[str, Any]:
    sources = [root / "CONTRIBUTING.md", root / ".github" / "CONTRIBUTING.md"]
    sources += [root / ".github" / name for name in templates.get("pr", [])]
    sources += [
        root / ".github" / "ISSUE_TEMPLATE" / name
        for name in templates.get("issue", [])
    ]
    for path in sources:
        text = _read_text(path)
        if text and ("Signed-off-by" in text or "DCO" in text):
            return {
                "required": True,
                "evidence": f"{path.relative_to(root).as_posix()} mentions sign-off",
            }
    return {"required": False, "evidence": None}


def _commit_style(root: Path) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            ["git", "log", "--format=%s", "-n", "20"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
        subjects = [s for s in proc.stdout.splitlines() if s.strip()]
    except OSError:
        subjects = []
    if not subjects:
        return {"conventional_ratio": None, "samples": []}
    conventional = sum(1 for s in subjects if CONVENTIONAL_RE.match(s))
    return {
        "conventional_ratio": round(conventional / len(subjects), 2),
        "samples": subjects[:5],
    }


def _labels(root: Path) -> Tuple[Optional[List[str]], Optional[str]]:
    try:
        proc = subprocess.run(
            ["gh", "label", "list", "--limit", "100", "--json", "name"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, "gh unavailable"
    if proc.returncode != 0:
        return None, "gh unavailable"
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None, "gh unavailable"
    return sorted(item["name"] for item in data if isinstance(item, dict) and "name" in item), None


def read_conventions(root: Path) -> Dict[str, Any]:
    """The conventions dict behind `read --json` — fleet.py calls this inline."""
    ai_policy = _find_ai_policy(root)
    templates = _find_templates(root)
    labels, note = _labels(root)
    return {
        "schema": SCHEMA,
        "ai_policy": ai_policy,
        "trust_tier_suggested": "T1" if ai_policy["found"] else None,
        "codeowners": _find_codeowners(root),
        "templates": templates,
        "dco": _find_dco(root, templates),
        "commit_style": _commit_style(root),
        "labels": labels,
        "note": note,
    }


def suggest_publish_mode(config: Dict[str, Any], conventions: Dict[str, Any]) -> Tuple[str, str]:
    """(mode, reason): T1 or an AI policy keeps publish in issue mode."""
    tier = str((config.get("trust") or {}).get("tier") or "T1")
    policy = (conventions.get("ai_policy") or {}).get("found")
    if policy:
        return "issue", "the repo's AI-contribution policy forbids AI-generated PRs"
    if tier == "T1":
        return "issue", f"trust tier {tier} (the default): issue mode until the owner promotes"
    return "pr", f"trust tier {tier} with no AI policy on record"


def eligible_for_t2(
    acceptance_rate: float, decided_items: int, referee_incidents: int
) -> Tuple[bool, str]:
    """Eligibility only — promotion is the owner's, never automatic."""
    if referee_incidents:
        return False, f"{referee_incidents} referee incident(s) on record"
    if decided_items < T2_MIN_DECIDED:
        return False, f"only {decided_items} decided items (need {T2_MIN_DECIDED})"
    if acceptance_rate < ACCEPTANCE_FLOOR:
        return (
            False,
            f"acceptance {round(acceptance_rate, 3)} below floor {ACCEPTANCE_FLOOR}",
        )
    return True, (
        f"acceptance {round(acceptance_rate, 3)} over {decided_items} decided "
        f"items with zero referee incidents"
    )


def fills_pr_template(
    conventions: Dict[str, Any], body: str, root: Optional[Path] = None
) -> Tuple[bool, List[str]]:
    """(ok, missing headings) — the body must carry every template heading."""
    names = (conventions.get("templates") or {}).get("pr") or []
    if not names or root is None:
        return True, []
    template_path = root / ".github" / names[0]
    text = _read_text(template_path)
    if text is None:
        return True, []
    headings = []
    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match:
            headings.append(match.group(1))
    missing = [h for h in headings if h not in body]
    return (not missing), missing


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    reader = sub.add_parser("read", help="print the repo's conventions as JSON")
    reader.add_argument("--json", action="store_true", help="JSON output (default)")
    args = parser.parse_args(argv)

    if args.cmd == "read":
        payload = read_conventions(repo_root())
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            for key, value in sorted(payload.items()):
                print(f"{key}: {value}")
        return 0
    eprint(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
