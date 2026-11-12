# Autonomous Improvement V3 — Post-Implementation Audit & Remediation Plan

**Date:** 2026-11-11 (implemented 2026-11-11 via PRs #169–#175)  
**Baseline Audited:** `cassandre60/skills@4b4ba1e` (PRs #145–#168 + direct commits `8ad5990`, `9c52e0b`)  
**Companion to:** `docs/autonomous-improvement-v3-universal-plan.md`  
**Status:** Implemented and merged into `cassandre60/skills@42b708f` (PRs #169–#175)

---

## 0. Executive Summary

Milestones 1–5 of `docs/autonomous-improvement-v3-universal-plan.md` and four follow-up infrastructure PRs (#161, #163, #166, #168) were merged into `main`. While the module split (`fleet_doctor.py`, `fleet_pool.py`, `fleet_cockpit.py`, `mutation_stryker.py`), per-model quota rotation, transient timeout cooldowns, and multi-language generated-file exclusions are solid, **live testing against throwaway repositories and inspection of the production VM logs (`~/.local/share/orchestrator/wake.log`) exposed four P0 defects** and five P1/P2 gaps.

Two of the P0 defects recreate the exact failure modes V3 was designed to eliminate:
1. **Unconditional 25-minute wake spinning** when `ORCH_WAKE_ON_BACKLOG=1` is enabled, because `fleet.py work-gate` is a level trigger (checks whether work *exists*) rather than an edge trigger (checks whether state *changed*).
2. **False-positive secret hits on ordinary code** (`final token = authResp.accessToken;`), which cause `survey.py` to score `security` at `0.9` on every run and wake the fleet every 25 minutes on a non-existent secret leak.
3. **Broken event wakeups during Deep Standby (Saturation)**, where `needs-brain` returns early before comparing the state digest—ignoring new commits on `main` and new owner directives.
4. **Destructive self-updater defaults**, where `update-skills.sh` operates directly inside the developer checkout (`~/github-projects/skills`) and runs `git checkout -B main origin/main` / `git reset --hard`.

Each item below is scoped as a single atomic PR with exact file/line references, root-cause analysis, concrete implementation steps, and a mandatory fail-to-pass (RED → GREEN) test specification.

---

## 1. P0 Defects (Must Fix First)

### P0-1: `fleet.py work-gate` bypasses `needs-brain` digest and spins every tick on static conditions

- **Files & Lines:**
  - `skills/autonomous-improvement/fleet-ops/assets/stateless-wake.sh:220-238`
  - `skills/autonomous-improvement/scripts/autonomous/fleet_cockpit.py:904-1025`
- **Symptom / Reproduction:**
  When `needs-brain` returns `false` ("state unchanged since the last wake"), `stateless-wake.sh:231` runs:
  ```bash
  elif [ "${ORCH_WAKE_ON_BACKLOG:-0}" = "1" ] && (cd "$REPO" && python3 "$PREDICATE" work-gate >>"$LOG" 2>&1); then
    echo "$(date -Is) fleet work-gate: multi-track backlog work available; waking despite unchanged PR state" >>"$LOG"
  ```
  Inside `evaluate_work_gate()` (`fleet_cockpit.py:904-998`), the function checks:
  1. Active directives (`lines 912-919`)
  2. Open PRs with `checks == "red"` or `owner_ask_pending` (`lines 926-937`)
  3. `survey.py` choosing any tool (`lines 946-959`)
  4. Any `ready`/`lead` item in `backlog.py` (`lines 963-979`)
  5. Any unaudited target in `ledger.py` (`lines 982-991`)

  **Why this is broken:**
  - Checks (1) and (2) duplicate `needs-brain`, which *already* woke once for that directive or red PR and recorded its digest. If a PR stays red (e.g., waiting on owner approval for a protected path like G07, or an external check), `needs-brain` rightly stays quiet on tick 2, 3, 4...—but `work-gate` sees `checks == "red"` and forces a wake on **every single tick forever**.
  - Checks (3), (4), and (5) are **stateless level checks**. If a candidate exists that an agent cannot resolve or that is blocked by governor/track caps or a false positive, `work-gate` returns exit `0` every 25 minutes indefinitely.
  - Worse, `work-gate` does **not** check `fleet.py govern` (WIP PR cap, 24h settled PR cap, circuit breaker, acceptance floor) or Deep Standby (`saturation_info(root)`). Even if `max_open_prs` is reached or the circuit breaker is tripped, `work-gate` still launches a model turn!
- **How to Fix:**
  1. **Remove `directive` and `pr-tend` from `evaluate_work_gate()`** (`fleet_cockpit.py:912-937`): `needs-brain` already includes directives (`fleet.py:996`) and red/owner-ask PRs (`fleet.py:916-925`) in `_wake_digest`. They belong in edge-triggered `needs-brain`, never in a level-triggered override.
  2. **Gate `evaluate_work_gate()` on Governor & Saturation first**:
     - If `saturation_info(root).get("saturated")` is `True`, return `{"work_available": False, "source": "saturated", "reason": "repo is in deep standby"}`.
     - If governor blocks dispatch (kill switch, circuit breaker, `len(open_states) >= max_open_prs`, daily cap reached, worktree cap reached), return `{"work_available": False, "source": "governor", "reason": ...}`.
  3. **Deduplicate `work-gate` wakes by digest (`work_gate.json`)**:
     - Compute a `work_digest` over `(main_sha, open_pr_numbers_and_head_shas, chosen_source, chosen_track, chosen_target_or_item_id)`.
     - Persist the last attempted `work_digest` and timestamp in `state_dir / "work_gate.json"`.
     - If the current `work_digest` equals the previous `work_digest` (meaning the previous wake already woke for this exact backlog item and neither `main`, open PRs, nor the top backlog pick changed), return `work_available: False` (`"backlog state unchanged since last work-gate wake"`).
  4. **Catch `UsageError` in `_next_candidates_preview()`** (`fleet_cockpit.py:501`):
     - Add `UsageError` (imported from `common`) to the `except` tuple in `_next_candidates_preview()` so repos without `candidates.A.include` configured do not crash `work-gate --json` with exit code 2.
- **Fail-to-Pass Test (`tests/test_cockpit.py`):**
  - *Test 1 (`test_work_gate_does_not_retrigger_on_unchanged_red_pr_or_same_backlog_digest`):* Stage a repo with a red PR and an unclaimed candidate; run `work-gate` twice with no change to `main` or PRs. Assert the second call returns `work_available: False` (exit `1`).
  - *Test 2 (`test_work_gate_works_on_unconfigured_repo_without_crashing`):* Run `fleet.py work-gate --json` on a git repo with no `config.json`. Assert exit code is `0` or `1` and stdout is valid JSON (not exit `2` `UsageError`).

---

### P0-2: `advisory.py secrets` false-positives on normal property/method assignments (`key-assignment` regex)

- **File & Lines:** `skills/autonomous-improvement/scripts/autonomous/advisory.py:61-68, 195-215`
- **Symptom / Reproduction:**
  In `advisory.py:62-67`:
  ```python
  (
      "key-assignment",
      re.compile(
          r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|pwd)\b[\"\']?\s*[:=]\s*[\"\']?"
          r"([A-Za-z0-9_\-/+=.]{16,})[\"\']?"
      ),
  ),
  ```
  Because the surrounding quotes `[\"\']?` are optional and `.` is inside the character class `[A-Za-z0-9_\-/+=.]`, any code that assigns a property access or method call ≥ 16 chars to a variable named `token`, `secret`, or `password` is flagged as a hardcoded secret!
  Live hits in `chesssrs` that triggered `survey.py` to pick `security` (score `0.9`):
  - `final token = userToCheck.token;` (`userToCheck.token` = 17 chars)
  - `final token = authResp.accessToken;` (`authResp.accessToken` = 20 chars)
  - `final token = response.body.trim();` (`response.body.trim` = 18 chars)
  - `final token = Platform.environment['LICHESS_TOKEN'];` (`Platform.environment` = 20 chars)
  - `final token = request.body.split(',')[0];` (`request.body.split` = 18 chars)
- **How to Fix:**
  1. **Require literal quotes (or `.env`/INI line structure) for `key-assignment`**:
     In source code files (`.py`, `.dart`, `.ts`, `.js`, `.go`, `.rs`, `.java`, `.kt`, `.rb`), a hardcoded secret literal is inside quotes (`"..."` or `'...'`). An unquoted right-hand side is an identifier or expression (`userToCheck.token`, `os.environ.get(...)`, `process.env.SECRET_KEY`).
     Update `key-assignment` into two explicit rules or post-match filters:
     - **Quoted string literal assignment**:
       ```python
       r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|pwd)\b[\"\']?\s*[:=]\s*([\"\'])([A-Za-z0-9_\-/+=.]{16,})\1"
       ```
     - **Unquoted `.env` / INI / properties assignment** (anchored to start of line `^[ \t]*(?:export[ \t]+)?[A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD|PASSWD|PWD)[A-Z0-9_]*[ \t]*=[ \t]*([^\s#\"\'();]+)`), excluding values that contain `.` identifier chains (`foo.bar`), `$` variable interpolation (`$VAR`, `${VAR}`), or placeholders (`changeme`, `your_token_here`, `example`, `xxxx`, `TODO`, `<...>`).
  2. **Add an expression/placeholder filter in `_scan_text()`**:
     Reject any captured candidate value that:
     - Looks like a dotted identifier chain (`re.match(r"^[A-Za-z_$][A-Za-z0-9_$]*(\.[A-Za-z_$][A-Za-z0-9_$]*)+$", val)`), OR
     - Is followed immediately by `(` or `[` on the line (method call or indexing like `response.body.trim()` or `Platform.environment[...]`).
- **Fail-to-Pass Test (`tests/test_cockpit.py` or `tests/test_v2.py::SecurityStreamTests`):**
  - *Test (`test_secret_scan_ignores_unquoted_code_expressions_and_env_lookups`):* Write a fixture file containing:
    - `final token = authResp.accessToken;`
    - `final token = response.body.trim();`
    - `final token = Platform.environment['LICHESS_TOKEN'];`
    - `api_key = os.environ.get("API_KEY")`
    - plus one real hardcoded quoted secret `API_KEY = "sk_test_9876543210abcdef123456"`.
  - Assert `advisory.py secrets --json` reports **only** the 1 real literal secret and **0** false positives on the code expressions.

---

### P0-3: Deep Standby (`saturation.json`) suppresses event wakes when `main` moves or an owner directive is added

- **Files & Lines:**
  - `skills/autonomous-improvement/scripts/autonomous/fleet.py:1005-1045`
  - `skills/autonomous-improvement/SPEC.md:53` (`R-28`)
- **Symptom / Reproduction:**
  In `fleet.py:1017-1040`:
  ```python
  # Deep Standby check (V3 Milestone 5 Honest Halt)
  sat_info = saturation_info(root)
  if sat_info.get("saturated") and not open_states and not issues_data:
      ts = sat_info.get("timestamp")
      now = datetime.datetime.now(datetime.timezone.utc)
      standby_hours = float((fleet_cfg.get("saturation_standby_hours") or 24))
      if ts and (now - datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() < standby_hours * 3600:
          payload = {
              "tool": "fleet.py needs-brain",
              "needs_brain": False,
              ...
  ```
  **Why this is broken:**
  1. The check only tests `and not open_states and not issues_data`. It does **not** check whether:
     - `active_directive_reasons(root)` is non-empty (an owner added a directive via `fleet.py directive add`), OR
     - `main_sha` changed since `previous.get("main_sha")` or since `sat_info.get("main_sha")` (a human pushed a new commit to `main`).
  2. Furthermore, `get_main_sha(root)` (`fleet.py:227-235`) only runs `git rev-parse HEAD` locally without checking `origin/main` or comparing against the SHA at which saturation was recorded.
  3. Consequently, in a saturated repo with 0 open PRs and 0 open issues, pushing a new commit to `main` or adding an owner directive returns `needs_brain: False` ("deep standby: repo is saturated")—violating Plan §3.3 and `SPEC.md` `R-28`.
- **How to Fix:**
  1. In `survey.py:752-760`, when writing `.autonomous/saturation.json`, also record `"main_sha"` (`git rev-parse HEAD`).
  2. In `fleet.py:cmd_needs_brain`, record `"main_sha": main_sha` inside `wake.json` alongside `"digest"`.
  3. In `fleet.py:1018-1040`, before entering the Deep Standby early return, check:
     - `directives_present = bool(active_directive_reasons(root))`
     - `prev_main = previous.get("main_sha") or sat_info.get("main_sha") or ""`
     - `main_moved = bool(main_sha and prev_main and main_sha != prev_main)`
     - If `directives_present` or `main_moved`, **clear `.autonomous/saturation.json`** (or bypass standby) and allow `needs-brain` to return `True`!
     - If standby * has * expired (`elapsed >= standby_hours * 3600`), unlink `.autonomous/saturation.json` or force a wake so `survey.py` can re-verify saturation and start a fresh 24h window.
- **Fail-to-Pass Test (`tests/test_v3_saturation.py`):**
  - Extend `test_deep_standby_wakes_on_events` (or add `test_deep_standby_wakes_on_moved_main_and_directive`):
    1. Put repo in deep standby (`saturation.json` with current timestamp, 0 PRs, 0 issues). Verify `needs-brain` is `False`.
    2. Make a new commit on `main`. Run `needs-brain --json`. Assert `needs_brain` is `True`.
    3. Re-arm `saturation.json`, add a directive via `fleet.py directive add "audit parser"`. Run `needs-brain --json`. Assert `needs_brain` is `True`.

---

### P0-4: `update-skills.sh` defaults `SKILLS_CLONE` to the developer working tree and runs `git checkout -B` / `git reset --hard`

- **File & Lines:** `skills/autonomous-improvement/fleet-ops/assets/update-skills.sh:25-39, 100-107`
- **Symptom / Reproduction:**
  In `update-skills.sh:25-37`:
  ```bash
  detect_clone() {
    if [ -n "${ORCH_SKILLS_CLONE:-}" ]; then
      printf '%s\n' "$ORCH_SKILLS_CLONE"
      return 0
    fi
    ...
    printf '%s\n' "$HOME/github-projects/skills"
  }
  ```
  And at lines 100–106:
  ```bash
  git checkout -q -B main origin/main
  rollback_clone() {
    if [ -n "$OLD" ]; then
      git reset -q --hard "$OLD"
    fi
  }
  ```
  **Why this is dangerous:**
  `~/github-projects/skills` is the human/agent development checkout on the host. When `update-skills.sh` runs via `skills-updater.timer` or `ORCH_AUTO_UPDATE=1` at the start of every wake:
  - If an agent or human is mid-edit on a branch or has uncommitted changes to tracked files in `~/github-projects/skills`, `git checkout -q -B main origin/main` and `git reset -q --hard "$OLD"` will overwrite or destroy their work.
- **How to Fix:**
  1. **Change the default clone path** in `detect_clone()` (`update-skills.sh:25-38`) to a dedicated, isolated cache directory outside `~/github-projects`:
     ```bash
     DEFAULT_CLONE="${XDG_CACHE_HOME:-$HOME/.cache}/orchestrator/skills-mirror"
     ```
     Never auto-detect `$SELF_DIR/../../../..` or `$HOME/github-projects/skills` unless the operator explicitly sets `ORCH_SKILLS_CLONE`.
  2. **Add a dirty-tree safety guard** before any `git checkout` or `git reset` in `update-skills.sh`:
     ```bash
     if [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
       log "ABORT: skills clone $SKILLS_CLONE has uncommitted tracked changes; refusing to overwrite"
       exit 1
     fi
     ```
- **Fail-to-Pass Test (`fleet-ops/tests/stateless_wake_test.sh`):**
  - Add a test case verifying:
    1. When `ORCH_SKILLS_CLONE` is unset, `update-skills.sh` uses `$HOME/.cache/orchestrator/skills-mirror` (not `~/github-projects/skills`).
    2. When `ORCH_SKILLS_CLONE` points to a dirty repo (modified tracked file), `update-skills.sh` aborts with a clear log message and leaves the modified file intact.

---

## 2. P1 Gaps (Plan/Spec Mismatches & Missing Guards)

### P1-1: Pyright type error on `origin/main` in `fleet.py` / `fleet_cockpit.py`

- **Files & Lines:**
  - `skills/autonomous-improvement/scripts/autonomous/fleet.py:260, 1718`
  - `skills/autonomous-improvement/scripts/autonomous/fleet_cockpit.py:546, 865`
- **Symptom:**
  Running `pyright skills/autonomous-improvement/scripts/autonomous/` fails with:
  ```text
  fleet.py:1718:25 - error: Argument of type "(state_dir: Path, cooldown_sec: int = ORCH_MODEL_COOLDOWN_DEFAULT) -> set[str]" cannot be assigned to parameter "load_cooling_fn" of type "(...) -> dict[str, int]" in function "run_dashboard_command"
  ```
  `_load_cooling_models` in `fleet.py:260` returns `set[str]`, whereas `build_dashboard_payload` (`fleet_cockpit.py:546`) and `run_dashboard_command` (`fleet_cockpit.py:865`) annotate `load_cooling_fn: Callable[..., dict[str, int]]` (and `fleet_cockpit.py:678` immediately wraps the result in `set(...)`).
- **How to Fix:**
  - Broaden the type annotation in `fleet_cockpit.py:546` and `fleet_cockpit.py:865` to:
    ```python
    load_cooling_fn: Callable[..., Iterable[str]],
    ```
    (importing `Iterable` from `collections.abc` or `typing`).
  - Also add a `pyright` step (or a unit test in `tests/test_structure.py` that runs `pyright` when installed) so type regressions cannot merge unnoticed.
- **Verification:** `pyright skills/autonomous-improvement/scripts/autonomous/` exits `0` with `0 errors`.

---

### P1-2: `orchestrator.py verify-gate` is missing the `refactor` track gate and has an unguarded `import yaml`

- **File & Lines:** `skills/autonomous-improvement/scripts/autonomous/orchestrator.py:319-356`
- **Symptom:**
  1. `orchestrator.py:319-355` handles `track == "research"`, `track == "ci"`, `track == "test"`, and `track == "bug"`, but has **no `elif track == "refactor":` branch**—even though Milestone 4 (`docs/autonomous-improvement-v3-universal-plan.md:654-656`) ticked off *"Wire the track gates: ... the J/M oracle for `refactor`"*. Passing `--track refactor` silently skips all track-specific verification!
  2. At line 329, `import yaml` is placed **outside** the `try:` block (`lines 330-333`). On a minimal Python environment without `PyYAML` installed (the skill's scripts are required to be zero-dependency Python stdlib), `verify-gate --track ci` crashes with an unhandled `ModuleNotFoundError: No module named 'yaml'`.
- **How to Fix:**
  1. Move `import yaml` inside a `try ... except ImportError:` block in `orchestrator.py:328-334`. When `PyYAML` is absent, fall back to `actionlint` (if on `PATH`) or a basic stdlib structural check (non-empty, no tab indentation, valid `on:` and `jobs:` top-level keys).
  2. Add `elif track == "refactor":` in `orchestrator.py:355`:
     - Require either a valid Stream J simplification oracle artifact or a strictly negative net line delta (`added < deleted` across non-test production files) plus zero deleted test files.
- **Fail-to-Pass Test (`tests/test_v3_gates.py`):**
  - Add a sub-assertion in `test_track_gate_enforcement_in_verify_gate`: running `verify-gate --track refactor` on a commit that *adds* net lines to `src/app.py` (without a simplification/ratchet artifact) fails with `"refactor track gate"`.

---

### P1-3: CI Track Tier Mismatch (`T2` in code vs `T1` in Plan §3.2 / §5.0 / §7.1)

- **Files & Lines:**
  - `skills/autonomous-improvement/scripts/autonomous/survey.py:137-143`
  - `skills/autonomous-improvement/scripts/autonomous/backlog.py:143`
  - `skills/autonomous-improvement/references/04-workflow-and-roles.md:415-421`
- **Symptom:**
  The V3 Plan (`§3.2`, `§5.0`, `§7.1`) explicitly states that `.github/workflows/` is **referee surface** under SKILL.md "Final rules" ("Do not edit the referee... That is a separate, human-approved PR") and therefore sets the `ci` track to **`T1` (issue/proposal only)** pending Owner Decision 1 (`§11`).
  However:
  - `survey.py:141` sets `"ci-audit": {..., "tier": "T2"}`
  - `backlog.py:143` sets `"tier": "T2"` on `CI-` items
  - `references/04-workflow-and-roles.md:419` instructs the agent: *"Output: One issue or draft PR proposing the workflow"*.
- **How to Fix:**
  - Change `"tier": "T2"` to `"tier": "T1"` in `survey.py:141` and `backlog.py:143`, and update `references/04-workflow-and-roles.md:419` to specify `T1 (issue/proposal only — CI config is referee surface; open a PR only when an owner directive explicitly authorizes it)`.
- **Fail-to-Pass Test (`tests/test_v3_signals.py`):**
  - Assert that `survey.py --json` reports `"tier": "T1"` for `"ci-audit"`, and `backlog.py harvest` sets `"tier": "T1"` on `ci` track items.

---

### P1-4: Circuit breaker and wake success detection ignore "garbage/empty model turns" that exit 0

- **Files & Lines:**
  - `skills/autonomous-improvement/scripts/autonomous/fleet.py:298-341, 1460-1505`
  - `skills/autonomous-improvement/fleet-ops/assets/stateless-wake.sh:340-415`
- **Symptom / Evidence from VM Log:**
  In `~/.local/share/orchestrator/wake.log` (2026-11-11T20:55:33Z), `openrouter/openrouter/free` was launched, emitted garbled multi-script tokens (`<tool_call> نهာ ظاهرة명alikASC...`), made zero tool calls, wrote no run-record outcome, and exited `0`.
  - `stateless-wake.sh` logged: `wake on 'openrouter/openrouter/free' finished; done` and wrote a fallback run-record with `outcome="wake-complete"`.
  - In `fleet.py:323`, `_check_consecutive_failed_wakes` only trips when 3 consecutive turns have `r.get("action") == "launch-failed"`. A broken model that exits `0` without recording a real run outcome (`pr-opened`, `issue-filed`, `no-gap`, `saturated`, `blocked`) is treated as a success (`launched`) and never cools down the model or trips the circuit breaker.
- **How to Fix:**
  1. In `stateless-wake.sh`, detect malformed/empty model output (e.g., raw `<tool_call>` tags printed to stdout with no tool execution, or runtime < 15s with no new run-record written by the agent itself) and treat it as a model failure (`mark_failed "$model" "$TIMEOUT_COOLDOWN"` and rotate to the next model in the ladder).
  2. In `fleet.py:_check_consecutive_failed_wakes`, also count `"traceless"` wakes (where the harness exited 0 in live mode but neither wrote an agent run-record (`pr-opened`, `issue-filed`, `no-gap`, `saturated`) nor updated any PR/issue).

---

### P1-5: `Retry-After` parsing and Ban-List filter were marked `[x]` in Milestone 1 & §3.1 but not implemented

- **Files & Lines:**
  - `skills/autonomous-improvement/scripts/autonomous/fleet.py:239, 280-296, 1470-1500`
  - `skills/autonomous-improvement/scripts/autonomous/backlog.py:57-120`
- **Symptom:**
  1. `fleet.py:239` defines `ORCH_RETRY_AFTER_EXHAUSTION_DEFAULT = 1800`, which is unused anywhere in `fleet.py`. Neither `_record_model_failure()` nor `_load_cooling_models()` parses `Retry-After: <seconds>` from harness output or supports the 3-column `<model> <ts> [cooldown_sec]` format that `stateless-wake.sh` (PR #166) and `fleet_cockpit.py:_load_wake_failures_cooling` now use!
  2. Plan §3.1 ("Enforcement: each harvester rule carries a ban-list check") has no corresponding filter in `backlog.py` or `orchestrator.py verify-gate`.
- **How to Fix:**
  1. **Unify 3-column cooldown support & `Retry-After` extraction in `fleet.py`**:
     - Add `RETRY_AFTER_PATTERN = re.compile(r"retry[-_ ]after[:\s]+(\d+)", re.IGNORECASE)` in `fleet.py`.
     - Update `_record_model_failure(state_dir, model, cooldown_sec=None)` to write `<model> <now> <cooldown_sec>` when a custom cooldown (from `Retry-After` or timeout) is present.
     - Update `_load_cooling_models(state_dir, cooldown_sec=...)` to honor the optional 3rd column per line (matching `fleet_cockpit.py:424-447`).
  2. **Add a deterministic Ban-List check in `orchestrator.py verify-gate`**:
     - Reject diffs that consist purely of comment/docstring changes on non-doc streams (`stream not in ("I",)`), or diffs on `test` track that add test files containing zero assertions (`stream_b_scan.py` `vacuous-test` hit on newly added tests).

---

## 3. P2 Hygiene & Cleanup

1. **Unused imports / variables (`ruff --select F`):**
   - Clean up the 26 unused-import / f-string warnings across `scripts/autonomous/` and the missing `from typing import Any, Dict` in `skills/autonomous-improvement/tests/test_v2.py:2667` (currently masked only by `from __future__ import annotations`).
2. **Immediate VM Mitigation (`~/.local/share/orchestrator/watchdog.env`):**
   - Until **P0-1** and **P0-2** are merged and synced via `update-skills.sh`, set `ORCH_WAKE_ON_BACKLOG=0` in `~/.local/share/orchestrator/watchdog.env` (or deploy P0-1 and P0-2 first) so the VM stops waking every 25 minutes on the 6 false-positive `advisory.py` secret hits in `chesssrs`.
   - Also remove `openrouter/openrouter/free` (the unpinned meta-router that routed to a broken model at `20:55Z`) from the curated pool in `chesssrs`'s config, keeping the explicit model IDs that follow it.

---

## 4. Recommended Implementation Order for Agents

Each step below should be executed in an isolated worktree branched from `origin/main` in `cassandre60/skills`, verified locally with scoped tests + `pyright` + `scripts/check_skills.py`, and opened as a small, single-purpose PR:

| Order | PR Slug | Scope | Target Files | Gate / Test File | Merged PR |
|---|---|---|---|---|---|
| **1** | `fix/work-gate-digest-and-governor` | **P0-1** (edge-triggered `work-gate`, governor/saturation check, `UsageError` guard) + **P1-1** (`pyright` `load_cooling_fn` fix) | `fleet_cockpit.py`, `fleet.py` | `tests/test_cockpit.py` + `pyright` | [#169](https://github.com/cassandre60/skills/pull/169) |
| **2** | `fix/advisory-secrets-code-expressions` | **P0-2** (require quotes or `.env` line format for `key-assignment`; ignore dotted expressions) | `advisory.py` | `tests/test_cockpit.py` / `tests/test_v2.py` | [#170](https://github.com/cassandre60/skills/pull/170) |
| **3** | `fix/deep-standby-event-wake` | **P0-3** (wake from Deep Standby when `main` SHA moves or owner directive is added; clear stale `saturation.json`) | `fleet.py`, `survey.py` | `tests/test_v3_saturation.py` | [#171](https://github.com/cassandre60/skills/pull/171) |
| **4** | `fix/updater-isolated-mirror-and-dirty-guard` | **P0-4** (default `update-skills.sh` clone to `~/.cache/orchestrator/skills-mirror`; abort on dirty tracked tree) | `fleet-ops/assets/update-skills.sh` | `fleet-ops/tests/stateless_wake_test.sh` | [#172](https://github.com/cassandre60/skills/pull/172) |
| **5** | `fix/verify-gate-refactor-yaml-and-ci-tier` | **P1-2** (`refactor` track gate + guarded `import yaml`), **P1-3** (`ci` track tier `T1`), **P1-5** (`Retry-After` + 3-col cooldown in `fleet.py`) | `orchestrator.py`, `survey.py`, `backlog.py`, `fleet.py`, `references/04-workflow-and-roles.md` | `tests/test_v3_gates.py`, `tests/test_v3_signals.py`, `tests/test_wake_circuit.py` | [#173](https://github.com/cassandre60/skills/pull/173) |
| **6** | `fix/wake-garbage-and-traceless-turns` | **P1-4** (rotate on leaked `<tool_call>` XML exit-0 turns; `ORCH_REQUIRE_AGENT_TRACE=1` `"traceless"` tracking; circuit-breaker helpers moved to `fleet_doctor.py`) | `stateless-wake.sh`, `fleet.py`, `fleet_doctor.py` | `tests/test_fleet_rollup.py`, `fleet-ops/tests/stateless_wake_test.sh` | [#174](https://github.com/cassandre60/skills/pull/174) |
| **7** | `chore/pyflakes-and-type-imports-hygiene` | **P2-1** (remove unused imports/vars flagged by `ruff --select E9,F` and add missing typing imports in `tests/test_v2.py`) | `scripts/autonomous/*.py`, `tests/*.py` | `ruff`, `pyright`, scoped `unittest` | [#175](https://github.com/cassandre60/skills/pull/175) |
