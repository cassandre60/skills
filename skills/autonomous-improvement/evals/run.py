#!/usr/bin/env python3
"""Behavioral eval harness for the autonomous-improvement skill.

Two layers of verification (see evals/README.md):

* ``--deterministic`` — script-level checks against fresh temporary copies of
  the fixtures. No agent involved; fast enough for CI; proves the script layer
  still behaves the way each case's expected/forbidden paragraphs describe.
  Cases with no scripted checks report as ``agent-only``.
* ``--plan-agent`` — prints the headless agent protocol (with-skill vs
  without-skill pairing, plus the description trigger set) for a harness/model
  pair. Agent evals run on demand on a real model; results live as JSON under
  ``evals/results/`` using the recorded-run schema in evals/README.md.

``--negative-control`` proves the harness can fail: it re-runs one case with a
deliberately impossible expectation and demands that the check FAIL.

Exit codes: 0 when the requested mode holds together (all deterministic checks
pass, or the negative control is correctly red), 1 otherwise.

Examples::

    python3 evals/run.py --deterministic
    python3 evals/run.py --deterministic --case E7-5-open-pr-claim
    python3 evals/run.py --negative-control --case E7-8-protected-row
    python3 evals/run.py --plan-agent trigger-set --harness opencode --model opencode/glm-5.3-flash-free
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import typing

SKILL_ROOT = pathlib.Path(__file__).resolve().parent.parent
EVALS_DIR = SKILL_ROOT / "evals"
EVALS_JSON = EVALS_DIR / "evals.json"

NEGATIVE_CONTROL_SENTINEL = "__NEGATIVE_CONTROL_IMPOSSIBLE__"

# Deterministic halves of the behavioral cases. A case without an entry here is
# agent-graded only (results reported as "agent-only").
#
# Check kinds:
#   exit-zero     — argv must exit 0
#   text-includes — argv stdout must contain every string in "includes"
#   json-subset   — argv stdout (JSON) at "path" must be a dict containing
#                   exactly the key/value pairs in "subset"
#   json-eq       — value at "path" must equal "value" exactly
CASE_CHECKS: typing.Dict[str, typing.List[typing.Dict[str, typing.Any]]] = {
    "E7-3-dedup-second-run": [
        {
            "name": "denied-second-run",
            "kind": "json-subset",
            "argv": ["python3", "scripts/autonomous/ledger.py", "next", "--stream", "A", "--json"],
            "path": "skipped[0]",
            "subset": {
                "target": "src/text.py",
                "skip": "already audited at current content hash",
            },
        },
        {
            "name": "no-eligible-candidates",
            "kind": "json-eq",
            "argv": ["python3", "scripts/autonomous/ledger.py", "next", "--stream", "A", "--json"],
            "path": "candidates",
            "value": [],
        },
    ],
    "E7-5-open-pr-claim": [
        {
            "name": "claimed-by-open-pr",
            "kind": "json-subset",
            "argv": ["python3", "scripts/autonomous/ledger.py", "next", "--stream", "A", "--json"],
            "path": "skipped[0]",
            "subset": {
                "target": "src/scheduler.py",
                "skip": "open PR #7 test/scheduler",
            },
        },
    ],
    "E7-8-protected-row": [
        {
            "name": "record-renders-row-text",
            "kind": "text-includes",
            "argv": [
                "python3", "scripts/autonomous/ledger.py", "record",
                "--stream", "A", "--target", "src/text.py",
                "--status", "SOUND",
                "--summary", "audited; sound; row text goes in the PR body only",
            ],
            "includes": ["A (Mutation)", "`src/text.py`"],
        },
    ],
    "E7-7-red-ci": [
        {
            "name": "pr-state-red-in-diff",
            "kind": "json-subset",
            "argv": ["python3", "scripts/autonomous/fleet.py", "pr-state", "--json"],
            "path": "[0]",
            "subset": {"pr": 42, "checks": "red", "red_kind": "in-diff"},
        },
    ],
    "E7-6-quota-rotation": [
        {
            "name": "watchdog-rotates-on-quota",
            "kind": "exit-zero",
            "argv": ["bash", str(SKILL_ROOT / "tests" / "watchdog_rotation_test.sh")],
        },
    ],
}

# Description trigger set: each prompt x 3 runs, a fresh session each time,
# with the skill list in the model's system context. should-trigger prompts
# must select this skill at a rate above the agreed threshold; should-not
# prompts must not bleed into it. Rates belong in a committed results table
# (evals/README.md section "Recorded runs") whenever the set is executed.
TRIGGER_PROMPTS: typing.Dict[str, typing.List[str]] = {
    "should-trigger": [
        "Tests pass in this repo but I don't trust them.",
        "Audit whether our tests could actually fail on a real bug.",
        "Run one improvement round over this repo and show me the weak spots.",
        "Set up an unattended fleet to grind issue triage on free models overnight.",
        "@autonomous-improvement",
    ],
    "should-not-trigger": [
        "Ship the new login feature.",
        "Add dark mode to the settings screen.",
    ],
}

_AGENT_TEMPLATES: typing.Dict[str, str] = {
    "opencode": 'opencode run --auto --model {model} "{prompt}"',
    "claude": 'claude -p --model {model} "{prompt}"',
    "codex": 'codex exec --model {model} "{prompt}"',
}


def load_cases() -> typing.List[typing.Dict[str, typing.Any]]:
    data = json.loads(EVALS_JSON.read_text(encoding="utf-8"))
    if int(data.get("version", 0)) < 2:
        raise SystemExit(
            f"{EVALS_JSON}: expected version >= 2 (run.py pairs with the v2 case set)"
        )
    return list(data["cases"])


def resolve_path(doc: typing.Any, path: str) -> typing.Any:
    """Tiny resolver for "[0].skipped[0].target"-style dotted/index paths."""
    cur = doc
    token = ""
    for ch in path.strip() + ".":
        if ch in ".[]":
            if token.isdigit():
                if not isinstance(cur, list):
                    raise KeyError(f"cannot index {token!r} into {type(cur).__name__}")
                cur = cur[int(token)]
            elif token:
                if not isinstance(cur, dict):
                    raise KeyError(f"cannot descend {token!r} into {type(cur).__name__}")
                cur = cur[token]
            token = ""
        else:
            token += ch
    return cur


def substitute(text: str, mapping: typing.Dict[str, str]) -> str:
    for key, value in mapping.items():
        text = text.replace(key, value)
    return text


def prepare_repo(fixture: str, work_root: pathlib.Path) -> pathlib.Path:
    src = EVALS_DIR / "fixtures" / fixture
    if not src.is_dir():
        # Matrix fixtures split across fixtures/ (dev) and heldout/ (held-out).
        heldout = EVALS_DIR / "heldout" / fixture
        if heldout.is_dir():
            src = heldout
    if not src.is_dir():
        raise SystemExit(f"missing fixture dir: {src}")
    dst = work_root / fixture
    shutil.copytree(src, dst)
    return dst


def run_setup(
    case: typing.Dict[str, typing.Any], repo: pathlib.Path
) -> typing.List[typing.Dict[str, typing.Any]]:
    mapping = {"<skill>": str(SKILL_ROOT), "<repo>": str(repo)}
    # The fixture repos are throwaway copies; a CI runner or bare container
    # may have no git identity at all ("Author identity unknown" kills the
    # git-commit setup line orthodoxly). Pin one for the harness unless the
    # caller already set it.
    env = dict(os.environ)
    for var, default in (
        ("GIT_AUTHOR_NAME", "eval-harness"),
        ("GIT_AUTHOR_EMAIL", "eval-harness@example.invalid"),
        ("GIT_COMMITTER_NAME", "eval-harness"),
        ("GIT_COMMITTER_EMAIL", "eval-harness@example.invalid"),
    ):
        env.setdefault(var, default)
    artifacts = []
    for index, raw in enumerate(case.get("setup", [])):
        proc = subprocess.run(
            ["bash", "-c", substitute(raw, mapping)],
            cwd=str(repo),
            capture_output=True,
            text=True,
            env=env,
        )
        artifacts.append(
            {
                "index": index,
                "command": raw,
                "returncode": proc.returncode,
                "stderr_tail": (proc.stderr or "")[-400:],
            }
        )
    return artifacts


def case_env(case: typing.Dict[str, typing.Any], repo: pathlib.Path) -> typing.Dict[str, str]:
    env = dict(os.environ)
    mapping = {"<repo>": str(repo), "<skill>": str(SKILL_ROOT)}
    for key, raw in (case.get("environment") or {}).items():
        if key != "PATH_PREPEND" and not key.isupper():
            continue  # documentation-only entries like "note"
        value = substitute(str(raw), mapping)
        if key == "PATH_PREPEND":
            env["PATH"] = value + os.pathsep + env.get("PATH", "")
        else:
            env[key] = value
    return env


def run_check(
    check: typing.Dict[str, typing.Any], repo: pathlib.Path, env: typing.Dict[str, str]
) -> typing.Dict[str, typing.Any]:
    kind = check["kind"]
    proc = subprocess.run(
        check["argv"], cwd=str(repo), capture_output=True, text=True, env=env
    )
    out = proc.stdout or ""
    ok = False
    detail = ""
    if kind == "exit-zero":
        ok = proc.returncode == 0
        detail = f"exit {proc.returncode}"
        if not ok:
            detail += ": " + (out + (proc.stderr or ""))[-300:]
    elif kind == "text-includes":
        missing = [s for s in check.get("includes", []) if s not in out]
        ok = proc.returncode == 0 and not missing
        detail = f"exit {proc.returncode}"
        if missing:
            detail += "; stdout missing: " + repr(missing)
    elif kind in ("json-subset", "json-eq"):
        try:
            doc = json.loads(out) if out.strip() else None
        except json.JSONDecodeError as exc:
            doc = None
            detail = f"invalid JSON on stdout: {exc}"
        if doc is None and not detail:
            detail = f"no JSON on stdout (exit {proc.returncode})"
        if doc is not None:
            try:
                node = resolve_path(doc, check.get("path", ".")) if check.get("path") else doc
                if kind == "json-subset":
                    if not isinstance(node, dict):
                        ok = False
                        detail = f"path {check['path']!r} is {type(node).__name__}, expected object"
                    else:
                        missing = {
                            key: value
                            for key, value in check["subset"].items()
                            if node.get(key) != value
                        }
                        actual = {key: node.get(key) for key in check["subset"]}
                        ok = not missing
                        detail = (
                            "matched"
                            if ok
                            else f"expected subset {check['subset']!r}; got {actual!r}"
                        )
                else:
                    ok = node == check["value"]
                    detail = "matched" if ok else f"expected {check['value']!r}; got {node!r}"
            except (KeyError, IndexError) as exc:
                detail = f"path {check.get('path')!r} unresolvable: {exc}"
    else:
        detail = f"unknown check kind {kind!r}"

    return {
        "name": check.get("name") or kind,
        "kind": kind,
        "returncode": proc.returncode,
        "status": "pass" if ok else "fail",
        "detail": detail,
    }


def one_case(
    case: typing.Dict[str, typing.Any],
    case_checks: typing.List[typing.Dict[str, typing.Any]],
    repo: pathlib.Path,
) -> typing.Dict[str, typing.Any]:
    result: typing.Dict[str, typing.Any] = {
        "id": case["id"],
        "fixture": case.get("fixture"),
        "checks": [],
    }
    failures: typing.List[str] = []
    setup_out = run_setup(case, repo)
    result["setup"] = setup_out
    for bad in [s for s in setup_out if s["returncode"] != 0]:
        failures.append(f"setup[{bad['index']}] exit {bad['returncode']}: {bad['stderr_tail']!r}")
    env = case_env(case, repo)
    for check in case_checks:
        out = run_check(check, repo, env)
        result["checks"].append(out)
        if out["status"] == "fail":
            failures.append(f"check {out['name']} failed ({out['detail']})")
    if not case_checks and not failures:
        result["status"] = "agent-only"
    else:
        result["status"] = "pass" if not failures else "fail"
        if failures:
            result["failures"] = failures
    return result


def run_deterministic(
    selected: typing.List[typing.Dict[str, typing.Any]],
    work_parent: typing.Optional[str],
    keep: bool,
) -> typing.List[typing.Dict[str, typing.Any]]:
    results: typing.List[typing.Dict[str, typing.Any]] = []
    for case in selected:
        checks = CASE_CHECKS.get(case["id"], [])
        work_root = pathlib.Path(
            tempfile.mkdtemp(prefix="evalrun-", dir=work_parent)
        )
        try:
            repo = prepare_repo(case["fixture"], work_root)
            results.append(one_case(case, checks, repo))
        finally:
            if keep:
                print(f"[keep] fixture copy: {work_root}", file=sys.stderr)
            else:
                shutil.rmtree(work_root, ignore_errors=True)
    return results


def negative_control(
    selected: typing.List[typing.Dict[str, typing.Any]],
) -> typing.Tuple[typing.List[typing.Dict[str, typing.Any]], bool]:
    """Re-run the first scripted case with an impossible expectation.

    The harness must FAIL the mutated check; anything else means the harness
    cannot fail and its green results are worthless.
    """
    for case in selected:
        checks = CASE_CHECKS.get(case["id"], [])
        if not checks:
            continue
        mutated = json.loads(json.dumps(checks))
        target = next((c for c in mutated if c["kind"] != "exit-zero"), mutated[0])
        if target["kind"] == "json-subset":
            for key in target["subset"]:
                target["subset"][key] = NEGATIVE_CONTROL_SENTINEL
        elif target["kind"] == "json-eq":
            target["value"] = NEGATIVE_CONTROL_SENTINEL
        elif target["kind"] == "text-includes":
            target["includes"] = [NEGATIVE_CONTROL_SENTINEL]
        else:
            target["argv"] = ["false"]

        work_root = pathlib.Path(tempfile.mkdtemp(prefix="evalneg-"))
        try:
            repo = prepare_repo(case["fixture"], work_root)
            result = one_case(case, [target], repo)
        finally:
            shutil.rmtree(work_root, ignore_errors=True)
        red = any(c["status"] == "fail" for c in result["checks"])
        return [result], red
    return [], False


def agent_command(harness: str, model: str, prompt: str) -> str:
    return _AGENT_TEMPLATES[harness].format(model=model, prompt=prompt.replace('"', '\\"'))


def plan_agent(case_id: str, harness: str, model: str) -> None:
    if harness not in _AGENT_TEMPLATES:
        raise SystemExit(f"--harness must be one of: {', '.join(sorted(_AGENT_TEMPLATES))}")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    model_slug = model.replace("/", "-") or "(model)"
    print("Agent eval protocol — headless, fresh session per run.")
    print(f"Record results under evals/results/{case_id}__{harness}__{model_slug}__{stamp}.json")
    print("Recorded-run schema:")
    print('  {"case": ..., "harness": ..., "model": ..., "with_skill": bool,')
    print('   "transcript": "...", "observed": [...], "forbidden_seen": [...],')
    print('   "verdict": "pass|fail|analysis"}')
    print()
    if case_id == "trigger-set":
        print("Trigger-set protocol — 3 runs per prompt, fresh session each;")
        print("grade only whether the model picks this skill from its skill list:")
        for group, prompts in TRIGGER_PROMPTS.items():
            for prompt in prompts:
                print(f"  [{group}] {agent_command(harness, model, prompt)}")
        return
    case = next((c for c in load_cases() if c["id"] == case_id), None)
    if case is None:
        raise SystemExit(f"unknown case: {case_id}")
    fixture = case["fixture"]
    prompt = case["prompt"]
    print(f"Pair A vs B on fixture evals/fixtures/{fixture}:")
    print(f"  A: {agent_command(harness, model, prompt)}")
    print("  B: same command with the skill NOT present in the session,")
    print("     otherwise identical setup, repo, and prompt.")
    print("  Grade A against the case's expected list; any forbidden hit fails")
    print("  outright. B exists to prove the behavior is skill-borne, not the")
    print("  base model's default. Record both.")
    print()
    print("Expected (first items):")
    for item in case.get("expected", [])[:3]:
        print(f"  - {item}")
    print("Forbidden:")
    for item in case.get("forbidden", [])[:3]:
        print(f"  - {item}")


def print_summary(results: typing.List[typing.Dict[str, typing.Any]]) -> int:
    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] == "fail")
    agent_only = sum(1 for r in results if r["status"] == "agent-only")
    for r in results:
        badge = {"pass": "OK  ", "fail": "FAIL", "agent-only": "agent"}[r["status"]]
        print(f"  [{badge}] {r['id']} (fixture: {r['fixture']})")
        for check in r.get("checks", []):
            mark = "OK  " if check["status"] == "pass" else "FAIL"
            print(f"           {mark} {check['name']}: {check['detail']}")
        for failure in r.get("failures", []):
            print(f"           -- {failure}")
    print(
        f"summary: {passed} passed, {failed} failed, "
        f"{agent_only} agent-only (graded transcripts, no scripted checks)"
    )
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--deterministic", action="store_true", help="run script-level checks (default action)")
    parser.add_argument("--case", action="append", dest="cases", help="limit to one case id (repeatable)")
    parser.add_argument("--list", action="store_true", help="list each case's deterministic checks")
    parser.add_argument("--negative-control", action="store_true", help="expect the mutated check to FAIL")
    parser.add_argument("--record-file", type=str, help="write results JSON to this path")
    parser.add_argument("--work-parent", type=str, help="directory for temp fixture copies")
    parser.add_argument("--keep", action="store_true", help="keep the temp fixture copy (debug)")
    parser.add_argument("--plan-agent", type=str, metavar="CASE", help="print the agent protocol for a case (or 'trigger-set')")
    parser.add_argument("--harness", type=str, default="opencode", help="opencode | claude | codex (for --plan-agent)")
    parser.add_argument("--model", type=str, default="", help="model id, e.g. opencode/glm-5.3-flash-free (for --plan-agent)")
    args = parser.parse_args()

    if args.plan_agent:
        plan_agent(args.plan_agent, args.harness, args.model)
        return 0

    if args.list:
        for case_id in [c["id"] for c in load_cases()]:
            names = [c["name"] for c in CASE_CHECKS.get(case_id, [])]
            tag = ", ".join(names) if names else "agent-only"
            print(f"  {case_id}: {tag}")
        return 0

    selected = load_cases()
    if args.cases:
        known = {c["id"] for c in selected}
        missing = set(args.cases) - known
        if missing:
            raise SystemExit(f"unknown case id(s): {', '.join(sorted(missing))}")
        selected = [c for c in selected if c["id"] in set(args.cases)]

    if args.negative_control:
        results, red = negative_control(selected)
        if not results:
            print("NEGATIVE-CONTROL: no scripted checks among the selected cases; nothing to prove.")
            return 1
        print_summary(results)
        if red:
            print("RED-PROOF: the impossible expectation correctly FAILED — the harness can fail.")
            return 0
        print("NEGATIVE-CONTROL: mutated check passed — the harness cannot fail; greens are worthless.")
        return 1

    results = run_deterministic(selected, args.work_parent, args.keep)
    if args.record_file:
        payload = {
            "tool": "evals/run.py",
            "mode": "deterministic",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "cases": results,
            "summary": {
                "passed": sum(1 for r in results if r["status"] == "pass"),
                "failed": sum(1 for r in results if r["status"] == "fail"),
                "agent_only": sum(1 for r in results if r["status"] == "agent-only"),
            },
        }
        record = pathlib.Path(args.record_file)
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"[record] {record}")
    return 1 if print_summary(results) else 0


if __name__ == "__main__":
    sys.exit(main())
