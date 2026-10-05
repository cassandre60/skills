# Contributor protocol: how an agent works inside a gated repo

Contents: before starting, while working, when a gate fails, forbidden moves, honest reporting, pre-push checklist,
roles and fresh-context review.

Use this when the repository already has SPEC.md/GATES.md/.gates or an AGENTS.md carrying the quality-gates
rules (`assets/AGENTS.snippet.md`). Your goal is to get a change accepted honestly, not to make gates go green.

## Before starting
1. Read AGENTS.md/CLAUDE.md, SPEC.md, GATES.md. Note protected paths and the PR classes.
2. Run the local gate command on the untouched tree to learn the baseline and runtime. If it is red before you
   start, say so; do not build on it silently.
3. Classify the task: bugfix, perf, refactor, feature, test-only, dependency. If it spans classes, split it.
4. Write a short plan naming the invariant(s) involved and the evidence you will attach.

## While working
- Bugfix: write the failing test first; confirm it fails on the current code for the right reason; then fix.
- Perf: record the baseline measurement first; keep the old implementation as an oracle until equivalence is shown.
- Refactor: pin behavior with characterization or differential tests before touching code; do not edit existing tests.
- Feature: confirm it is in scope; update the spec via the proper PR; write properties first.
- Keep the diff minimal and single-purpose. No drive-by cleanups, formatting waves, or unrelated dependency changes.
- If you find another bug, record it (issue or note) and do not bundle the fix.

## When a gate fails
1. Read the feedback: gate, invariant, counterexample, reproduce command.
2. Reproduce locally with the printed seed/command.
3. Form a hypothesis about the root cause in the code; fix the code.
4. Re-run the specific gate, then the full local suite.
5. After 3 failed attempts on the same gate, stop and report what you learned, what you tried, and what you
   believe is wrong (code, test, or gate). Ask for help instead of thrashing.
If you believe the gate or spec is wrong, say so with evidence and propose a gate-change/spec-change. Do not
bypass it.

## Forbidden moves (these are failures even if CI turns green)
Editing protected paths; deleting, skipping, or loosening tests/assertions; adding suppressions; lowering
thresholds or ratchet baselines; hardcoding expected values or special-casing test inputs; mocking the subject;
swallowing errors; adding sleeps/retries to mask flakiness; fixing seeds to hide failures; deleting features or
code paths to remove failing behavior; widening types; adding dependencies to shortcut work; skipping hooks
or CI with flags; editing the PR template or labels to dodge a check; claiming a result you did not observe.

## Honest reporting (end every task with this)
```
Class: <bugfix|perf|...>
Change: <one or two sentences>
Evidence: <commands run and their results: fail-to-pass output, benchmark numbers, mutation delta>
Gates run locally: <list> -> <pass/fail>
Not run / unverified: <specific items and why>
Protected paths touched: <none | list + justification>
Follow-ups / risks: <anything a human should check>
```
Never write "fixed", "verified" or "all tests pass" without having observed it in this session. Quote the command
and the relevant output lines. Say "unverified" plainly when you could not check something.

## Pre-push checklist
- Local gate command is green on the final tree.
- New tests fail when the fix is reverted (spot check by reverting or running fail-to-pass).
- No new skips, suppressions, loosened asserts, or baseline edits (`test_weakening_check.py`, `protected_paths_check.py`).
- Diff is minimal and matches the declared class.
- PR description complete with evidence and honest status.

## Roles and fresh-context review
Do not grade your own homework. When you write both code and tests, have a different session (or model) review
the tests against the spec without seeing the implementation, and have a separate red-team pass try to break the
change. When building the gate system itself, keep the gate-writing session separate from the gate-breaking one.
If subagents are available, use one as an independent verifier whose only job is to find reasons the change is wrong.
