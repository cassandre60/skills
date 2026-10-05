# Protect the referee

Contents: principle, what to protect, mechanisms, hermetic CI, the agent boundary, gate-change process,
tamper detection, supply chain.

If the author can edit the gates, nothing else in this skill matters. The most important property of a gate
system is that a normal PR cannot change what "passing" means.

## What counts as the referee
SPEC.md, GATES.md, GATES_PLAN.md decisions, `.gates/` (protected paths, banned APIs, mutation config and
suppressions, ratchet baselines), `scripts/` used by gates, CI workflow files, CODEOWNERS, lint/type/analyzer
config, coverage and mutation tool config, test runner config, red-team corpus, frozen fixtures and golden files,
agent instruction files (AGENTS.md, CLAUDE.md), and the PR template.

Existing tests are special: contributors must be able to add tests, but modifying or deleting existing
assertions is flagged by `test_weakening_check.py` and, for refactors, forbidden.

## Mechanisms (layer them)
1. **CODEOWNERS** (`assets/CODEOWNERS.example`) with required review from a human owner on every referee path.
2. **protected_paths_check.py** with `.gates/protected-paths.txt`: fails the PR when referee files change unless
   a human has approved (CI passes `--approved` only from a trusted signal such as an owner's approving review).
3. **Branch protection / rulesets:** required status checks, required reviews, no direct pushes to the main
   branch, no force pushes, no bypass for the agent's identity, linear history optional. Apply the same to
   release branches.
4. **Trusted checkout:** CI runs gate scripts and reads gate configuration from the base branch checkout, and
   runs them against the PR's code in a separate directory (see `assets/github-actions.gates.example.yml`).
   For platforms where a PR can modify the workflow that runs on it, protect the workflow directory with
   CODEOWNERS and use organization-level required workflows or reusable workflows pinned to a protected ref
   where available.
5. **Do not run untrusted PR code with privileged credentials.** Avoid event types that give write tokens or
   secrets to workflows executing PR code (for example `pull_request_target` combined with checking out the PR head).
6. **Tamper evidence (optional):** a `gates.lock` file listing hashes of gate configs and scripts, verified by
   CI from the trusted checkout.

## Hermetic gates
Authoritative runs happen on the server, from a clean environment, with pinned toolchains and lockfiles,
no network except to approved registries, and no state carried over from the author's machine. Local runs
are for fast feedback only. Client-side hooks (pre-commit, husky) are conveniences that can be bypassed; they are
never the gate.

## The agent boundary
- The agent's credentials can open PRs and push to branches, nothing more. It cannot merge, edit branch
  protection, change CI secrets, or approve its own PRs.
- The sandbox where the agent works is untrusted: it may edit anything locally, which is exactly why results
  from that sandbox carry no authority.
- Give the agent the local gate command so it can iterate cheaply, and tell it plainly (AGENTS.md snippet) that
  editing the referee is out of bounds.
- Use separate sessions or models for writing code, writing gates, and reviewing; do not let one context grade
  its own homework.

## Gate-change process
Gate changes are their own PR class. Requirements: states exactly what it will stop or start catching; shows
red-team recall did not drop (and ideally rose); includes the tests/cases proving the new behavior; gets human
approval; contains no product-code changes. Loosening a threshold needs a written reason a human accepts.

## Suppression hygiene
Lint/type/analyzer suppressions and mutation exclusions are allowed only with a reason comment and are counted by a
ratchet. New ones are surfaced by the tripwire for explicit human acknowledgement.

## Supply chain basics
Lockfiles committed; dependency additions justified and scanned; third-party CI actions pinned by commit hash;
secrets scanning in CI; least-privilege tokens (`permissions: contents: read` by default); signed releases where
the platform supports it.
