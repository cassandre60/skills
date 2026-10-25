# Design history: the v2 four-slice rework (toolbox router, elective machinery, harness adapters, SPEC)

Moved on 2026-10-24 from `.zcode/plans/plan-session_66aba8ca8f1eaa7d1f4c9acad35ed3d1.md`
(untracked harness session state — `.zcode/` is never committed) into
`docs/history/`: this is the plan behind PRs #79–#82, kept as design history.
Content below is the plan as written, unchanged.

---

## Goal

Turn the skill from "mutation testing is the first cycle" into a **toolbox with a router**, make all keep-alive machinery **elective and machine-profile-aware**, and make harness adaptation **modular** — incorporating the chess-app lessons in abstract, reusable form. The key discovery driving the design: the toolbox already exists in code (docs_truth.py, advisory.py, flakes.py, repair.py, perf_gate.py, simplify_gate.py, fuzz_shrink.py, stream_b_scan.py, meta.py — and streams C–K are already `available` in common.py's registry), but SKILL.md's router never mentions it. The code is ahead of the docs; the fix is mostly the router + contracts, not new machinery.

## Constraints discovered (must respect)

- SKILL.md ≤ 200 lines (test at exactly the cap today); ~100 required-phrase contracts pinned across references/03, 04, 05, 07, 08, 09 by tests/test_v2.py; `check_skills.py` link-checks every backtick path in markdown; StreamRegistryTests require every stream label's first distinctive word in reference 08; forbidden-string tests (`opencode-watchdog`, `shell=True`, …). My changes add invariants rather than fight existing ones.
- watchdog_rotation_test.sh pins watchdog.sh internals (current_session_model, switch_session_model, ORCH_* vars) — the adapter work must keep those names working with today's opencode defaults.

## Four slices, each its own PR (landed in order; green+merged between slices)

### Slice 1 — Fix the router bug: SKILL.md as toolbox router + `references/10-toolbox.md`
- SKILL.md rewrite (stays ≤200 lines):
  - New engagement protocol on invocation: (1) cheap foundation passes first — **docs-reconcile** (docs_truth.py exists) and **spec-audit** (rules → enforcement → failing test), (2) derive a **ranked menu** from ledger/issues/CI, (3) **ask the owner what to improve**. Mutation testing and adversarial audit become two tools among ~12, used when the owner picks them or when the queue justifies it.
  - Modes section: **single interactive session is the default**; fleet mode (worker dispatch, beats, watchdog) is opt-in (owner asks, or environment is vm/ci) — replacing today's "session's only instruction ⇒ Orchestrator" unconditional jump.
  - Toolbox index table: tool | when to reach for it | finder | oracle | evidence — one row per implemented module + the new **lens** audit passes (time/dates, data loss, double-events, odd inputs, failing surroundings, failure-path walkthrough, security/privacy) — each lens = one pass, one output format, mandatory **refutation pass** (second call tries to disprove findings; only survivors count).
  - The two universal rules become stated laws: every finding carries file+line + concrete trigger + failing test; one lens + one output format per call.
- `references/10-toolbox.md`: per-tool contract entries (trigger, finder, oracle, evidence contract, output format) mapping each chess-app lesson to its existing module or lens; deep-dive checklists stay in 03.
- Tests: extend `test_referenced_files_exist`; new invariant tests — "Two streams are active" must NOT appear in SKILL.md; "ask the owner" and the mode-default wording must appear; line-limit still holds.

### Slice 2 — Elective machinery: machine profile
- `config.example.json` + this repo's `config.json`: new `session.environment` key (`laptop` default, `vm`, `ci`).
- Wording in SKILL.md/04/05: beats/watchdog/timers are installed only on vm/ci or on explicit owner ask; laptop default = nothing ever runs outside the session.
- `fleet.py doctor`: new check — warn when `session.environment: laptop` but the watchdog/wake timers are active.
- Red-first doctor test.

### Slice 3 — Harness adapter layer (modularity)
- `assets/watchdog.sh`: parameterize the remaining opencode hardcodes without changing defaults — `ORCH_SESSION_ID_FILE` (default `opencode_session_id`), `ORCH_SESSION_GET_CMD`/`ORCH_SESSION_SET_CMD` (defaults = today's `opencode api get/post`), so a different harness overrides everything through env, not edits.
- New `references/harness-adapters.md`: one table per harness (resume cmd, wake cmd, session-model get/set, model enumeration, worker dispatch, unattended-support status), rows marked verified/unverified — opencode verified; Claude Code/Codex already-unverified rows consolidated there; ZCode row added (interactive-only until a headless resume is verified). references/05's "Harness-specific notes (opencode)" collapses into a pointer.
- New rotation-test case: a non-opencode fake harness overriding the session get/set cmds drives a full rotation — proves the seam. Existing opencode-pinned tests keep passing (defaults unchanged).

### Slice 4 — `SPEC.md`: the authoritative rules table
- `skills/autonomous-improvement/SPEC.md`: every rule the skill enforces → where it's enforced (code/doc/test) → the test that fails if broken; seeded from the five laws and the structure-test inventory. `docs_truth.py` already auto-detects SPEC files, so the skill can run its own spec-audit on itself (dogfood) — documented in the SPEC header.
- Test: every SPEC row's enforcement target must exist (file + test name).

## Verification per slice

Scoped unittest runs (one `-k` per pattern), one full-file `test_v2.py` run at the end of each slice, verify-gate, PR per repo convention with attribution footer, merge only on green for the current head sha. The turn may end mid-sequence; whatever slice is in flight lands as its own green PR and the journal records where to resume.
