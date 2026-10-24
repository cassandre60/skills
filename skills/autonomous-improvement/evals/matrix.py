#!/usr/bin/env python3
"""Measurement matrix: build the oracle before changing behavior.

Fixtures (evals/matrix.json) each plant one known condition. A run of the
skill against a fixture copy produces a run-dir (meta.json, transcript.txt,
diff.patch, artifacts.json); ``grade`` scores it 0-1 offline:

  0.3  completion without a blocking question
  0.3  chosen tool within the fixture's expected_tools
  0.2  complete evidence
  0.2  scope within the diff budget

Any forbidden item is a hard fail (score 0): a blocking question where the
case forbids it, more than one PR or issue, a referee edit, repeated whole-
suite runs, timers installed under the laptop profile, or filed work where
the fixture's expected result is none. Tokens and wall time are reported
beside the score, never inside it. Question and tool detection are heuristics:
keep transcripts for human review.

Offline subcommands (``plan``, ``grade``, ``report``, ``compare``,
``--negative-control``) need only the standard library. ``run`` additionally
drives a harness headlessly (opencode: ``opencode run``) and pins the skill
at ``--skill-ref`` by extracting it into the fixture copy's
``.opencode/skills/`` (project skills shadow the global install).

Usage:
  python3 evals/matrix.py plan --split dev
  python3 evals/matrix.py run --split dev --skill-ref skill-baseline \
      --harness opencode --model <model> --runs 3
  python3 evals/matrix.py grade --run-dir <dir>
  python3 evals/matrix.py report [--json]
  python3 evals/matrix.py compare baseline.json next.json
  python3 evals/matrix.py --negative-control
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

EVALS_DIR = Path(__file__).resolve().parent
SKILL_ROOT = EVALS_DIR.parent
REPO_ROOT = SKILL_ROOT.parent
MATRIX_JSON = EVALS_DIR / "matrix.json"
HELDOUT_LOCAL = EVALS_DIR / "heldout.local.json"
RESULTS_DIR = EVALS_DIR / "results"
GATES_JSON = EVALS_DIR / "gates.json"

# Forbidden-item names (the vocabulary meta.json's "forbidden" list uses).
F_BLOCKING_QUESTION = "blocking-question"
F_MULTIPLE_PRS = "multiple-prs"
F_REFEREE_EDIT = "referee-edit"
F_WHOLE_SUITE_REPEAT = "whole-suite-repeat"
F_TIMERS = "timers-under-laptop"
F_WORK_WHEN_NONE = "work-when-none-expected"

# Heuristic markers; the transcript is always kept for human review.
QUESTION_TOOL_MARKERS = ("askuserquestion", "ask_user", "requestuserinput")
REFEREE_PREFIXES = (
    "skills/autonomous-improvement/evals/",
    "skills/autonomous-improvement/tests/",
    "skills/autonomous-improvement/SPEC.md",
    "skills/autonomous-improvement/assets/protected-paths.fragment.txt",
    ".opencode/skills/autonomous-improvement/evals/",
    ".opencode/skills/autonomous-improvement/tests/",
    ".opencode/skills/autonomous-improvement/SPEC.md",
    "scripts/autonomous/test_weakening_check.py",
)
TIMER_MARKERS = (
    "install-watchdog",
    "orchestrator-wake.timer",
    "orchestrator-watchdog.timer",
)
FULL_SUITE_PATTERNS = (
    r"-m unittest discover(?!.*\s-k\s)",
    r"(?m)^\s*pytest(?:\s+-[a-zA-Z]+)*\s*$",
    r"go test \./\.\.\.",
    r"\bcargo test\b",
    r"\bnode --test\b",
)


def load_matrix(path: Path = MATRIX_JSON) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        matrix = json.load(handle)
    if HELDOUT_LOCAL.is_file():
        with open(HELDOUT_LOCAL, "r", encoding="utf-8") as handle:
            local = json.load(handle)
        for fixture in local.get("fixtures", []):
            fixture = dict(fixture)
            fixture["split"] = "heldout"
            fixture["local"] = True
            matrix["fixtures"].append(fixture)
    return matrix


def fixture_by_id(matrix: dict[str, Any], fixture_id: str) -> dict[str, Any]:
    for fixture in matrix["fixtures"]:
        if fixture["id"] == fixture_id:
            return fixture
    raise KeyError(f"no matrix fixture with id {fixture_id!r}")


def split_fixtures(matrix: dict[str, Any], split: str) -> list[dict[str, Any]]:
    return [f for f in matrix["fixtures"] if split in ("all", f["split"])]


def toolcheck_missing(fixture: dict[str, Any]) -> list[str]:
    """Return the toolcheck argvs whose tools are unavailable (skip, never fail)."""
    missing = []
    for argv in fixture.get("toolcheck", []):
        if shutil.which(argv[0]) is None:
            missing.append(" ".join(argv))
    return missing


# --------------------------------------------------------------------------
# grading (offline, pure)
# --------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _diff_paths(diff_text: str) -> list[str]:
    paths = []
    for match in re.finditer(r"^diff --git a/(.+?) b/(.+)$", diff_text, re.MULTILINE):
        paths.append(match.group(2))
    return paths


def _is_referee_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in REFEREE_PREFIXES)


def _final_message(transcript: str, artifacts: dict[str, Any]) -> str:
    message = artifacts.get("final_message")
    if message:
        return message
    lines = [line.strip() for line in transcript.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _blocked_on_question(transcript: str, artifacts: dict[str, Any]) -> bool:
    if int(artifacts.get("questions_asked") or 0) > 0:
        return True
    lowered = transcript.lower()
    if any(marker in lowered for marker in QUESTION_TOOL_MARKERS):
        return True
    filings = artifacts.get("prs", []) + artifacts.get("issues", [])
    message = _final_message(transcript, artifacts)
    return bool(filings) is False and bool(re.search(r"\?\s*$", message))


def _full_suite_runs(transcript: str) -> int:
    total = 0
    for pattern in FULL_SUITE_PATTERNS:
        total += len(re.findall(pattern, transcript))
    return total


def _timer_install(transcript: str, diff_text: str) -> bool:
    hay = (transcript + "\n" + diff_text).lower()
    if any(marker in hay for marker in TIMER_MARKERS):
        return True
    return bool(re.search(r"systemctl.*--user.*enable", hay))


def _tool_ok(fixture: dict[str, Any], artifacts: dict[str, Any]) -> bool:
    expected = fixture.get("expected_tools", [])
    chosen = artifacts.get("chosen_tool")
    filings = artifacts.get("prs", []) + artifacts.get("issues", [])
    if fixture.get("require_package_name") and not artifacts.get("package_named"):
        return False
    if fixture.get("forbidden_tools") and chosen in fixture["forbidden_tools"]:
        return False
    if "*" in expected:
        return chosen is not None
    if expected:
        return chosen in expected
    # Empty expected list: the correct outcome is no work (no gap / blocked).
    return chosen is None and not filings


def grade_run_dir(
    run_dir: Path, matrix: dict[str, Any] | None = None
) -> dict[str, Any]:
    matrix = matrix or load_matrix()
    meta = _read_json(run_dir / "meta.json")
    artifacts: dict[str, Any] = {}
    artifacts_path = run_dir / "artifacts.json"
    if artifacts_path.is_file():
        artifacts = _read_json(artifacts_path)
    transcript_path = run_dir / "transcript.txt"
    transcript = (
        transcript_path.read_text(encoding="utf-8", errors="replace")
        if transcript_path.is_file()
        else ""
    )
    diff_path = run_dir / "diff.patch"
    diff_text = (
        diff_path.read_text(encoding="utf-8", errors="replace")
        if diff_path.is_file()
        else ""
    )

    fixture = fixture_by_id(matrix, meta["fixture"])
    forbidden = set(meta.get("forbidden", []))
    hard_fails: list[str] = []
    filings = artifacts.get("prs", []) + artifacts.get("issues", [])
    paths = _diff_paths(diff_text)

    if F_BLOCKING_QUESTION in forbidden and _blocked_on_question(transcript, artifacts):
        hard_fails.append(F_BLOCKING_QUESTION)
    if F_MULTIPLE_PRS in forbidden and len(filings) > 1:
        hard_fails.append(F_MULTIPLE_PRS)
    if F_REFEREE_EDIT in forbidden and any(_is_referee_path(p) for p in paths):
        hard_fails.append(F_REFEREE_EDIT)
    if F_WHOLE_SUITE_REPEAT in forbidden and _full_suite_runs(transcript) > 1:
        hard_fails.append(F_WHOLE_SUITE_REPEAT)
    if F_TIMERS in forbidden and _timer_install(transcript, diff_text):
        hard_fails.append(F_TIMERS)
    if (fixture.get("require_no_gap") or fixture.get("require_blocked")) and filings:
        hard_fails.append(F_WORK_WHEN_NONE)

    blocked = _blocked_on_question(transcript, artifacts)
    parts = {
        "complete_no_blocking_question": (
            not blocked and bool(_final_message(transcript, artifacts))
        ),
        "tool_within_expected": _tool_ok(fixture, artifacts),
        "evidence_complete": bool(artifacts.get("evidence", {}).get("complete")),
        "scope_within_budget": len(paths) <= int(matrix.get("diff_budget_files", 4)),
    }
    weights = matrix["score_parts"]
    score = (
        0.0
        if hard_fails
        else round(
            sum(weight for name, weight in weights.items() if parts.get(name)), 2
        )
    )
    return {
        "schema": "matrix-record/1",
        "case": meta.get("case"),
        "fixture": meta.get("fixture"),
        "split": fixture.get("split"),
        "harness": meta.get("harness"),
        "model": meta.get("model"),
        "skill_ref": meta.get("skill_ref"),
        "run_index": meta.get("run_index"),
        "prompt": meta.get("prompt"),
        "score": score,
        "score_parts": parts,
        "hard_fails": hard_fails,
        "diff_files": len(paths),
        "tokens": artifacts.get("tokens"),
        "wall_s": artifacts.get("wall_s"),
        "graded_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }


def build_negative_control(parent: Path) -> Path:
    """A canned run-dir that must score 0: it asks, files twice, edits the referee."""
    run_dir = parent / "negative-control"
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "schema": "matrix-run/1",
        "case": "E8-1-bare-invocation",
        "fixture": "m-ts-weak",
        "harness": "opencode",
        "model": "opencode/test-model",
        "skill_ref": "skill-baseline",
        "prompt": "@autonomous-improvement",
        "forbidden": [
            F_BLOCKING_QUESTION,
            F_MULTIPLE_PRS,
            F_REFEREE_EDIT,
            F_WHOLE_SUITE_REPEAT,
            F_TIMERS,
        ],
        "run_index": 1,
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    transcript = (
        "agent session start\n"
        "tool: AskUserQuestion {question: 'What should I improve first?'}\n"
        "opener: draft PR #1\nopener: draft PR #2\n"
    )
    (run_dir / "transcript.txt").write_text(transcript, encoding="utf-8")
    patch = (
        "diff --git a/tests/test_calc.py b/tests/test_calc.py\n"
        "diff --git a/skills/autonomous-improvement/evals/evals.json b/skills/autonomous-improvement/evals/evals.json\n"
    )
    (run_dir / "diff.patch").write_text(patch, encoding="utf-8")
    artifacts = {
        "prs": [{"url": "http://x/1"}, {"url": "http://x/2"}],
        "issues": [],
        "chosen_tool": "mutation",
        "questions_asked": 1,
        "final_message": "What should I improve first?",
        "evidence": {"complete": True, "checks": []},
        "tokens": 5000,
        "wall_s": 42.0,
        "package_named": None,
    }
    (run_dir / "artifacts.json").write_text(
        json.dumps(artifacts, indent=2), encoding="utf-8"
    )
    return run_dir


def compare_scores(base: dict[str, float], next_: dict[str, float]) -> dict[str, Any]:
    cases = sorted(set(base) | set(next_))
    deltas = {
        case: round(next_.get(case, 0.0) - base.get(case, 0.0), 2) for case in cases
    }
    mean_delta = round(sum(deltas.values()) / len(deltas), 4) if deltas else 0.0
    return {"cases": deltas, "mean_delta": mean_delta}


# --------------------------------------------------------------------------
# run-dir construction and the harness adapter
# --------------------------------------------------------------------------


def _case_for(fixture_id: str) -> dict[str, Any]:
    data = _read_json(EVALS_DIR / "evals.json")
    wanted = f"E8-{fixture_id}"
    for case in data["cases"]:
        if case["id"] == wanted:
            return case
    raise KeyError(f"no evals.json case named {wanted}")


def _skill_at_ref(dest: Path, skill_ref: str, workdir: Path) -> Path:
    """Materialize the skill at a git ref (or an existing dir) into dest."""
    skill_dir = dest / "autonomous-improvement"
    ref_path = Path(skill_ref)
    if ref_path.is_dir():
        shutil.copytree(
            ref_path,
            skill_dir,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"),
        )
        return skill_dir
    tarball = workdir / "skill.tar"
    subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "archive",
            "--format=tar",
            "-o",
            str(tarball),
            skill_ref,
            "skills/autonomous-improvement",
        ],
        check=True,
    )
    extract = workdir / "extract"
    extract.mkdir(parents=True, exist_ok=True)
    subprocess.run(["tar", "-xf", str(tarball), "-C", str(extract)], check=True)
    shutil.move(str(extract / "skills" / "autonomous-improvement"), str(skill_dir))
    return skill_dir


def _prepare_fixture_copy(fixture: dict[str, Any], work_root: Path, index: int) -> Path:
    repo = work_root / f"{fixture['id']}-run{index}"
    if repo.exists():
        shutil.rmtree(repo)
    source = Path(fixture["path"])
    if not source.is_absolute():
        source = EVALS_DIR / source
    shutil.copytree(source, repo)
    env = dict(os.environ)
    for var, default in (
        ("GIT_AUTHOR_NAME", "matrix-runner"),
        ("GIT_AUTHOR_EMAIL", "matrix-runner@example.invalid"),
        ("GIT_COMMITTER_NAME", "matrix-runner"),
        ("GIT_COMMITTER_EMAIL", "matrix-runner@example.invalid"),
    ):
        env.setdefault(var, default)
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "base"],
    ):
        subprocess.run(argv, cwd=str(repo), check=True, env=env)
    return repo


def run_split(args: argparse.Namespace) -> int:
    matrix = load_matrix()
    fixtures = split_fixtures(matrix, args.split)
    if not fixtures:
        print(f"no fixtures for split {args.split!r}")
        return 2
    skipped = []
    todo = []
    for fixture in fixtures:
        missing = toolcheck_missing(fixture)
        if missing:
            skipped.append((fixture["id"], missing))
        else:
            todo.append(fixture)
    for fixture_id, missing in skipped:
        print(f"[skip] {fixture_id}: toolchain missing: {', '.join(missing)}")
    if not todo:
        print("nothing to run (all fixtures skipped)")
        return 0

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    exit_code = 0
    for fixture in todo:
        case = _case_for(fixture["id"])
        prompt = case["prompt"]
        for index in range(1, args.runs + 1):
            record = _run_once(matrix, fixture, case, prompt, args, index)
            stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
            name = f"{record['case']}__{args.harness}__{args.model.replace('/', '_')}__{stamp}.json"
            out = Path(args.out) / name
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "w", encoding="utf-8") as handle:
                json.dump(record, handle, indent=2)
                handle.write("\n")
            print(
                f"[run] {record['case']} #{index}: score {record['score']}"
                f" (hard_fails: {record['hard_fails'] or 'none'}) -> {out}"
            )
    return exit_code


def _run_once(
    matrix: dict[str, Any],
    fixture: dict[str, Any],
    case: dict[str, Any],
    prompt: str,
    args: argparse.Namespace,
    index: int,
) -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix=f"matrix-{fixture['id']}-{index}-"))
    try:
        repo = _prepare_fixture_copy(fixture, workdir, index)
        skills_dir = repo / ".opencode" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        _skill_at_ref(skills_dir, args.skill_ref, workdir)
        (repo / "opencode.json").write_text(
            json.dumps(
                {"skills": [".opencode/skills/autonomous-improvement"]}, indent=2
            ),
            encoding="utf-8",
        )
        run_dir = workdir / "run-dir"
        run_dir.mkdir()
        env = dict(os.environ)
        for key, raw in (case.get("environment") or {}).items():
            if key == "PATH_PREPEND":
                env["PATH"] = f"{repo / 'bin'}{os.pathsep}{env.get('PATH', '')}"
            elif key.isupper():
                env[key] = str(raw)
        argv = ["opencode", "run", "--model", args.model, prompt]
        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                cwd=str(repo),
                capture_output=True,
                text=True,
                env=env,
                timeout=args.timeout,
                check=False,
            )
            transcript = (proc.stdout or "") + "\n-- stderr --\n" + (proc.stderr or "")
        except subprocess.TimeoutExpired as exc:
            transcript = (
                ((exc.stdout or "") + "\n[timeout]")
                if isinstance(exc.stdout, str)
                else "[timeout]"
            )
        wall_s = round(time.monotonic() - started, 1)
        (run_dir / "transcript.txt").write_text(transcript, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
        diff_proc = subprocess.run(
            ["git", "diff", "--cached", "HEAD"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        (run_dir / "diff.patch").write_text(diff_proc.stdout or "", encoding="utf-8")
        meta = {
            "schema": "matrix-run/1",
            "case": case["id"],
            "fixture": fixture["id"],
            "harness": args.harness,
            "model": args.model,
            "skill_ref": args.skill_ref,
            "prompt": prompt,
            "forbidden": [
                F_MULTIPLE_PRS,
                F_REFEREE_EDIT,
                F_WHOLE_SUITE_REPEAT,
                F_TIMERS,
            ]
            + ([F_BLOCKING_QUESTION] if case["id"] == "E8-1-bare-invocation" else []),
            "run_index": index,
        }
        (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        artifacts = {
            "prs": [],
            "issues": [],
            "chosen_tool": None,
            "questions_asked": 0,
            "final_message": "",
            "evidence": {"complete": False, "checks": []},
            "tokens": None,
            "wall_s": wall_s,
            "package_named": None,
        }
        (run_dir / "artifacts.json").write_text(
            json.dumps(artifacts, indent=2), encoding="utf-8"
        )
        print(f"    run-dir (fill artifacts.json by hand, then grade): {run_dir}")
        return grade_run_dir(run_dir, matrix)
    finally:
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def collect_records(results_dir: Path = RESULTS_DIR) -> list[dict[str, Any]]:
    records = []
    for path in sorted(results_dir.glob("*.json")):
        if path.name == "BASELINE.md":
            continue
        try:
            record = _read_json(path)
        except (json.JSONDecodeError, OSError):
            continue
        if record.get("schema") == "matrix-record/1":
            records.append(record)
    return records


def report(args: argparse.Namespace) -> int:
    records = collect_records(Path(args.results_dir))
    if not records:
        print(f"no matrix records under {args.results_dir}")
        return 2
    per_case: dict[str, list[float]] = {}
    for record in records:
        per_case.setdefault(record["case"], []).append(record["score"])
    means = {
        case: round(sum(scores) / len(scores), 3)
        for case, scores in sorted(per_case.items())
    }
    if args.json:
        print(json.dumps({"means": means, "n_records": len(records)}, indent=2))
        return 0
    print(f"{'case':34} {'runs':>4} {'mean':>6}")
    for case, scores in sorted(per_case.items()):
        print(f"{case:34} {len(scores):>4} {means[case]:>6}")
    all_scores = [r["score"] for r in records]
    print(
        f"overall mean over {len(all_scores)} runs: {round(sum(all_scores) / len(all_scores), 3)}"
    )
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def cmd_plan(args: argparse.Namespace) -> int:
    matrix = load_matrix()
    for fixture in split_fixtures(matrix, args.split):
        missing = toolcheck_missing(fixture)
        status = f"skip (missing: {', '.join(missing)})" if missing else "runnable"
        print(
            f"{fixture['id']:14} {fixture['split']:8} {status:40} expected_tools={fixture['expected_tools']}"
        )
    print(
        "\nrun: python3 evals/matrix.py run --split <split> --skill-ref <ref> "
        "--harness opencode --model <model> --runs 3"
    )
    return 0


def cmd_grade(args: argparse.Namespace) -> int:
    record = grade_run_dir(Path(args.run_dir))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
            handle.write("\n")
    print(json.dumps(record, indent=2))
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    base = _read_json(Path(args.base)).get("means")
    next_ = _read_json(Path(args.next)).get("means")
    if base is None or next_ is None:
        print('both files must be report --json output ({"means": {...}})')
        return 2
    print(json.dumps(compare_scores(base, next_), indent=2))
    return 0


def cmd_negative_control(_: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory(prefix="matrix-negctl-") as tmp:
        run_dir = build_negative_control(Path(tmp))
        record = grade_run_dir(run_dir)
    print(json.dumps(record, indent=2))
    ok = record["score"] == 0.0 and {
        F_BLOCKING_QUESTION,
        F_MULTIPLE_PRS,
        F_REFEREE_EDIT,
    }.issubset(set(record["hard_fails"]))
    print(
        "RED-PROOF: the grader correctly fails the bad run"
        if ok
        else "BROKEN: the grader accepted a bad run"
    )
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--negative-control",
        action="store_true",
        help="grade a canned bad run; expect score 0",
    )
    sub = parser.add_subparsers(dest="cmd")

    plan = sub.add_parser("plan", help="print the matrix plan; run nothing")
    plan.add_argument("--split", default="dev", choices=["dev", "heldout", "all"])

    run = sub.add_parser("run", help="execute agent runs over a split")
    run.add_argument("--split", default="dev", choices=["dev", "heldout", "all"])
    run.add_argument(
        "--skill-ref",
        required=True,
        help="git ref in the skills repo, or a skill directory",
    )
    run.add_argument("--harness", default="opencode", choices=["opencode"])
    run.add_argument("--model", required=True)
    run.add_argument("--runs", type=int, default=3)
    run.add_argument("--timeout", type=int, default=900, help="per-run seconds")
    run.add_argument("--out", default=str(RESULTS_DIR))
    run.add_argument("--keep", action="store_true", help="keep temp run dirs")

    grade = sub.add_parser("grade", help="score one run-dir offline")
    grade.add_argument("--run-dir", required=True)
    grade.add_argument("--out", default=None, help="also write the record JSON here")

    rep = sub.add_parser("report", help="aggregate records under a results dir")
    rep.add_argument("--results-dir", default=str(RESULTS_DIR))
    rep.add_argument("--json", action="store_true")

    comp = sub.add_parser("compare", help="delta between two report --json files")
    comp.add_argument("base")
    comp.add_argument("next")

    args = parser.parse_args(argv)
    if args.negative_control:
        return cmd_negative_control(args)
    if args.cmd == "plan":
        return cmd_plan(args)
    if args.cmd == "run":
        return run_split(args)
    if args.cmd == "grade":
        return cmd_grade(args)
    if args.cmd == "report":
        return report(args)
    if args.cmd == "compare":
        return cmd_compare(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
