> **OUTCOME (2026-10-25):** the owner chose **Option B** and it was executed —
> the keep-alive set now lives under `fleet-ops/` in this skill. The option
> text below is historical; its paths describe pre-move locations.

# Proposal: separating the portable engine from the keep-alive machinery (Phase 6)

Status: PROPOSAL ONLY — no file moves. Decision: the owner's (plan Phase 6.3).
Written 2026-10-25 from a full dependency map (below), which anyone can
reproduce with the greps listed at the end.

## The dependency map (facts)

Keep-alive set: references/05-unattended-operation.md,
references/harness-adapters.md, assets/watchdog.sh,
assets/stateless-wake.sh, assets/wake.sh, assets/snapshot.sh,
assets/install-watchdog.sh, the six `assets/orchestrator-*.service/.timer`
units, tests/watchdog_rotation_test.sh, tests/stateless_wake_test.sh,
and the unattended subcommands of `fleet.py` (`wake`, `govern`, `doctor`,
`needs-brain`, `snapshot` — `pr-state`, `stats`, `claim`, `publish` are core).

Who references it:

| Engine file | Coupling | Nature |
|---|---|---|
| `tests/test_v2.py` | **153 lines** | test pins: elective-machinery warnings, doctor, rotation, E7 wake cases, adapter tables |
| `SKILL.md` | 13 lines | prose: Modes, the fleet-rules paragraph, Unattended section, References table |
| `SPEC.md` | 4 rows | R-05/R-07/R-08 name watchdog.sh / harness-adapters / shell tests as enforcement+proof |
| `evals/run.py`, `evals/matrix.py` | ~6 lines | E7-6 runs the rotation shell test; TIMER_MARKERS referee detectors |
| `scripts/autonomous/common.py` | 2 lines | the `session.environment` laptop/vm/ci gate — shared plumbing, not keep-alive-only |
| every other engine script (ledger, mutation, survey, onboard, conventions, repair, advisory, docs_truth, pins, profile, runrecord, meta) | **0** | cleanly separable already |

## Option A — a sibling skill (`autonomous-fleet-ops`)

Move the keep-alive set into `skills/autonomous-fleet-ops/` with its own
SKILL.md; this skill keeps a one-line pointer.

- File moves: the set above plus its SPEC rows (R-05, R-07, R-08) and the
  two shell tests.
- Test/path changes: ~153 lines of test_v2.py pins move or rewrite; the
  eval matrix's E7-6 case and matrix.py TIMER_MARKERS re-point at the new
  location; the wake-launcher run-record hook moves with the launcher.
- Install story: two skills to install where one sufficed for fleet users;
  single-session users install one less (a real win).
- What breaks: measurement comparability — the matrix measures this skill
  as a unit, and a mid-stream split forks the baseline; and any external
  consumer of the asset paths (systemd unit installers) needs the new path.

## Option B — one skill, keep-alive under `fleet-ops/` behind one pointer

Move the set into `fleet-ops/` inside this skill (assets and reference
included); SKILL.md's Unattended section becomes the single pointer.

- File moves: the set above, one directory deeper.
- Test/path changes: the two shell tests keep their names (they pin
  internals); path fixes are mechanical (`assets/` → `fleet-ops/assets/`),
  and SPEC rows keep their proofs.
- Install story: unchanged (one skill).
- What breaks: nothing structural; the boundary becomes visible and
  Option A stays cheap later — the moves are the same moves.

## Recommendation

**Option B now; revisit Option A after the measurement loop has run.**
The engine is already decoupled in code (0 imports), so the real cost of a
full split is concentrated in 153 test-pin lines and measurement
comparability — both of which argue for not forking the skill while the
eval baseline is fresh. Option B buys the visible boundary and the smaller
install surface for the common case, at near-zero risk, and leaves every
option open. Phase 7 (the optional ratchet stream) is unaffected by either
choice.

Reproduce the map:

    rg -c "watchdog|wake|snapshot|heartbeat|ORCH_|unattended|orchestrator-|session.environment" \
        SKILL.md SPEC.md tests/test_v2.py
    rg -n "wake|watchdog" evals/run.py evals/matrix.py
    rg -n "def cmd_" scripts/autonomous/fleet.py
