---
name: autonomous-improvement
description: Run falsifiable, high-signal improvement cycles on a codebase instead of cosmetic churn: mutation testing to find tests that don't actually protect behavior, and adversarial audits of existing tests (vacuous assertions, flaky sleeps, over-mocking). Use when asked to "improve the repo", "audit the tests", "find weak or flaky tests", "improve test quality, not just coverage", "run mutation testing", or when "tests pass but I don't trust them" — or when an agent has spare budget and needs work that can be proven real (fails without the fix, not already done at this file's content, cheap enough to run locally). Given as a session's only prompt, it turns that
session into an orchestrator that dispatches worker subagents instead of
building itself (references/04-workflow-and-roles.md). Do not use for feature work, refactors, or one-off bugfixes.
---

# Autonomous Improvement

Spend tokens on work that can be **mechanically falsified**, not on renames,
prose, or checkbox tests. Two streams are active:

- **A — mutation testing:** find behavior the suite does not protect.
- **B — adversarial test audit:** find tests that cannot fail, assert mocks, or
  sleep instead of synchronizing.

Each round produces at most one small, evidence-backed PR. If there is no
falsifiable gap, stop; that is a valid result.

## Who runs the round

An explicit task ask runs the per-task protocol directly (`04`). When this
skill is a session's **only** instruction, that session is the Orchestrator
instead: it dispatches and supervises worker subagents — at most 3 at a
time — and merges their PRs on green CI, building nothing in its own turns.
See "Roles" in `04`; the unattended stack that keeps it alive is in `05`,
whose cold start finds the state dir, writes its own session id, and arms
the wake grid **before** the watchdog — boot order matters.

## The four laws

| Law | Rule |
|---|---|
| **1. Fail-to-pass** | No fix or test lands without proof that the new test **fails against the defect** and passes once the defect is addressed. For a bugfix the defect is the bug on `origin/main` (red on main, green after the fix). For a test-only PR closing a mutation gap the defect is the surviving mutant (red with the mutant applied, green on clean source). |
| **2. Content-hash dedup** | Deduplicate on the **content hash** of the target *and its companion test*, never on commit identity. The ledger stores `<source9>:<test9>`; a test change invalidates the audit just like a source change. |
| **3. Worktree isolation** | Every task runs in an isolated worktree branched from the configured base (`origin/main` by default). Stage by name; never run destructive git commands outside your own worktree. |
| **4. Machine budget** | One targeted test per mutant, one full-file run at the end of the task. Never run the whole suite locally — that is CI's job. One analyze/lint pass per task, on touched files, after the last edit. |

The engine runs tests serially. `machine_budget.max_concurrent_tests` in config
records the owner's cap for parallel shards/agents; it is not a per-run fan-out
knob.

## Install into a repo

```bash
cp -r <skill>/scripts/autonomous <repo>/scripts/autonomous
cp <repo>/scripts/autonomous/config.example.json <repo>/scripts/autonomous/config.json
cp <skill>/assets/AUTONOMOUS_WORK_LEDGER.template.md <repo>/docs/AUTONOMOUS_WORK_LEDGER.md
```

Edit the `config.json` copy to name the repo's real test, format, analyze, and
gate commands. Every command is an **argv array**; a missing key is
skipped with `[skip] <step> not configured`. See `references/stack-adapters.md`
for Python, Dart/Flutter, TypeScript/JavaScript, and Go examples.

## Quick start (one round)

1. `python3 scripts/autonomous/ledger.py next --stream A` selects the next unaudited target.
2. `python3 scripts/autonomous/orchestrator.py create-worktree --slug <slug> --type test` isolates it.
3. `python3 scripts/autonomous/mutation_engine.py --source <target> --test-cmd "<targeted cmd>"` finds survivors.
4. For one triaged survivor, prove the new test is red with the mutant and green on clean source.
5. Commit, then run `python3 scripts/autonomous/orchestrator.py verify-gate --worktree .`.
6. Push the branch and open the PR, then record the audit on that branch and push the ledger commit.

Full commands, worktree cleanup, and the evidence contract: `references/04-workflow-and-roles.md`.

## Stream A — mutation testing

The engine is regex-based, with per-language rule packs under
`scripts/autonomous/mutation_rules/`. It mutates one line at a time and runs
your targeted test command once per mutant.

- `KILLED`: the suite noticed. No work.
- `SURVIVED`: a candidate gap. **Triage first** — equivalent mutants, string or
  comment noise, and regex false positives are real.
- `UNCOMPILABLE`: an anchored compiler-error pattern matched; excluded from the
  kill rate. Never "fix" these with tests.
- `TIMEOUT`: reported separately; counted as killed by default. Change with
  `--timeout-counts-as`.

A survivor becomes work only when you can state the behavior it breaks and write
a test that is red with the mutant and green without it. The test passes on
`origin/main` by construction; that is expected (Law 1).

Safety: the engine refuses a dirty source unless `--allow-dirty`, writes
`<file>.mutbak` + `<file>.mutlock`, restores on SIGTERM/SIGINT/SIGHUP, and
recovers a stale backup after SIGKILL. Add these patterns to `.gitignore`; see
`assets/gitignore.fragment.txt`. Shards need separate worktrees.

## Stream B — adversarial test audit

Read a test file and ask:

1. **Can it actually fail?** Does it assert real state, or only that a call did
   not throw? Would it pass if the implementation did the opposite?
2. **Does it assert behavior or the mock?** If the mock returns X and the test
   asserts X, no domain logic ran.
3. **Is it a timing bomb?** Fixed sleeps are flakes under load. Poll a condition
   or use a virtual clock; never add a sleep to "stabilize" anything.

Every finding needs evidence (a line, a counterexample, or a proposed
assertion). Cosmetic edits are not Stream B work.

## The ledger

The ledger is a git-tracked markdown table:
`docs/AUTONOMOUS_WORK_LEDGER.md` by default (`ledger_path` in config).

- One row per audit; new rows go directly below the header (newest first).
- The authoritative row for a target is the newest by date; on a tie, the
  topmost row wins.
- Statuses are exactly `COMPLETED` and `SOUND`. There is no in-progress
  status: claim a target by opening a draft PR early, because a claim written
  in one worktree is invisible to other agents until merged.
- Cells are escaped (`\|`, `\\`); rows must stay append-only. Add the
  `merge=union` fragment from `assets/gitattributes.fragment.txt` so parallel
  appends merge without conflicts.
- The hash is the blob at HEAD. `record` warns if the target is dirty;
  `--strict` makes that fatal.

## Planned (not available)

- **Stream C** — property fuzzing / model checking.
- **Stream D** — migration and data-preservation archeology.

Do not start work under these names or promise them in a PR; offer Streams A/B.

## Unattended operation (advanced)

Multi-day, owner-away fleets are a separate concern from the core loop. If you
need them, read `references/05-unattended-operation.md` and treat the
opencode/systemd material there as **harness-specific**: it does not transfer
to Claude Code or another agent without adapting the resume command.

## References

| File | Read when |
|---|---|
| `references/01-mutation-engine.md` | Rule packs, statuses, seeds/shards, diff scoping, triage |
| `references/02-ledger-and-dedup.md` | Schema, newest-wins, composite hash, escaping, merge driver, draft-PR claims |
| `references/03-streams-and-triage.md` | Deeper Stream A/B checklists and planned streams |
| `references/04-workflow-and-roles.md` | The one canonical per-task protocol and evidence contract |
| `references/05-unattended-operation.md` | Heartbeats, watchdog, quotas, fleet coordination (advanced) |
| `references/07-moving-main.md` | Working on a repo that keeps merging; stale-base traps |
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
- If the repo has no gates, run the project's own CI commands locally on touched
  files only and use the tripwire. Never block on a `gates.sh` that does not
  exist.

*Honest limit: gates verify conformance to the spec, not the truth of the spec.
Mutation here does not replace AST-based tools (Stryker, PIT, mutmut,
cargo-mutants); it is the zero-dependency fallback.*
