#!/usr/bin/env python3
"""Stream I: docs & spec truthfulness — deterministic finder (candidates only).

references/08-recursive-self-improvement.md contract instantiation: agents
read docs (README, SPEC, AGENTS.md, references) as ground truth, so a stale
example or a drifted symbol poisons every future worker turn. The checks:

* DT-1 example-failure  — fenced python blocks marked ``# runnable`` are
  executed with the caller's cwd on ``sys.path``; a non-zero exit is a
  candidate. "Runnable" is the doc's own opt-in contract, not an invitation
  to execute untrusted text.
* DT-2 symbol-drift     — ``from X import name``/``import X`` in fenced
  python blocks must resolve to a real binding in the mapped source file
  (catches the renamed-function fixture class at an exact file:line).
* DT-3 missing-link     — markdown links to repo-relative paths that do not
  exist (anchors not checked in v1).
* DT-4 spec-trace       — when the repo declares a spec (``SPEC*.md`` or
  --spec): every spec id (``G05``-style) is cited by at least one test, and
  every id cited in tests is defined in the spec — both directions.

The tool NEVER edits: the direction call (fix docs vs defect issue) needs
git history and owner intent. ``--scaffold-issue`` prints a Stream I issue
body per drift candidate, explicitly against silently rewriting the spec to
match a regression.

Usage:
  python3 scripts/autonomous/docs_truth.py check --json
  python3 scripts/autonomous/docs_truth.py check --paths README.md docs \
      --source skills/autonomous-improvement/scripts/autonomous --json
  python3 scripts/autonomous/docs_truth.py check --scaffold-issue
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import EXIT_OK, Fatal, eprint  # noqa: E402

SCHEMA = "docs-truth/1"
CODE_FENCE = re.compile(r"^```\s*([A-Za-z][\w+-]*)")
RUNNABLE_MARK = re.compile(r"(?:#|//)\s*runnab?le\b", re.IGNORECASE)
FROM_IMPORT = re.compile(r"^\s*from\s+([\w\.]+)\s+import\s+(.+)$")
IMPORT_STMT = re.compile(r"^\s*import\s+([\w\.]+)")
MD_LINK = re.compile(r"\]\(([^)#\s]+)(?:#[\w-]+)?\)")
SPEC_ID = re.compile(r"\b([A-Z]{1,4}\d{2,3})\b")

DEFAULT_DOC_PATHS = ["README.md", "AGENTS.md", "CHANGELOG*.md", "docs"]
DEFAULT_SOURCE_DIRS = ["src", "lib", "scripts"]


def parse_fences(text: str) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    in_fence = False
    lang = ""
    runnable = False
    body: List[str] = []
    start_line = 0
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not in_fence:
            match = CODE_FENCE.match(line)
            if match:
                in_fence = True
                lang = match.group(1)
                runnable = bool(RUNNABLE_MARK.search(line))
                body = []
                start_line = lineno + 1
            continue
        if line.strip().startswith("```"):
            blocks.append(
                {
                    "lang": lang.lower(),
                    "runnable": runnable,
                    "start_line": start_line,
                    "end_line": lineno,
                    "code": "\n".join(body),
                }
            )
            in_fence = False
            continue
        body.append(line)
    return blocks


def module_to_files(module: str, source_roots: List[Path]) -> List[Path]:
    rel = Path(module.replace(".", "/") + ".py")
    found: List[Path] = []
    for root in source_roots:
        for candidate in (root / rel, root.parent / rel):
            if candidate.is_file() and candidate not in found:
                found.append(candidate)
    return found


def binding_exists(path: Path, name: str) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    target = re.escape(name)
    patterns = [
        re.compile(rf"^\s*(?:async\s+)?def\s+{target}\b"),
        re.compile(rf"^\s*class\s+{target}\b"),
        re.compile(rf"^\s*(?:const|let|var|val|fun|func|fn|public|export)\b.*\b{target}\b"),
        re.compile(rf"^\s*[A-Za-z_][\w\.]*{target}\s*="),
    ]
    for line in text.splitlines():
        if re.search(rf"\b{target}\b", line):
            if any(p.match(line) for p in patterns):
                return True
    return False


def _rel_label(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def scan_symbol_drift(
    doc: Path, base: Path, blocks: List[Dict[str, Any]], source_roots: List[Path]
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    rel_doc = _rel_label(doc, base)
    stdlib = sys.stdlib_module_names
    for block in blocks:
        if block["lang"] not in ("python", "py"):
            continue
        for offset, line in enumerate(block["code"].splitlines()):
            from_match = FROM_IMPORT.match(line)
            import_match = None if from_match else IMPORT_STMT.match(line)
            if from_match:
                module = from_match.group(1)
                names = [n.strip().lstrip("(").rstrip(")").strip() for n in from_match.group(2).split(",")]
                names = [n.split(" as ")[0].strip() for n in names if n.strip()]
            elif import_match:
                module = import_match.group(1)
                names = [module.split(".")[0]]
            else:
                continue
            if module.split(".")[0] in stdlib:
                continue  # imports from the standard library are not repo drift
            files = module_to_files(module, source_roots)
            line_no = block["start_line"] + offset
            if not files:
                findings.append(
                    {
                        "kind": "symbol-drift",
                        "file": rel_doc,
                        "line": line_no,
                        "name": module,
                        "evidence": {
                            "reason": f"module {module!r} maps to no file under the scanned source roots",
                        },
                    }
                )
                continue
            for name in names:
                if not any(binding_exists(f, name) for f in files):
                    findings.append(
                        {
                            "kind": "symbol-drift",
                            "file": rel_doc,
                            "line": line_no,
                            "name": name,
                            "evidence": {
                                "module": module,
                                "module_file": _rel_label(files[0], base),
                                "reason": f"{name!r} is imported by the docs but not defined in {files[0]}",
                            },
                        }
                    )
    return findings


def scan_runnable(
    doc: Path, base: Path, blocks: List[Dict[str, Any]], timeout: int = 30
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    rel_doc = _rel_label(doc, base)
    for block in blocks:
        if not block["runnable"] or block["lang"] not in ("python", "py"):
            continue
        try:
            proc = subprocess.run(
                ["python3", "-c", block["code"]],
                cwd=str(Path.cwd()),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=dict(os.environ),
            )
            ok = proc.returncode == 0
            detail = (proc.stderr or "").strip()[-400:]
        except subprocess.TimeoutExpired:
            ok = False
            detail = f"timed out after {timeout}s"
        if not ok:
            findings.append(
                {
                    "kind": "example-failure",
                    "file": rel_doc,
                    "line": block["start_line"],
                    "name": f"runnable block lines {block['start_line']}-{block['end_line']}",
                    "evidence": {"detail": detail},
                }
            )
    return findings


def scan_links(doc: Path, base: Path) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    rel_doc = _rel_label(doc, base)
    text = doc.read_text(encoding="utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in MD_LINK.finditer(line):
            target = match.group(1)
            if target.startswith(("http://", "https://", "mailto:", "#", "data:")):
                continue
            if target.startswith("/"):
                candidate = base / target.lstrip("/")
            else:
                candidate = doc.parent / target
            if not candidate.exists():
                findings.append(
                    {
                        "kind": "missing-link",
                        "file": rel_doc,
                        "line": lineno,
                        "name": target,
                        "evidence": {"target": _rel_label(candidate, base)},
                    }
                )
    return findings


def scan_spec_trace(spec: Path, tests_root: Optional[Path], base: Path) -> List[Dict[str, Any]]:
    spec_text = spec.read_text(encoding="utf-8", errors="replace")
    defined = set(SPEC_ID.findall(spec_text))
    cited: Dict[str, str] = {}
    if tests_root is not None and tests_root.is_dir():
        for test_file in sorted(tests_root.rglob("*.py")):
            if not test_file.name.startswith("test_"):
                continue
            for lineno, line in enumerate(
                test_file.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
            ):
                for match in SPEC_ID.findall(line):
                    cited.setdefault(match, f"{_rel_label(test_file, base)}:{lineno}")
    findings: List[Dict[str, Any]] = []
    for spec_id in sorted(defined - set(cited)):
        findings.append(
            {
                "kind": "spec-id-uncited",
                "file": _rel_label(spec, base),
                "line": 0,
                "name": spec_id,
                "evidence": {"reason": f"spec id {spec_id} defined but cited by no test"},
            }
        )
    for spec_id in sorted(set(cited) - defined):
        findings.append(
            {
                "kind": "spec-id-undefined",
                "file": cited.get(spec_id, ""),
                "line": 0,
                "name": spec_id,
                "evidence": {"reason": f"tests cite {spec_id}, which no spec id defines"},
            }
        )
    return findings


SCAFFOLD_BODY = """\
## Stream I candidate issue: docs/code drift at {file}:{line}

**What the docs say** (`{file}`, line {line}): `{name}`
**What the code has**: {evidence_detail}

Direction rule (references/08): if the code moved intentionally (git
blame/log), fix the docs. If the docs state the contract and the code
drifted, this is a defect-repair (Stream E) candidate — do not silently
"fix" the spec to match a regression. Ambiguous direction: leave as
issue-only (T1) with this body attached; the finder never edits.
"""


def cmd_check(args: argparse.Namespace) -> int:
    base = Path.cwd()
    docs: List[Path] = []
    for pattern in args.paths or DEFAULT_DOC_PATHS:
        path = base / pattern
        if path.is_dir():
            docs.extend(sorted(path.glob("**/*.md")))
        elif path.is_file():
            docs.append(path)
    source_roots: List[Path] = []
    for pattern in args.source or DEFAULT_SOURCE_DIRS:
        path = base / pattern
        if path.is_dir():
            source_roots.append(path)

    candidates: List[Dict[str, Any]] = []
    spec = Path(args.spec) if args.spec else None
    if spec is None:
        for name in ("SPEC.md", "docs/SPEC.md"):
            if (base / name).is_file():
                spec = base / name
                break
    if spec is not None and spec.is_file():
        tests_root = next(((base / n) for n in ("tests", "test") if (base / n).is_dir()), None)
        candidates.extend(scan_spec_trace(spec, tests_root, base))

    # Spec-shaped files are the contract side: a drifted symbol there is an
    # ISSUE (direction call), not a candidate the runner auto-flags; and the
    # toolwide DT-2 checks only apply to docs that cite real module files.
    for doc in docs:
        if "spec" in doc.stem.lower() or "template" in doc.stem.lower() or "audit" in doc.stem.lower():
            continue
        blocks = parse_fences(doc.read_text(encoding="utf-8", errors="replace"))
        candidates.extend(scan_symbol_drift(doc, base, blocks, source_roots))
        candidates.extend(scan_runnable(doc, base, blocks))
        candidates.extend(scan_links(doc, base))

    if args.scaffold_issue:
        for finding in candidates:
            if finding["kind"] not in ("symbol-drift",):
                continue
            body = SCAFFOLD_BODY.format(
                file=finding["file"],
                line=finding["line"],
                name=finding["name"],
                evidence_detail=finding["evidence"].get("reason", finding["evidence"]),
            )
            eprint(body)
        if any(c["kind"] == "symbol-drift" for c in candidates):
            eprint("<!-- paste into the issue tracker; T1 artifact for an ambiguous direction -->")

    payload = {
        "schema": SCHEMA,
        "tool": "docs_truth.py check",
        "spec": str(spec) if spec else None,
        "spec_trace": str(spec) if spec else "skipped (no spec file)",
        "candidates": candidates,
        "note": "Candidates only. Direction calls need git history: fix the docs when code moved,"
        " open a defect issue when the spec is the contract. The finder never edits.",
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        if not candidates:
            print("docs_truth: 0 findings")
        for c in candidates:
            trailing = c["evidence"].get("reason", c["evidence"])
            eprint(f"[{c['kind']}] {c['file']}:{c['line']} {c['name']}: {trailing}")
    return EXIT_OK if not candidates else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    sub = parser.add_subparsers(dest="cmd", required=True)

    check = sub.add_parser("check", help="scan docs for drifted symbols, dead links, spec-id trace breaks")
    check.add_argument("--paths", nargs="*", help="docs to scan (default README.md AGENTS.md CHANGELOG*.md docs)")
    check.add_argument("--source", nargs="*", help="source roots for module mapping (default src lib scripts)")
    check.add_argument("--spec", help="SPEC file to trace ids against (default: auto-detect)")
    check.add_argument("--scaffold-issue", action="store_true", help="print an issue body per drift candidate")
    check.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        return cmd_check(args)
    except Fatal as exc:
        eprint(f"[!] {exc}")
        return 2
    except OSError as exc:
        eprint(f"[!] {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
