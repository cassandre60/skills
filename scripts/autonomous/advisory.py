#!/usr/bin/env python3
"""Streams H+K: advisory finders + oracle-first bumps (candidates only).

references/08-recursive-self-improvement.md contract instantiation: the
findings here are pre-triaged by external truth (advisory DBs, format
rules), so the candidate list is script-made with a tracked FP surface.
The difference vs Dependabot-style bots is ORACLE-FIRST:

* ``secrets`` — format-rule secret scan. Candidates are issue-only, always:
  rotation is a human act, and the fleet never prints the secret into a
  PR/issue/log. Redaction is by construction — artifacts carry only a
  prefix ellipsis, the length, and a sha8 fingerprint of the raw value.
* ``deps`` — reads a locally-provided stub advisory JSON against the
  repo's ``==`` pins and flags vulnerable ones. Identical candidates from
  previous runs dedup through ``--state`` (default-off: without it the
  tool never writes anything, so scans stay zero-diff). The state file
  belongs under ``.autonomous/`` (gitignored) so it can never appear as a
  PR diff.
* ``bump`` — oracle-first version bump: the tool copies the tree to a
  scratch dir, rewrites the pin, runs the scoped test command there, and
  only a green result becomes build evidence. A red result prints a
  "## Breakage" issue draft with the failure excerpt — breakage is
  information for the owner, delivered once.

Usage:
  python3 scripts/autonomous/advisory.py secrets --json
  python3 scripts/autonomous/advisory.py deps --advisory docs/advisory-stub.json \
      --manifest requirements.txt --json --state .autonomous/advisory-seen.json
  python3 scripts/autonomous/advisory.py bump --dep vulnerable-lib --to 2.2.0 \
      --manifest requirements.txt --test-cmd "python3 tests/test_pin.py --expect 2.2.0"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import EXIT_OK, Fatal, eprint  # noqa: E402

SCHEMA = "advisory/1"
SEEN_SCHEMA = "advisory-seen/1"

# Every pattern captures the secret value as group 1; artifacts only ever
# receive the redacted form, never the capture.
SECRET_PATTERNS = [
    ("aws-access-key", re.compile(r"(?<![A-Z0-9])(AKIA[0-9A-Z]{16})(?![A-Z0-9])")),
    ("github-token", re.compile(r"\b(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("openai-style-key", re.compile(r"\b(sk-(?:live|proj|svcacct)?-?[A-Za-z0-9_-]{16,})\b")),
    ("slack-token", re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b")),
    ("private-key-block", re.compile(r"(-{2,5}(?:BEGIN [A-Z0-9 ]+ )?PRIVATE KEY-{2,5}--------)[^\n]{0,40}")),
    (
        "key-assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|pwd)\b[\"\']?\s*[:=]\s*[\"\']?"
            r"([A-Za-z0-9_\-/+=.]{16,})[\"\']?"
        ),
    ),
]

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".autonomous", ".venv", "venv"}
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".gz", ".whl", ".png", ".pdf"}

DEFAULT_SECRET_PATHS = ["."]

PIN_RE = re.compile(r"^([A-Za-z0-9_.\-]+)(==)([^\s#]+)")
VERSION_PART = re.compile(r"\d+")

def redact(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    shown = value[: min(3, len(value))]
    return f"{shown}...[redacted len={len(value)} sha8={digest[:8]}]"

def candidate_digest(kind: str, file: str, name: str, extra: str) -> str:
    payload = f"{kind}|{file}|{name}|{extra}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def iter_text_files(base: Path, patterns: List[str]) -> List[Path]:
    files: List[Path] = []
    for pattern in patterns:
        matches = sorted(base.glob(pattern)) if "*" in pattern else [base / pattern]
        for path in matches:
            if not path.is_file():
                continue
            if path.suffix.lower() in BINARY_SUFFIXES:
                continue
            if path.is_symlink():
                continue
            files.append(path)
        if pattern in (".", ""):
            files.extend(
                p for p in sorted(base.rglob("*"))
                if p.is_file() and not any(part in SKIP_DIRS for part in p.relative_to(base).parts)
                and p.suffix.lower() not in BINARY_SUFFIXES and not p.is_symlink()
            )
    deduped: List[Path] = []
    seen: set[str] = set()
    for path in files:
        key = str(path.relative_to(base)) if path.is_relative_to(base) else str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped

def scan_secrets(
    rel: str, line_no: int, raw_line: str
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for rule, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(raw_line):
            value = match.group(1)
            if not value or len(value) < 8:
                continue
            name = f"{rule}"
            findings.append(
                {
                    "kind": "secret",
                    "file": rel,
                    "line": line_no,
                    "name": name,
                    "evidence": {
                        "rule": rule,
                        "redacted": redact(value),
                        "sha8": hashlib.sha256(value.encode("utf-8")).hexdigest()[:8],
                        "note": "issue-only, always: rotation is a human act",
                    },
                    "digest": candidate_digest("secret", rel, rule, hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]),
                    "disposition": "issue-only",
                }
            )
    return findings

def cmd_secrets(args: argparse.Namespace) -> int:
    base = Path.cwd()
    candidates: List[Dict[str, Any]] = []
    paths = args.paths or DEFAULT_SECRET_PATHS
    for path in iter_text_files(base, paths):
        rel = str(path.relative_to(base)) if path.is_relative_to(base) else str(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            candidates.extend(scan_secrets(rel, line_no, line))
    if args.state:
        candidates, deduped = apply_state(Path(args.state), candidates)
    else:
        deduped = 0
    emit(args, base, candidates, deduped, "Secrets are issue-only; the scan never files PRs.")
    if args.state and args.owner_note:
        eprint("[!] rotation and secret-link cleanup are human steps — the artifact")
        eprint("    names the finding and stops there.")
    return 1 if candidates else 0

def parse_manifest_pin(line: str) -> Optional[Tuple[str, str]]:
    match = PIN_RE.match(line.strip())
    if not match:
        return None
    return match.group(1), match.group(3)

def parse_version(version: str) -> List[int]:
    return [int(match) for match in VERSION_PART.findall(version)]

def vcmp(lhs: List[int], rhs: List[int]) -> int:
    """Pad both versions on the right (2.2 counts as 2.2.0) and compare."""
    width = max(len(lhs), len(rhs))
    left = lhs + [0] * (width - len(lhs))
    right = rhs + [0] * (width - len(rhs))
    if left < right:
        return -1
    return 0 if left == right else 1

def satisfies(version: List[int], spec: str) -> bool:
    for part in spec.split(","):
        part = part.strip().rstrip("*.")
        for op in ("<=", ">=", "<", ">", "==", "!="):
            if part.startswith(op):
                operand, target = op, part[len(op):]
                break
        else:
            operand, target = "==", part
        order = vcmp(version, parse_version(target))
        ok = {
            "<": order < 0,
            "<=": order <= 0,
            ">": order > 0,
            ">=": order >= 0,
            "==": order == 0,
            "!=": order != 0,
        }[operand]
        if not ok:
            return False
    return True

def spec_upper(spec: str) -> Optional[str]:
    """Upper bound of a vulnerable spec as a normalized version string."""
    for part in spec.split(","):
        part = part.strip().rstrip("*.")
        for op in ("<=", ">=", "<", ">", "==", "!="):
            if part.startswith(op):
                operand, target = op, part[len(op):]
                break
        else:
            operand, target = "==", part
        if operand in ("<", "<="):
            return target
    return None

def cmd_deps(args: argparse.Namespace) -> int:
    base = Path.cwd()
    manifest = base / args.manifest
    if not manifest.is_file():
        raise Fatal(f"manifest not found: {manifest}")
    advisory_path = base / args.advisory
    if not advisory_path.is_file():
        raise Fatal(f"advisory file not found: {advisory_path}")
    advisory = json.loads(advisory_path.read_text(encoding="utf-8"))
    candidates: List[Dict[str, Any]] = []
    lines = manifest.read_text(encoding="utf-8", errors="replace").splitlines()
    for line_no, line in enumerate(lines, start=1):
        pin = parse_manifest_pin(line)
        if not pin:
            if line.strip() and not line.strip().startswith("#") and args.strict:
                candidates.append(
                    {
                        "kind": "unpinned-dep",
                        "file": args.manifest,
                        "line": line_no,
                        "name": line.split("=", 1)[0].strip(),
                        "evidence": {"reason": "dependency has no == pin; the audit skips it"},
                        "digest": candidate_digest("unpinned", args.manifest, line.split("=", 1)[0].strip(), line.strip()),
                        "disposition": "candidate",
                    }
                )
            continue
        name, version = pin
        entries = advisory.get(name) or []
        for entry in entries:
            spec = entry.get("vulnerable", "")
            if not spec:
                continue
            if satisfies(parse_version(version), spec):
                upper = spec_upper(spec)
                candidates.append(
                    {
                        "kind": "dep-vuln",
                        "file": args.manifest,
                        "line": line_no,
                        "name": name,
                        "evidence": {
                            "version": version,
                            "advisory_id": entry.get("id", "unlisted"),
                            "spec": spec,
                            "fix_hint": entry.get("fix_hint", ""),
                            "fix_bounds": (">=" + upper) if upper else entry.get("fix_hint", ""),
                        },
                        "digest": candidate_digest(
                            "dep-vuln", args.manifest, name, f"{version}|{entry.get('id', 'unlisted')}"
                        ),
                        "disposition": "candidate",
                    }
                )
    if args.state:
        candidates, deduped = apply_state(Path(args.state), candidates)
    else:
        deduped = 0
    emit(args, base, candidates, deduped, "Vulnerable pins are repair candidates; bumps go through the oracle (bump).")
    return 1 if candidates else 0

def apply_state(state_path: Path, candidates: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    seen: Dict[str, Dict[str, Any]] = {}
    if state_path.is_file():
        try:
            seen = json.loads(state_path.read_text(encoding="utf-8")).get("seen", {})
        except json.JSONDecodeError:
            eprint(f"[!] state file unreadable, starting fresh: {state_path}")
            seen = {}
    fresh: List[Dict[str, Any]] = []
    deduped = 0
    for index, candidate in enumerate(candidates):
        digest = candidate["digest"]
        if digest in seen:
            deduped += 1
            continue
        seen[digest] = {"first_seen": time.strftime("%Y-%m-%d")}
        candidate = dict(candidate)
        candidate["index"] = index
        fresh.append(candidate)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"schema": SEEN_SCHEMA, "seen": seen}, indent=2) + "\n", encoding="utf-8")
    return fresh, deduped

def emit(
    args: argparse.Namespace,
    base: Path,
    candidates: List[Dict[str, Any]],
    deduped: int,
    note: str,
) -> None:
    payload = {
        "schema": SCHEMA,
        "tool": f"advisory.py {getattr(args, 'cmd', '')}",
        "candidates": candidates,
        "deduped": deduped,
        "note": note,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        if not candidates:
            print("advisory: 0 new findings" + (f" ({deduped} deduped)" if deduped else ""))
        for item in candidates:
            ev = item["evidence"]
            eprint(
                f"[{item['kind']}] {item['file']}:{item['line']} {item['name']}"
                f" {ev.get('redacted') or ev.get('secrecy', '')}"
                f" {ev.get('advisory_id', '')}".rstrip()
            )
        if deduped:
            eprint(f"[deduped {deduped} already-reported candidate(s)]")
    if args.scaffold_issue:
        for item in candidates:
            eprint(SCAFFOLD_SECRET if item["kind"] == "secret" else SCAFFOLD_DEP)

SCAFFOLD_SECRET = """\
## Security finding (secret): <file>:<line>
Evidence carries only the redacted fingerprint. Rotation is a human step;
the fleet never reproduces the secret in a PR, issue, or log.
"""

SCAFFOLD_DEP = """\
## Dependency finding: <name> pinned inside a vulnerable range in <manifest>:<line>
Bump through the oracle (advisory.py bump) — only a green scoped suite
becomes a PR; a red result becomes a Breakage issue delivered once.
"""

def cmd_bump(args: argparse.Namespace) -> int:
    base = Path.cwd()
    manifest = base / args.manifest
    if not manifest.is_file():
        raise Fatal(f"manifest not found: {manifest}")
    pin_re = re.compile(rf"^({re.escape(args.dep)}==)([^\s#]+)", re.MULTILINE)
    match = pin_re.search(manifest.read_text(encoding="utf-8", errors="replace"))
    if not match:
        raise Fatal(f"no == pin for {args.dep} in {args.manifest}")
    from_version = match.group(2)
    # copytree() refuses an existing destination, so copy into a child of a
    # fresh temp dir and skip run state (it never influences the oracle).
    parent = Path(tempfile.mkdtemp(prefix="advisory-bump-"))
    scratch = parent / "work"
    try:
        copy_where(base, scratch)
        copied_manifest = scratch / args.manifest
        text = copied_manifest.read_text(encoding="utf-8", errors="replace")
        new_text = pin_re.sub(rf"\g<1>{args.to}", text, count=1)
        copied_manifest.write_text(new_text, encoding="utf-8")
        try:
            proc = subprocess.run(
                ["/bin/bash", "-lc", args.test_cmd],
                cwd=str(scratch),
                capture_output=True,
                text=True,
                timeout=args.timeout,
            )
            rc = proc.returncode
            tail = ((proc.stdout or "") + (proc.stderr or "")).strip()[-800:]
        except subprocess.TimeoutExpired:
            rc, tail = 124, f"timed out after {args.timeout}s"
    finally:
        shutil.rmtree(parent, ignore_errors=True)
    changelog_excerpt = None
    if args.changelog:
        changelog_path = base / args.changelog
        if changelog_path.is_file():
            changelog_excerpt = find_changelog_excerpt(
                changelog_path.read_text(encoding="utf-8", errors="replace"), args.to
            )
    if rc == 0:
        payload = {
            "schema": SCHEMA,
            "tool": "advisory.py bump",
            "status": "green",
            "dep": args.dep,
            "from": from_version,
            "to": args.to,
            "command": args.test_cmd,
            "tail": tail,
            "evidence": {
                "changelog": changelog_excerpt or "no matching section found",
                "ran_in": "scratch copy (repo untouched)",
            },
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            eprint(f"[bump] green: {args.dep} {from_version}->{args.to} (scoped suite passed on the bumped copy)")
            if changelog_excerpt:
                eprint(f"[bump] changelog: {changelog_excerpt}")
        return EXIT_OK
    body = BOUNCE_BODY.format(
        dep=args.dep, frm=from_version, to=args.to, cmd=args.test_cmd, tail=tail or "(no output)",
    )
    eprint(body)
    if args.json:
        print(json.dumps({"status": "red", "dep": args.dep, "from": from_version, "to": args.to}, indent=2))
    return 1

def find_changelog_excerpt(text: str, version: str) -> Optional[str]:
    for pattern in (rf"^.*\b{re.escape(version)}\b.*$",):
        for match in re.finditer(pattern, text, re.MULTILINE):
            start = match.start()
            snippet = text[start : text.find("\n\n", start) if text.find("\n\n", start) != -1 else len(text)]
            return snippet.strip().splitlines()[0].strip()
        break
    return None

def copy_where(base: Path, scratch: Path) -> None:
    shutil.copytree(
        base,
        scratch,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".autonomous", "*.mutlock", "*.mutbak"),
    )

BOUNCE_BODY = """\
<!-- Breakage issue draft — oracle-first: no bump PR gets filed for this. -->
## Breakage: bump {dep} {frm}->{to} fails the scoped suite

**Command run on the bumped copy:**
```
{cmd}
```

**Failure excerpt:**
```
{tail}
```

**Suggested repro for the owner:**
```bash
pip install {dep}=={to}
{cmd}
```

Per references/08 (oracle-first): red bumps are issues, not PRs — breakage
is information, delivered once. Record it in the negative ledger and chase
again only when the target's hash changes.
"""

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    sub = parser.add_subparsers(dest="cmd", required=True)

    secrets_p = sub.add_parser("secrets", help="format-rule secret scan; issue-only, redacted by construction")
    secrets_p.add_argument("--paths", nargs="*", help="files/dirs to scan (default: the repo tree)")
    secrets_p.add_argument("--state", help="dedup state file under .autonomous/ (omitted: zero writes)")
    secrets_p.add_argument("--owner-note", action="store_true", help="print the human-side reminder")
    secrets_p.add_argument("--scaffold-issue", action="store_true", help="print a redacted issue draft per finding")
    secrets_p.add_argument("--json", action="store_true")
    secrets_p.set_defaults(func=cmd_secrets)

    deps_p = sub.add_parser("deps", help="audit == pins against a stub advisory JSON")
    deps_p.add_argument("--advisory", required=True, help="local advisory JSON (panel of vulnerable specs)")
    deps_p.add_argument("--manifest", required=True, help="requirements-style pin file")
    deps_p.add_argument("--strict", action="store_true", help="also flag non-==-pinned deps as unpinned")
    deps_p.add_argument("--state", help="dedup state file under .autonomous/ (omitted: zero writes)")
    deps_p.add_argument("--scaffold-issue", action="store_true", help="print an issue draft per finding")
    deps_p.add_argument("--json", action="store_true")
    deps_p.set_defaults(func=cmd_deps)

    bump_p = sub.add_parser("bump", help="oracle-first bump on a scratch copy of the tree")
    bump_p.add_argument("--dep", required=True)
    bump_p.add_argument("--to", required=True, help="target version to pin")
    bump_p.add_argument("--manifest", required=True)
    bump_p.add_argument("--test-cmd", required=True, help="scoped command run on the bumped copy")
    bump_p.add_argument("--changelog", help="changelog file to excerpt a risk note from")
    bump_p.add_argument("--timeout", type=int, default=300)
    bump_p.add_argument("--json", action="store_true")
    bump_p.set_defaults(func=cmd_bump)

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
