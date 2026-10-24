---
name: autonomous-improvement
description: A toolbox of falsifiable improvement passes for a codebase — docs reconciliation, spec-vs-code audit, mutation testing, adversarial test audits, flake hunting, defect repair, fuzzing, measured performance, security scans, and oracle-guarded simplification. The first cycle adapts to the repo (cheap foundation passes, then a ranked menu the owner picks from) instead of applying a prebuilt formula; mutation testing is one tool among many. Use when asked to "improve the repo", "audit the tests", "find weak or flaky tests", "reconcile docs with the code", "remove dead code safely", or when "tests pass but I don't trust them". Keep-alive fleet machinery is elective (vm/ci environments or an explicit owner ask). Do not use for feature work, unguarded refactors, or one-off bugfixes.
---

# Autonomous Improvement

Spend tokens on work that can be **mechanically falsified**, not on renames,
prose, or checkbox tests. This skill is a **toolbox of passes**, not a
pipeline: read the repo's state first, adapt the plan to what it shows, and
never run a pass the repo does not need. Every pass produces at most one
small, evidence-backed PR; no gap is a valid result.

## The first cycle: foundations, then a menu

On a fresh invocation, before reaching for any heavy tool:

1. **Reconcile docs with reality** (`docs_truth.py check`, stream I): the
   README, specs, plans, and the code must agree, and every rule must name
   one authoritative place. Wrong docs cause wrong work in every later pass.
2. **Audit spec against code**: for every rule the repo claims, find the code
   that enforces it and the test that would fail if it broke. Rules with no
   code, no test, or a test that cannot fail are the first findings.
3. **Derive a ranked menu** — ledger `next --rank risk`, open issues, red CI —
   and **ask the owner what to improve**. The answer picks the tool; the menu
   comes from evidence, not from a fixed order.

## Modes: adapt, don't assume

- **Single session (default).** You run the per-task protocol (`04`) yourself.
  This is the mode for personal machines and ordinary engagements.
- **Fleet mode (opt-in).** Worker dispatch, beats, watchdogs, and unattended
  wake loops are for an explicit owner ask or a `vm`/`ci` environment
  (`session.environment` in config) — never a personal-laptop default. The
  orchestrator duties live in "Roles" in `04`; the unattended stack is in
  `05`, whose primary shape is stateless — `fleet.py wake` (snapshot →
  needs-brain → one fresh run) — with the session-id/wake-grid boot kept as
  the long-lived-session fallback.

Fleet rules that bind once fleet mode is on: on a quota error **rotate the
model**, never retry the same pool ("Model rotation", `05`); a PR is not done
when created — merge only on green **for the current head sha**, with the
authority set by config `fleet.merge_policy`; ledger rows follow config
`fleet.ledger_mode` (`batched`: one owner decision per wave, not per PR); a
dead pool never parks a task — the session's own model, dispatched unpinned,
is tried first, and exhausted tracked targets give way to files with no
ledger row. Tending the queue is work; generating into a full queue is the
new parking: `scripts/autonomous/fleet.py govern` checks open-PR caps,
kill-switches, and the `fleet.py doctor` preflight (report, don't dispatch).
Idle capacity turns the fleet's own history into skill proposals — never a
merge (`05`).

## The five laws

| Law | Rule |
|---|---|
| **1. Fail-to-pass** | No fix or test lands without proof that the new test **fails against the defect** and passes once the defect is addressed. For a bugfix the defect is the bug on `origin/main` (red on main, green after the fix). For a test-only PR closing a mutation gap the defect is the surviving mutant (red with the mutant applied, green on clean source). |
| **2. Content-hash dedup** | Deduplicate on the **content hash** of the target *and its companion test*, never on commit identity. The ledger stores `<source9>:<test9>`; a test change invalidates the audit just like a source change. |
| **3. Worktree isolation** | Every task runs in an isolated worktree branched from the configured base (`origin/main` by default). Stage by name; never run destructive git commands outside your own worktree. |
| **4. Machine budget** | One targeted test per mutant, one full-file run at the end of the task. Never run the whole suite locally — that is CI's job. One analyze/lint pass per task, on touched files, after the last edit. |
| **5. Pool rotation** | A quota error changes the model; it is never retried on the model that just failed. The dead model is recorded, the next live model takes the task, and the wake that revives a frozen session names a live model. A gateway reporting "$0 balance" is a paid pool with no credit, not a free fallback. |

## The toolbox

One pass at a time, picked by evidence. Per-tool contracts (trigger, finder,
oracle, evidence, output format): `references/10-toolbox.md`. Deep
checklists: `references/03-streams-and-triage.md`. Passes marked *audit* are
model reads with a fixed output format and a mandatory **refutation pass** —
a second call tries to disprove each finding; only survivors count.

| Tool (stream) | Reach for it when | Finder → oracle |
|---|---|---|
| docs-reconcile (I) | docs or plans may drift from code | `docs_truth.py check` → spec-trace diff |
| spec-audit (I) | rules may lack enforcement or tests | `docs_truth.py --spec` → rule/proof table |
| mutation testing (A) | the suite's kill rate is unknown | `mutation_engine.py` → red-with-mutant, green-clean |
| adversarial audit (B) | tests may be vacuous, mock-bound, sleepy | `stream_b_scan.py` shortlist → harness checklist (`03`) |
| defect repair (E) | an issue or red CI claims a bug | `repair.py intake / verify-red / verify-green` → repro red on main |
| flake & determinism (F) | tests flake or inherit the host | `flakes.py finder / oracle` → N-run contract |
| performance (G) | a hot path has numbers to beat | `perf_gate.py` → bench delta **and** output equivalence |
| security & deps (H+K) | secrets, advisories, risky bumps | `advisory.py secrets / deps / bump` → scan diff, oracle-first bump |
| simplification (J) | dead code is suspected | `simplify_gate.py finder / oracle` → green tests, non-falling mutation score |
| fuzz (C) | a parser or consumer needs crashing | `fuzz_shrink.py run / replay` → shrunk counterexample |
| lens pass (B-shaped) | one risk class needs one focused read | the lens menu (`10`) → refuted findings only |
| meta (L, gated) | the fleet reviews its own rules | `meta.py replay / lessons` → replay-gated, issue-only |

Two rules bind every tool: a finding counts only when it carries the **file,
line, a concrete trigger, and a failing test**; and one call carries **one
lens and one output format** — open-ended asks get generic advice.

## Install into a repo

```bash
cp -r <skill>/scripts/autonomous <repo>/scripts/autonomous
cp <repo>/scripts/autonomous/config.example.json <repo>/scripts/autonomous/config.json
cp <skill>/assets/AUTONOMOUS_WORK_LEDGER.template.md <repo>/docs/AUTONOMOUS_WORK_LEDGER.md
```

Edit the `config.json` copy to name the repo's real test, format, analyze, and
gate commands. Every command is an **argv array**; a missing key is
skipped with `[skip] <step> not configured`. Set also the candidate globs and
the source-to-test mapping — both repo-specific, and an empty candidate list
now means *not configured* rather than an empty queue. Verify the mapping once
with `ledger.py next --stream A --limit 3`. See `references/stack-adapters.md`
for Python, Dart/Flutter, TypeScript/JavaScript, and Go examples.

## Quick start (a round)

1. Pick the tool from the menu above (a first cycle runs the foundation
   passes before anything heavier).
2. `python3 scripts/autonomous/ledger.py next --stream <X>` selects the
   target (`--rank risk` for churn/issue-intake ordering — see
   `references/02-ledger-and-dedup.md`); `orchestrator.py create-worktree`
   isolates it.
3. Run the tool's finder, triage, prove red/green (Law 1), generate the
   evidence artifact (`evidence.py mutant|defect` — it runs red, green, and
   pass-to-pass itself), commit, then `orchestrator.py verify-gate --worktree .`
4. Push, open the PR, record the audit on that branch — or, if the ledger is
   a protected path, report the row text for the wave's batched ledger PR
   (see `04`).

Full commands, worktree cleanup, and the evidence contract:
`references/04-workflow-and-roles.md`. Stream A specifics (statuses, safety,
backends): `references/01-mutation-engine.md`.

## The ledger

The ledger is a git-tracked markdown table:
`docs/AUTONOMOUS_WORK_LEDGER.md` by default (`ledger_path` in config).

- One row per audit; new rows go directly below the header (newest first).
- The authoritative row for a target is the newest by date; on a tie, the
  topmost row wins.
- Statuses are audits (`COMPLETED`, `SOUND`) or hash-scoped refusals
  (`REJECTED`, `WONTFIX`, `SUPERSEDED`). No in-progress status: claim a target
  via `fleet.py claim` (draft PR at minute 1); `sync-rejections` records closed PRs.
- Cells are escaped (`\|`, `\\`); rows must stay append-only. Add the
  `merge=union` fragment from `assets/gitattributes.fragment.txt` so parallel
  appends merge without conflicts, and the Status cell must be the bare token.
- The hash is the blob at HEAD; `record` warns if the target is dirty.

## Planned and gated

**D** (migration safety) is still planned; **L** (the fleet improving itself)
is its own kill-switched, replay-gated stream with T2 caps. While a stream's
tooling is absent, `ledger.py` rejects its letter: offer an unlocked stream
(J is first-wave T1 — issue/proposal only), or file the finding as an
evidence-backed issue. The stream contract and catalog are in reference 08.

## Unattended operation (elective)

Nothing in this skill runs outside your session unless the owner asked for a
fleet or config names a `vm`/`ci` environment. When that is the case,
`references/05-unattended-operation.md` is the manual: heartbeats, the
watchdog's model ladder (`ORCH_MODEL_LADDER` — on a quota/balance error it
marks that model and retries the next in the same tick; every ladder entry
must carry quota independent of the others, and must actually be free), fleet
coordination, and the improvement cadence. Harness-specific commands live in
the adapter material there, not in this file.

## References

| File | Read when |
|---|---|
| `references/01-mutation-engine.md` | Rule packs, statuses, seeds/shards, diff scoping, triage |
| `references/02-ledger-and-dedup.md` | Schema, newest-wins, composite hash, escaping, merge driver, the validator, draft-PR claims |
| `references/03-streams-and-triage.md` | Deeper Stream A/B/J checklists and planned streams |
| `references/04-workflow-and-roles.md` | The one canonical per-task protocol and evidence contract |
| `references/05-unattended-operation.md` | Heartbeats, watchdog, quotas, fleet coordination, the improvement cadence (advanced) |
| `references/07-moving-main.md` | Working on a repo that keeps merging; stale-base traps |
| `references/08-recursive-self-improvement.md` | The stream contract, autonomy tiers, and the stream catalog |
| `references/09-session-history-review.md` | The method and evidence bar for skill-improvement proposals |
| `references/10-toolbox.md` | Per-tool contracts and the lens menu |
| `references/gates-and-vocabulary.md` | Gate ids, optional vocabulary, and the "no gates yet" fallback |
| `references/stack-adapters.md` | Concrete commands per stack |
| `references/examples.md` | One end-to-end Stream A example and a good/bad Stream B finding |

## Final rules

- Do not edit the referee (specs, gate configs, CI, `CODEOWNERS`, ratchets,
  linter config) to get green. That is a separate, human-approved PR.
- Do not weaken tests (`skip`, `xfail`, `.only`, `# noqa`, widened tolerance) to
  make a gate pass. `scripts/autonomous/test_weakening_check.py` is the local
  tripwire when the repo has no gate system.
- Delete a gate that stops paying; do not accumulate ceremony.
- If the repo has no gates, run the project's CI commands locally on touched files
  only and use the tripwire. Never block on a `gates.sh` that does not exist.

*Honest limit: gates verify conformance to the spec, not the truth of the spec.
Mutation here does not replace AST-based tools (Stryker, PIT, mutmut, cargo-mutants);
it is the zero-dependency fallback.*
