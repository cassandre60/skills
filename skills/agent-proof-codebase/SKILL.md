---
name: agent-proof-codebase
description: Build, audit, and work inside codebases whose deterministic gates (invariant specs, property/model/differential/crash-consistency tests, mutation testing, fail-to-pass bug-fix proof, ratchets, protected referee files, red-team corpus) make any accepted change high-signal even from weak or sloppy AI agents. Use whenever the user wants to harden a repo against low-quality or AI-generated code, design CI quality gates, make tests that can actually fail instead of checkbox tests, audit test-suite strength, set up mutation testing, write invariants or a SPEC, add performance budgets or ratchets, prepare a project for release with rigorous verification, stop agents from gaming tests, or asks for bulletproof, antifragile, or "premium quality" code. Also use when a repo already has SPEC.md, GATES.md, or .gates/ and you are asked to change it.
---

# Agent-proof codebase

Make acceptance mechanically expensive to fake, so whatever gets merged — from a
human, a weak agent, or a strong one — is high-signal: it fixes a real defect,
improves a measured property, or clears a proven edge case, with machine-checked
evidence attached.

This is not "write more tests". A million weak tests are worth less than a hundred
that fail when the code is wrong. For every gate ask: **what is the cheapest way to
pass without being correct, and have I closed it?**

## Choose your mode

| If the user wants... | Mode | Start with |
|---|---|---|
| a gate system built or retrofitted, "make this bulletproof/release-ready" | **A. Build** | Workflow A |
| to know how strong existing tests and CI really are | **B. Audit** | Workflow B |
| a feature or fix in a repo that already has SPEC.md / GATES.md / `.gates/` | **C. Contribute** | `references/12-contributor-protocol.md` |
| speed/memory/size improvements | **D. Optimize** | Workflow D |
| to test whether the gates can be beaten | **E. Red-team** | `references/11-red-team-corpus.md` |

If a repo is gated and you were asked to change code, you are in mode C even if the
request does not say so. If unsure, run `python3 scripts/assess_repo.py .` and
decide from what exists.

## Conduct rules (all modes)

These exist because agents optimise the reward they can see. Keep them when nobody
is watching.

1. **Evidence over assertion.** Never write "fixed", "verified", "safe" or "all
   tests pass" without having observed it this session. Quote the command and the
   relevant output. Say "unverified" plainly when you could not check.
2. **Never edit the referee to get a pass.** Specs, gate configs, CI workflows,
   CODEOWNERS, ratchet baselines, lint/type/coverage/mutation config, red-team
   corpus, frozen fixtures, agent instruction files. If you think a gate is wrong,
   stop and propose a separate gate-change with evidence.
3. **Never weaken a check.** No deleted or loosened assertions, skips, new
   suppressions, widened tolerances, mocking the subject, hardcoded expected
   values, catch-all error handling, sleeps or retries to hide flakiness, fixed
   seeds to hide failures, deleted features to remove failing behaviour.
4. **Fix the code, not the test** — unless the test is demonstrably wrong against
   the spec, in which case say so and get a human decision.
5. **Do not grade your own homework.** Use a separate context or subagent to review
   tests, and another to attack the result. Writing the gates and breaking the
   gates are different sessions.
6. **Ask only for decisions that are genuinely the user's:** product scope,
   spending money, removing user-facing features, changing persisted formats.
   Decide the rest and proceed.
7. **State what you chose not to do** and why. Silent omissions look like oversights.

## Workflow A: build or retrofit the gates

Read the referenced file when you reach each step; do not load them all up front.

1. **Orient and measure the true baseline.** Read docs, manifests, CI. Run
   `python3 scripts/assess_repo.py .` and the project's own quality commands.
   Record what passes, how long it takes, what flakes.
   See `references/02-assess-and-plan.md`.
2. **Subtract first.** Delete dead code, orphaned screens, unused dependencies,
   legacy features outside product scope. Fewer lines means fewer gates and fewer
   bugs. Record LOC and dependency count as the first ratchets.
3. **Tier the risk** (R0 loss/corruption/security, R1 core logic, R2 glue, R3
   generated). Spend rigor where errors are catastrophic. Identify irreversible
   decisions (schemas, identifiers, formats, public APIs) and freeze them early.
4. **Write the plan** (`GATES_PLAN.md`): maturity per tier, gap table with cost and
   leverage, phases, what you will not do. Get approval only for decisions that
   belong to the user.
5. **Write `SPEC.md` — the invariants, not the implementation.** If a property
   cannot be stated as an observable invariant, it is not a gate yet.
   See `references/03-spec-and-invariants.md`.
6. **Choose testable seams.** Architecture that cannot be tested is the real
   finding. See `references/04-testable-architecture.md`.
7. **Layer the strategies.** Property/model/differential/crash-consistency tests
   earn their keep against agents that pattern-match. See
   `references/05-test-strategies.md`.
8. **Prove the tests can fail.** Mutation testing is the only honest answer to "is
   this suite worth anything". See `references/06-mutation-testing.md`.
9. **Define the evidence contract** — what a passing PR must attach. See
   `references/07-evidence-contract.md`.
10. **Add ratchets and budgets** so regressions are blocked, not merely reported.
    See `references/08-ratchets-and-budgets.md`.
11. **Protect the referee** from the agent's own incentives: CODEOWNERS,
    protected paths, review requirements. See `references/09-protect-the-referee.md`.
12. **Wire CI for fast feedback**, with a local `gates.sh` mirroring it so the same
    commands work offline. See `references/10-ci-and-feedback.md`.
13. **Seed a red-team corpus.** See `references/11-red-team-corpus.md`.
14. **Write `CONTRIBUTING.md`** so humans know the bar. See
    `references/12-contributor-protocol.md`.

## Workflow B: audit

Run `python3 scripts/assess_repo.py .`, then answer honestly:

- Which assertions could be deleted with no test failure?
- Which tests assert only "did not throw"?
- Which gates have never failed, and therefore prove nothing?
- Which budgets are reported but not enforced?
- Where is the cheapest way to pass without being correct?

Then run the red-team corpus against the repo. A weakness you can only prove
theoretically is a weakness you have not yet found.

## Workflow D: optimize

Measure first, then bisect; change one variable at a time. Never guess at a
performance fix. See `references/08-ratchets-and-budgets.md`.

## Assets

Copy from `assets/` rather than writing from scratch: `SPEC.template.md`,
`GATES.template.md`, `gates.sh.example`, `github-actions.gates.example.yml`,
`protected-paths.example.txt`, `ratchet-baseline.example.json`,
`banned-apis.example.json`, `redteam-case.template.md`,
`PULL_REQUEST_TEMPLATE.md`, `CODEOWNERS.example`, `AGENTS.snippet.md`.

## Why these gates exist

The failure mode they target is specific: an agent that optimises the reward it can
see. It writes the test and the implementation, finds the test passes, and reports
success. Every gate here exists to make that path fail — see rule 5, do not grade
your own homework.
