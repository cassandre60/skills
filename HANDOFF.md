# Session Handoff: Codebase Review & Hardening

**Date**: 2026-11-18  
**Branch**: `main` (commit `635a077` synced with `origin/main`)  
**Status**: All background tasks and subagents stopped; working tree clean; ready for resumption.

---

## 1. What Was Completed and Verified

### A. Issue Triage & Cleanup
- Audited the full GitHub issues list:
  - Closed duplicate Issue #117 (already implemented by #178).
  - Closed 10 completed V2 tracking issues: #15, #16, #18, #19, #20, #21, #22, #23, #24, #26 (shipped in PR #61).
  - Addressed and closed issues #183, #184, #187, #189, #190 via PRs #191–#195.
- Logged Issue #196 for the remaining runner timing race in `MutationEngineTests.test_sigkill_recovery`.

### B. Shipped Pull Requests (All Audited by Adversarial Reviewer)
1. **PR #191 (Issue #183, commit `8060231`) — Retire Stale Root Runtime & Repo Hygiene**:
   - Removed obsolete 23-file root `scripts/autonomous/` copy, placeholder `docs/AUTONOMOUS_WORK_LEDGER.md`, and orphaned `.gitattributes`.
   - Relocated misplaced `Lessons Learned` entries in `AGENTS.md` out of the `# Global Agent Baseline` into the local section.
   - Updated `.gitignore` for `context-optimization` fixtures and `.ruff_cache/`.

2. **PR #192 (Issue #184, commit `1fda8eb`) — `fork-upstream-sync` Shallow & Subdir Fixes**:
   - Replaced flawed `git rev-parse --verify` with `git cat-file -e "<base_sha>^{commit}"` in `skills/fork-upstream-sync/scripts/tracker_template.py`.
   - Added `_is_ancestor` (`git merge-base --is-ancestor`) deepening loop so shallow clones deepen across shallow grafts even when `base_sha` exists locally.
   - Validated `base_sha` hex format, added `-c core.quotePath=false` and `--root` to `diff-tree`, and resolved `repo_root` via `git rev-parse --show-toplevel`.
   - Added 5 fail-to-pass unit tests in `skills/fork-upstream-sync/tests/test_tracker.py`.

3. **PR #193 (Issue #187, commit `8080ff1`) — `cliproxy-integration` Script Hardening**:
   - Scoped post-write verification to `--provider` and guarded boolean / un-named `model_id` in `skills/cliproxy-integration/scripts/label-models.py`.
   - Added `GATEWAY_API_KEY` (with `OMNIROUTE_API_KEY` fallback), closed `HTTPError` responses, and fixed `pyright` types in `skills/cliproxy-integration/scripts/sync-limits.py`.
   - Surfaced HTTP error bodies in `validate_key` and closed sockets in `skills/cliproxy-integration/scripts/onboard-keys.py`.
   - In `skills/cliproxy-integration/scripts/probe-models.sh`: fixed keyless `/v1/models` parsing, `jq` payload escaping, non-empty `choices` array validation, numeric `.error.code` string formatting, and positive integer `--max-tokens`.
   - Added 5 fail-to-pass unit tests in `skills/cliproxy-integration/tests/test_scripts.py`.

4. **PR #194 (Issue #189, commit `a95344d`) — `fail_to_pass_check.sh`, `survey.py`, & `__doc__` Guards**:
   - Isolated `HEAD_REF` execution in `skills/agent-proof-codebase/scripts/fail_to_pass_check.sh` into an isolated `mktemp -d` worktree with a unique log file (never detaching caller's branch or colliding `/tmp/f2p_head.log`), anchored to repo root, and passed `-c core.quotePath=false`. Added 4 unit tests in `skills/agent-proof-codebase/tests/test_scripts.py`.
   - Handled JSON `list` output and exact-target + newest-row + live git blob SHA-1 ledger filtering in `skills/autonomous-improvement/scripts/autonomous/survey.py`.
   - Guarded `__doc__` against `None` (`python -OO` safe) in `skills/context-optimization/scripts/analyze_context_budget.py` and `skills/autonomous-improvement/evals/run.py`.

5. **PR #195 (Issue #190, commit `635a077`) — `SPEC.md` `R-31`, Root Linters, & CI Enforcement**:
   - Renumbered duplicate rule `R-10` to `R-31` in `skills/autonomous-improvement/SPEC.md` and enforced unique rule IDs in `skills/autonomous-improvement/tests/test_spec_standalone.py`.
   - Switched `scripts/generate_readme.py` to repo-relative `git ls-files -z skills` across `render_skills_table()` and `render_layout()` to ignore untracked files.
   - Hardened `scripts/check_skills.py` with secret detection regexes (Gemini, AWS, GitLab), root `docs/` and `scripts/` scanning, `fleet-ops/` and `evals/` reference checks, and skill-root containment. Added 2 unit tests in `scripts/tests/test_root_scripts.py`.
   - Wired `ruff check --isolated --select E9,F`, `pyright`, and all unit test suites into `.github/workflows/check.yml`.

---

## 2. Quick Verification Commands

> [!IMPORTANT]
> **HARD RULE: Never Melt The User's Machine**: Never run full/wide test suites locally (e.g. running the entire 470+ test `autonomous-improvement` regression suite locally is forbidden). Run only fast, lightweight, scoped test suites locally.

### Fast Local Gates (<5 seconds total)
```bash
python3 scripts/check_skills.py
bash scripts/check_discovery.sh
python3 scripts/generate_readme.py --check
```

### Static Linters & Type Checks (<10 seconds)
```bash
ruff check --isolated --select E9,F --exclude skills/autonomous-improvement/evals/fixtures scripts/ skills/
pyright scripts/ skills/agent-proof-codebase/ skills/autonomous-improvement/scripts/autonomous/ skills/autonomous-improvement/evals/run.py skills/cliproxy-integration/ skills/context-optimization/ skills/fork-upstream-sync/
```

### Scoped Unit Test Suites (<10 seconds total)
```bash
python3 -m unittest discover -s scripts/tests -v
python3 -m unittest discover -s skills/agent-proof-codebase/tests -v
python3 -m unittest discover -s skills/cliproxy-integration/tests -v
python3 -m unittest discover -s skills/context-optimization/tests -v
python3 -m unittest discover -s skills/fork-upstream-sync/tests -v
```

---

## 3. Immediate Next Steps & File Targets

### Target 1: Resolve Issue #196 (`MutationEngineTests.test_sigkill_recovery` Timing Race)
- **Target File**: `skills/autonomous-improvement/tests/test_v2.py` (lines 1644–1706).
- **Background**: In CI run `44161300083` on `main`, `test_sigkill_recovery` failed with `AssertionError: b'def f(a, b):\n return a == b\n' == b'def f(a, b):\n return a == b\n'`.
- **Root Cause**: `SourceGuard.__enter__` creates the backup file `.mutbak` *before* the baseline test run finishes and *before* writing the mutant into `src/app.py`. On slower CI runners, the test's `while not backup.exists(): time.sleep(...)` loop wakes up and sends `SIGKILL` before the mutation engine actually modifies `src/app.py`.
- **Fix**:
  1. In both `test_sigterm_restores_source` and `test_sigkill_recovery`, update `slow.py` to touch a readiness marker file (e.g. `mutated_ready = Path('mutated_ready'); mutated_ready.touch()`) only when `(repo / "src" / "app.py").read_bytes() != original`.
  2. In the test methods, poll until both `backup.exists()` AND `(repo / "mutated_ready").exists()` before sending signals (`SIGTERM` or `process.kill()`).
  3. Clean up the marker in `finally:` or test teardown.
  4. Prune the resolved `2026-10-21` flake lesson from `AGENTS.md`.
- **Scoped Local Test Command**:
  ```bash
  cd skills/autonomous-improvement
  PYTHONPATH=tests python3 -m unittest test_v2.MutationEngineTests.test_sigkill_recovery test_v2.MutationEngineTests.test_sigterm_restores_source -v
  ```
- **PR & Merge**: Create branch `fix/sigkill-sigterm-test-race`, push, open PR for #196, verify GitHub Actions CI is green, and merge.

### Target 2: Stale Worktree Cleanup
- Check `git worktree list` and prune or remove completed worktrees:
  - `/home/mohamed/Desktop/Github/skills-a-common`
  - `/home/mohamed/Desktop/Github/skills-a-docs-truth`
  - `/home/mohamed/Desktop/Github/skills-advisory-gap`
  - `/home/mohamed/Desktop/Github/skills-survey-adversarial-smell`
  - `/home/mohamed/Desktop/Github/skills-worktrees/phase5`
  - `/home/mohamed/Desktop/Github/skills-worktrees/remeasure`
