# autonomous-improvement — deep audit (headless PR/issue-machine edition)

**Audited at:** `872697b` (`main`, PR #9) on 2026-10-21 · **Skill version:** V2 + PRs #1–#9
**Deployment under audit:** a headless VM where the skill is the session's only instruction —
an opencode Orchestrator session kept alive by sleep "beats" + a systemd watchdog, dispatching
worker subagents on (mostly free) model pools to produce a continuous stream of small,
evidence-backed PRs/issues against a target repo, owner away for days.

**Method:** full read of `SKILL.md`, all 9 references, all scripts (~2,400 LOC Python + shell),
tests, evals, assets, the V2 spec/changelog, and the #1–#9 PR history; ran the suite
(50 tests pass in ~8.5 s); probed for dead config, contradictions, and prose-vs-code coverage;
benchmarked against external systems (§5, sources linked inline).

> **Verdict in one paragraph.** The *quality core* of this skill is genuinely strong and rarer
> than most "autonomous loop" material: falsifiable work only, a correct fail-to-pass
> distinction for test-only work, content-hash dedup, worktree isolation, crash-safe mutation,
> and a CI-to-merge ownership loop — all encoded as tested, stdlib-only code, not vibes.
> But judged as a **PR/issue-generating machine**, only the first half of the machine exists
> as code. Everything after "worker opens a PR" — claiming, publishing, issue filing,
> CI-state tracking, merge policy, rejection memory, rate governing, metrics — is prose the
> Orchestrator must re-improvise every wake, burning the tokens the machine exists to spend
> on findings. The machine also has no governor and no memory of being told "no": it is
> optimized to never idle, while its scarce resource is owner review minutes. The single
> highest-leverage improvement is not a smarter prompt; it is finishing the deterministic
> spine so the LLM only does the two things only it can do: triage survivors and write tests.

---

## 1. What you are actually running (goals, reconstructed)

From the skill body, the dated field lessons, and PRs #1–#9, the deployment's real objective is:

> **Convert spare, unreliable (free-pool) agent tokens into merged, mechanically-verified
> test-hardening work, at the highest rate per owner-review-minute, without ever shipping
> cosmetic churn, weakening a test, or touching the referee — and without dying quietly
> when a model pool, the harness, or `main` moves underneath it.**

The machine you have built to do that:

```
systemd timer (20 min)
  └─ watchdog.sh ── heartbeat stale? ── nudge (walk ORCH_MODEL_LADDER on quota signatures)
        │
Orchestrator session (skill = only prompt)          ← long-lived, contextful
  ├─ sleep-beat grid (10/30/60 min) re-armed every turn — its only self-wake
  ├─ journal + (described, unshipped) hourly snapshot accumulator, outside the repo
  ├─ derives queue: ledger.py next  ×  gh open PRs, re-derived every dispatch
  ├─ dispatches ≤3 workers, unpinned by default; rotates model on quota death
  └─ owns every PR to merge: per-wake check reads, ≤2 fix iterations, head-sha green, Sentinel
        │
Workers (per-task protocol, references/04)
  ├─ isolated worktree from origin/main (orchestrator.py, registered)
  ├─ Stream A: regex mutation_engine → triage 1 survivor → red-with-mutant / green-clean test
  ├─ Stream B: read a test file adversarially (vacuous / mock-asserting / sleep)
  ├─ verify-gate (format + analyze + repo gate_cmd on touched files)
  └─ push, gh pr create, record composite-hash ledger row (or report row text if protected)
```

Things the design deliberately is **not**: a feature builder, a refactorer, a bugfixer-of-record
(the description forbids all three), or a merge bot with taste. Its outputs are intentionally
boring: one small test-hardening diff + evidence, or a documented "SOUND / no real gap".

Two structural facts follow from your usage, and the audit judges everything against them:

1. **The bottleneck is not tokens, it is merged-PR throughput per owner minute.** Free pools are
   flaky but replenish; owner attention does not. Anything that produces unreviewable,
   duplicate, previously-rejected, or red-and-forgotten PRs is a machine failure even when the
   diff is locally "correct". (Industry data agrees: agentic PRs merge at markedly lower rates
   than human PRs and most rejections are project-fit/size, not code flaws — §5.6.)
2. **Everything that happens every wake should be code; the LLM should only judge.** Target
   discovery, collision checks, red/green proof, CI state, dedup — each is deterministic.
   What remains for the model: is this survivor a real behavior gap, and what test kills it.

## 2. What is genuinely strong — do not break these

| # | Strength | Evidence |
|---|---|---|
| S1 | **Law 1 is stated correctly for test-only work** — the mutant, not `main`, is the defect. Most agent-test workflows get this wrong and demand a test red on main, which is impossible by construction for coverage work. | `SKILL.md` five laws; `references/04` evidence contract; engine issue template |
| S2 | **Composite content-hash dedup** (`<source9>:<test9>` at HEAD blob) — a weakened companion test reopens the audit. Commit-identity dedup (V1) could never express that. | `ledger.py`, `references/02`; `test_test_file_change_invalidates` |
| S3 | **Deterministic, dependency-free core with real tests.** Argv-only execution (no `shell=True`), NUL-delimited git output, exit-code discipline (0/1/2/3), crash-recovery sidecars, pid locks, process-group kills. 50 tests, ~8.5 s, no network. This is far above the median skill. | `scripts/autonomous/*`, `tests/test_v2.py` (run for this audit: **OK**) |
| S4 | **The freeze anatomy is a real contribution.** "Token starvation is a third failure mode" + knock rate-limit + same-tick model ladder — verified against an 8–9 h freeze, with a fake-`opencode` regression test. Nothing in the Ralph/loop literature covers pool death this concretely. | `references/05`, `assets/watchdog.sh`, `tests/watchdog_rotation_test.sh`, PR #5 |
| S5 | **PRs are owned to merge** (head-sha green, 2-iteration cap, per-PR state line, owner-ask re-asked every wake, protected-file rows batched per wave). As *policy* this is exactly right. | `references/04` "The CI green loop", PRs #7, #8 |
| S6 | **Honest limits, scar tissue with dates.** Every "verified 2026-10-20" note is a bug that already bit; the skill says the regex engine is a fallback, gates verify conformance not truth, etc. This makes the skill trustworthy to hand to a weaker model. | throughout |
| S7 | **Skill mechanics are correct**: 190-line `SKILL.md`, one-line frontmatter description, progressive disclosure into references, trigger-rich description with an explicit do-not-use clause. | `SKILL.md`; `scripts/check_skills.py` |

## 3. Findings

Severity is *for the headless-machine deployment*, not for occasional interactive use
(interactive use is well served today). IDs are referenced from the roadmap in §7.

### 3.1 The second half of the machine is prose **[P0 theme]**

**F1 — Snapshot accumulator, journal schema, PR-state lines, ownership map, dead-pool ledger:
all specified, none shipped.** `references/05` says the hourly snapshot timer "caught a full
merge wave while the orchestrator was frozen" — but `assets/` contains no snapshot script and
no second timer unit, and there is no defined journal/PR-state format anywhere. Every
deployment (and every fresh Orchestrator) re-invents these formats in prose, and a restarted
session must parse its predecessor's free text. The most failure-exposed state in the system
is the least formalized.

**F2 — No PR/CI state tooling.** The CI green loop (S5) requires, every wake, per PR: head sha
vs green-run sha, check classification (in-diff vs referee vs flake/infra), iteration count,
owner-ask status. `orchestrator.py` has exactly three commands (`create-worktree`,
`verify-gate`, `cleanup-worktree`) — none touches GitHub. So the Orchestrator burns model
turns shelling `gh pr checks/view` and eyeballing JSON every wake, and the "state line" lives
or dies by whether the model remembered to write it. A `fleet.py pr-state --json` that emits
exactly the documented state line would make the loop's first 80% free (0 tokens), leaving the
model only dispatch/escalate/merge judgment.

**F3 — No publishing layer at all.** There is no code that creates an issue or a PR: no title/body
template filled from the evidence, no labels, no draft-first claim, no reviewer request, no
issue↔PR linking. Consequences for a *PR/issue machine*:
- The documented claim mechanism ("open a **draft** PR early", `references/02`) never happens in
  the canonical protocol — Quick Start and `references/04` open the PR at step 6/7, *after*
  1.5–4 h of work. The collision window the draft was meant to close is wide open.
- The **Breaker** role ("produces an issue") has no transport: `mutation_engine --output-issue`
  writes one markdown file, for the *first* survivor only, to a `docs/tasks/` path that is never
  explained, gitignored, or filed. Issue-generation — half your stated purpose — is manual.
- PR bodies are free-text `"..."` in every example, so the evidence contract (base sha, seed,
  red/green pair) depends on worker diligence. See F9.

**F4 — Merge authority contradicts itself across the skill.** `SKILL.md`: the Orchestrator
"merges their PRs on green CI". `references/05` blast-radius: token must have "no merge",
branch protection must make it so the agent "*cannot* merge even if it tries", "assume any
unattended run may need a human merge queue". `references/04`: "the merge decision stays with
the orchestrator/owner". On a headless VM this must be one configured policy
(`merge_policy: never | owner-label | auto-green-test-only`), not three vibes — and if it is
`never`, the governor in F6 becomes even more load-bearing.

### 3.2 Memory: the machine cannot learn "no" **[P0]**

**F5 — No negative ledger.** The ledger records only `COMPLETED`/`SOUND` per file. It cannot record:

- *owner rejected this work* (closed-unmerged PR) → because the row only lands when work merges,
  a rejected target stays eligible and the fleet **will re-propose it**, possibly repeatedly —
  the fastest way to make an owner switch the machine off;
- *equivalent mutant / triaged noise* → Stream A triage verdicts live only in a summary string.
  The ledger is per-**file**, so (a) fixing 1 of N survivors and recording `COMPLETED` marks the
  whole file audited and orphans the other N−1 survivors until the file happens to change;
  (b) the same equivalent mutant at the same line will be re-triaged by an LLM every time a
  future hash change reopens the file. Per-**mutant** disposition
  (`file:line:mutated-text-hash → KILLED-BY #pr | EQUIVALENT | NOISE | WONTFIX`) is the missing
  memory structure;
- *superseded-by-other-fleet/human* (partially handled by convention text in `references/07`,
  not by any status or sync).

Your sibling skill **fork-upstream-sync** already ships this pattern ("machine-readable
negative ledger + 0-token deterministic pre-filtering… so an agent stops re-auditing
already-rejected changes"). This skill needs the same organ, fed by a periodic
`gh pr list --state closed` / issue-close sync. For a PR-generating machine this is not a
nice-to-have; rejection memory *is* the difference between a colleague and a spammer.

### 3.3 No governor, no metrics — "never idle" with no counterweight **[P0]**

**F6 — The fleet is optimized against idleness, not against review debt.** PR #9 added strong
anti-parking rules (rightly — the fleet had gone quiet for 5 h). But there is no opposing force:
no max-open-PRs, no PRs/day cap, no pause-when-merge-rate-drops, no kill-switch file, no
budget (tokens/wall-clock/CI-minutes) per wave. Mature bot fleets learned this the hard way:
Renovate ships `prConcurrentLimit` (default 10) and `prHourlyLimit` (default 2), Dependabot
caps open PRs per ecosystem (default 5), and Renovate's most effective anti-flood control is an
*approval dashboard* — work waits as a checkbox, not a PR (§5.5). When the owner is the
bottleneck, the correct machine behavior is to **stop generating and drive the existing queue
to merge** — today the skill says the opposite ("look wider").

**F7 — Zero observability.** Nothing computes: PRs opened/merged/rejected per week, merge rate
by stream/model, survivor→merged conversion, equivalent-mutant rate, CI iterations per PR,
time-to-merge, freeze hours, cost per merged PR. Without an acceptance-rate feedback loop the
machine cannot tell good weeks from slop weeks, and neither can you — you'd have to hand-count
`gh` output. A `fleet.py stats` over `gh` + ledger + watchdog log is a weekend-sized script
that changes how every other knob gets tuned. (Meta's ACH reports a 73% engineer-acceptance
rate for generated tests and tracks it as a headline metric — §5.2. You currently have no
equivalent number for your own fleet.)

### 3.4 Discovery economics: the queue is alphabetical **[P1]**

**F8 — `ledger next` orders by priority prefix, then lexicographically; nothing ranks by value.**
(And until the PR accompanying this audit, multiple `priority_paths` didn't even rank among
themselves — see B1.) A 300-file repo gets audited a→z regardless of churn, complexity,
coverage, defect history, or what changed yesterday. Everything external points the other way:
Google's production mutation system is *diff-first* (mutants only on changed, covered lines,
surfaced in review), test-impact/risk practice ranks by churn × complexity × (1−coverage), and
your own engine already supports `--diff-base` — but discovery never uses it (§5.3). The
highest-signal targets for a hardening fleet are: files that changed since last audit, files in
this week's diffs, flaky/failing-on-main tests, high-churn hotspots. Lexicographic order spends
your scarcest pools on whichever file sorts first. Relatedly, discovery is *only* "files with no
fresh ledger row" — the machine never consumes existing GitHub issues, red `main` CI, or flaky
test signals, which are pre-triaged, pre-justified work.

### 3.5 Verification integrity: the fleet grades its own homework **[P1]**

**F9 — The evidence contract is prose; nothing checks the evidence exists.**
`orchestrator.py verify-gate` runs format + analyze + `gate_cmd` — notably **not the tests**,
**not** `test_weakening_check.py`, and nothing validates that the claimed red/green pair was
actually run (correct mutant? correct base sha? both commands?). The Sentinel who would catch
this is another prompt in the same fleet. Given measured reward-hacking rates in coding agents
(METR observed spontaneous test-gaming in 30%+ of some eval runs; roughly half of
test-passing SWE-bench PRs would not be merged by maintainers — §5.6), "worker asserts
red/green in a PR body" is the weakest link in an otherwise hard-nosed design. Fix shape:
a script (`evidence.py`) that *performs* the red run (apply mutant → targeted test → expect
fail) and the green run, plus a **pass-to-pass** regression set (the full mapped test file, and
ideally the file's test-impact set) — SWE-bench's `FAIL_TO_PASS` × `PASS_TO_PASS` pair — and
writes a machine-checkable `evidence.json` (base sha, commands, exit codes, mutant diff) that
`verify-gate` and the Sentinel *read* instead of trusting prose. PR bodies should be *rendered
from* that artifact (F3).

**F10 — Mutation-engine verdicts are softer than they look on a loaded VM.**
- `TIMEOUT` counts as *killed* by default at a fixed 60 s. On a contended headless box,
  slowness masquerades as test strength. Production tools use baseline-relative timeouts
  (cargo-mutants: 5× baseline, 20 s floor; Stryker: net-time × factor + constant — §5.3).
- The baseline runs **once**. A flaky mapped test therefore "kills" mutants at coin-flip rates
  and can also manufacture survivors; nothing re-confirms a survivor with a second clean run
  before an LLM spends an hour on it. Flaky-baseline quarantine is a stated *prerequisite* in
  the scale literature, not an enhancement.
- Rule packs are thin (Python 17, TS/JS 13, Dart 18, Go 10 rules) and regex-only: no arithmetic
  swap, return-value, string-literal, argument-swap, or statement-deletion mutants — the
  classes AST tools (Stryker, PIT, mutmut, cargo-mutants, gremlins) cover routinely. The skill
  is honest that this is a fallback (§S6), but for a *machine* the fallback's false-survivor
  rate is a direct tax on every downstream LLM hour. The strategic move is an adapter: if a
  real mutator exists for the stack, drive it (JSON report in → same ledger/triage flow);
  keep the regex engine only where nothing else exists (today: mainly Dart).
- No coverage/per-test mapping: every mutant runs the whole mapped test *file*. Per-test
  coverage selection is the single biggest known speedup (5–20×) and would multiply how many
  mutants a night the VM can verdict before an LLM is even woken (§5.3).

**F11 — Stream B has no deterministic pre-filter.** Vacuous asserts, `sleep`/`Future.delayed`,
mock-returns-X-assert-X are all grep/AST-detectable smells. Today every Stream B round costs a
full LLM read of a test file to find what a 0-token scanner could shortlist (the exact pattern
your fork-upstream-sync skill is built around). The LLM's job should start at "here are 4
candidate smells with line numbers — judge them", not at discovery.

### 3.6 Liveness substrate: great scar tissue, fragile foundation **[P1]**

**F12 — The design immortalizes one session; the ecosystem converged on the opposite.** Beats,
the session-id file, and watchdog nudges all exist to keep *one conversation* alive for days —
yet the journal/snapshot/ledger already externalize everything that matters, and your own field
notes record the substrate failing (server restart kills beats; queued nudges burst 7×; pool
death freezes the one session that every wake channel funnels through). Ralph-style loops,
OpenHands headless runs, Copilot/Codex cloud agents and Claude Code routines all use the
inverse shape: **fresh short-lived session per work unit; state only in files/git** (§5.1, §5.4).
A stateless-wake Orchestrator — each wake is a *new* headless run that reads journal + snapshot
+ PR-state, acts, appends, exits — deletes whole failure classes (no session id to resurrect,
no beats to re-arm, no context rot, watchdog becomes a plain cron that *starts* work instead of
reviving a conversation). The watchdog/ladder work (S4) survives this change intact as the
starter motor. This is the deepest architectural decision in the skill and the one most worth
revisiting deliberately rather than accreting more revival machinery.

**F13 — Watchdog details that bite at 3 a.m.** (two fixed in the accompanying PR):
- **B3** The shipped systemd service had no `EnvironmentFile`, so `ORCH_MODEL_LADDER` & friends —
  the entire model-rotation feature — were **unreachable** in a stock install: systemd does not
  inherit shell-profile exports. Users had to hand-edit the unit (undocumented) or rotation
  silently never engaged under the timer.
- **B4** `install-watchdog.sh` defined a shell function named `install()`, shadowing
  `/usr/bin/install`; its own `install -m …` calls recursed until bash segfaulted (reproduced
  for this audit: exit 139). **The shipped installer could not complete.** Presumably every real
  deployment used the documented manual commands instead — which is how this survived.
- **B5** On an all-models-quota-failed tick the log claimed the heartbeat was left stale, but it
  had been touched as an attempt marker — retries were delayed by a freshness nobody earned.
- Still open: quota detection greps free-text agent output (`429`, `quota`…) — an agent that
  merely *discusses* rate limits can get a healthy model parked on the cooldown list;
  `model_failures` grows unboundedly; the watchdog always exits 0 so systemd can never alert;
  there is no owner notification path at all (a frozen fleet is discovered by silence — an
  hourly "fleet heartbeat" issue/comment or ntfy/webhook ping with the §F7 stats would close
  the loop); nothing auto-starts the *session* after a VM reboot (linger keeps the timer, the
  timer nudges a session id that may no longer exist — F12 removes this class too).

**F14 — Polling where events exist.** Wakes are timed sleeps; CI completion and merges are
discovered ≤60 min late and cost a model turn each. GitHub-native triggers (Actions
`workflow_run`, webhooks to a tiny local listener, or `gh pr checks --watch` in the snapshot
script) would let the deterministic layer accumulate state continuously and spend model turns
only when a decision is actually pending.

### 3.7 Safety on an unattended VM **[P1]**

**F15 — Worktree isolation is a convention, not a sandbox.** Workers run `--auto` with the
user's full filesystem: other worktrees, the watchdog state dir, any credentials on disk.
Your blast-radius section gives good *advice* (scoped token, branch protection, dedicated user)
but nothing *verifies* it. Every hosted equivalent (Copilot coding agent, Codex cloud, OpenHands)
runs each task in an ephemeral, network-off-by-default container — on a self-hosted VM the
cheap equivalents are: a `doctor` preflight command that fails closed (token scopes include no
merge/admin if policy says so, branch protection on, linger on, ladder models actually probe
alive, disk headroom, `gh auth` sane, clock sane); running workers under a dedicated user with
the worktree as its only writable tree; and treating all repo/issue text as untrusted input
(prompt-injection surface grows the moment F3/F8 teach the machine to read issues).

### 3.8 The skill as a product **[P2]**

**F16 — Your own tests never run in CI.** `.github/workflows/check.yml` validated frontmatter,
links, discovery and README freshness — but not the 50-test suite or the watchdog rotation test.
A scripts regression could merge green. (Fixed in the accompanying PR.)

**F17 — Evals cover V2's risks, not today's.** The 5 behavioral cases (streams, dedup,
Stream-C refusal, PR collision) are well written and *manually* graded — but the behaviors added
by PRs #1–#9 (cold start ordering, model rotation, never-park, CI green loop, ledger batching)
have **zero** eval coverage, and nothing measures the skill against a no-skill baseline, across
the free models the fleet actually runs. SkillsBench's paired with/without methodology and
trigger-rate evals are the current standard here (§5.7); at minimum, add orchestrator-behavior
cases with fake `gh`/`opencode` stubs like the existing `open-pr-python` fixture.

**F18 — Small contradictions/dead ends a worker can trip on:**
- Quick Start step 6 told workers to record the ledger row *in the same PR* while PR #8's rule
  says never bundle a protected ledger — with no config flag (`ledger_mode: bundled|batched`)
  to tell the worker which repo it's in. (Wording reconciled in the accompanying PR; the config
  flag is roadmap.)
- `machine_budget.max_concurrent_tests` is defined in every config and **read by no code** —
  pure decoration that looks like a guarantee.
- `--test-cmd` is a shlex-parsed string while every other command in the system is an argv
  array; quoting traps are documented rather than designed away.
- `ledger next` shells `gh pr list --limit 200` with no truncation warning; claims beyond 200
  open PRs are silently invisible. `parse_ledger` silently *skips* malformed/unknown-status
  rows (a hand-edited typo erases an audit without a sound).
- Reference numbering skips `06` (folded into `05` in V2) — trivial, but it's the kind of thing
  a weak model burns turns wondering about. `docs/` ships the 925-line V2 spec + changelog
  archaeology alongside the skill.
- The description triggers well for interactive asks but never mentions *unattended / overnight /
  fleet / issue generation* — the machine use-case relies on "skill as only prompt" phrasing a
  new user won't guess.

### 3.9 Verified-bug table (all reproduced during this audit)

| ID | Bug | Proof | Status |
|---|---|---|---|
| B1 | Multiple `priority_paths` collapse to one bucket; config order ignored (`common._sorted_by_priority`) | new `test_priority_paths_rank_in_config_order` red on `main` | fixed in accompanying PR |
| B2 | ~~installer recursion~~ see B4 | — | — |
| B3 | systemd unit has no `EnvironmentFile`; `ORCH_MODEL_LADDER` unreachable in stock install | `grep Environment assets/*` empty | fixed in accompanying PR |
| B4 | `install-watchdog.sh` `install()` shadows coreutils `install` → infinite recursion → SIGSEGV (139) | staged run during audit | fixed in accompanying PR |
| B5 | All-quota-fail tick logs "heartbeat left stale" after touching it fresh | `watchdog.sh:136,159` | fixed in accompanying PR |
| B6 | Skill's test suites absent from repo CI | `.github/workflows/check.yml` | fixed in accompanying PR |

## 4. The pipeline, today vs. machine-complete

| Machine stage | Today | Should be |
|---|---|---|
| Discover | `ledger next` (lexicographic) ✅ code | risk-ranked queue (diff/churn/coverage/flake/issue intake), still `ledger next` |
| Claim | prose ("draft PR early") — not in protocol ❌ | `fleet.py claim` → draft PR / claim lease, checked by `next` |
| Prove defect | mutation engine ✅ code (regex) | AST mutator adapter where available; baseline-relative timeouts; survivor re-confirmed; per-mutant record |
| Build | worker LLM ✅ (the right use of a model) | unchanged — plus Stream-B scanner shortlist as input |
| Self-verify | format/analyze only ⚠️ | `evidence.py` performs red/green + pass-to-pass; `verify-gate` checks artifact + weakening |
| Publish | manual `gh pr create`, free-text body ❌ | `fleet.py publish` renders PR/issue from evidence artifact; labels; dedup vs open **and closed** |
| Drive CI | prose loop, LLM parses `gh` every wake ❌ | `fleet.py pr-state --json`; LLM only decides fix/escalate/merge |
| Merge | contradictory prose ❌ | one `merge_policy` config; merge queue where available |
| Record | ledger row ✅ code | + negative ledger sync from closed PRs/issues; per-mutant dispositions |
| Learn / govern | anti-idle prose only ❌ | `fleet.py stats`; WIP/rate caps; kill switch; weekly owner digest |
| Stay alive | beats + watchdog ✅ code (fragile substrate) | stateless wakes started by timer; watchdog as starter; `doctor` preflight |

## 5. What similar systems do (and what to steal)

### 5.1 Ralph loops & fresh-context agents
The Ralph pattern (Huntley, 2025; since productized as a Claude Code plugin and echoed by
Codex's `/goal`) inverts your liveness bet: every iteration is a *fresh* agent; memory is
`prd.json` (machine-readable task list with `passes` flags) + append-only `progress.txt` +
git. Nothing to freeze, nothing to resurrect. **Steal:** stateless wakes (F12) and a
machine-readable queue file — your ledger is already 80% of `prd.json`; the journal should be
held to the same standard. *Sources:* [snarktank/ralph](https://github.com/snarktank/ralph),
[FutureAGI on the Ralph loop](https://futureagi.com/blog/loop-engineering/ralph-loop/).

### 5.2 Meta ACH — mutation-guided LLM test generation at scale
The closest industrial relative of Stream A. ACH generates few, *concern-specific* mutants with
an LLM, filters equivalent mutants with a dedicated agent (+ static preprocessing: precision
0.95 / recall 0.96), then generates tests guaranteed to kill them. Deployment: 10,795 classes,
9,095 mutants, 571 tests, **73% engineer acceptance** — and 49% of those tests would have been
missed by coverage-guided generation. **Steal:** (a) an equivalence pre-filter stage before
expensive triage (F5/F10), (b) optional LLM-generated *semantic* mutants layered over the
regex pack for high-value files, (c) acceptance rate as the fleet's headline metric (F7).
*Sources:* [Meta engineering blog](https://engineering.fb.com/2025/09/30/security/llms-are-the-key-to-mutation-testing-and-better-compliance/),
[FSE'25 paper (arXiv:2501.12862)](https://arxiv.org/abs/2501.12862).

### 5.3 Google diff-mutation & the mature mutator ecosystem
Google runs mutation *on the diff, during review*: mutants only on changed covered lines, a
capped number surfaced as review comments, humans mark which must die. Stryker/PIT/cargo-mutants/
mutmut add: incremental result reuse (Stryker reuses ~94% of verdicts across runs), per-test
coverage selection (5–20×), baseline-relative timeouts. **Steal:** diff-first discovery (F8),
a reviewer-facing mode (comment survivors on *human* PRs — a new, low-trust product surface for
the same engine), real-mutator adapters + incremental verdict cache keyed by your existing
composite hash (F10). *Sources:* [Petrović et al., Practical Mutation Testing at Scale](https://arxiv.org/pdf/2102.11378),
[Stryker incremental docs](https://stryker-mutator.io/docs/stryker-js/incremental/),
[PandaStack on mutation at scale](https://www.pandastack.ai/blog/mutation-testing-at-scale-ephemeral-microvms/).

### 5.4 Hosted issue→PR agents (Copilot coding agent, Codex cloud, OpenHands, Devin)
Uniform shape: issue/task in → ephemeral sandbox (network off in agent phase) → **draft** PR
early → iterate against checks → request review; session logs inspectable; spend caps native.
OpenHands adds a trajectory *critic* that can abort a doomed run before a human ever sees it,
and an `/iterate` loop that keeps a PR draft until verification passes. **Steal:** draft-first
as the real claim+visibility mechanism (F3), sandboxing expectations (F15), a cheap pre-push
critic gate for obviously-doomed diffs, and — where you don't need free-pool arbitrage —
consider whether a hosted runner (GitHub Actions + headless CLI, or Claude routines) deletes
your VM babysitting entirely (F12/F14). *Sources:*
[OpenHands verification stack](https://www.openhands.dev/blog/20260506-the-verification-stack),
[Copilot coding agent](https://github.blog/changelog/2025-10-28-copilot-coding-agent-now-supports-self-hosted-runners/),
[headless modes compared](https://dev.to/gabrielanhaia/headless-mode-in-5-agents-claude-p-codex-exec-gemini-p-copilot-p-opencode-run-4816).

### 5.5 Bot-fleet governance (Renovate / Dependabot)
Twenty years of "machine opens PRs" scar tissue: concurrent/hourly PR caps by default, grouping
related changes into one PR, schedules, and approval-before-PR dashboards. **Steal wholesale**
(F6): caps as config with sane defaults (e.g. ≤5 open fleet PRs, ≤2 new/hour), same-file
survivor grouping (your own PR #9 evidence — 72 survivors / 31 tests in one round — already
violates the written one-finding-per-PR rule; make grouping a *policy with limits* instead of
an accident), and an issue-based approval queue for lower-trust work.
*Sources:* [Renovate automerge/noise docs](https://deepwiki.com/renovatebot/renovate/7.3-automerge-and-noise-reduction),
[Dependabot vs Renovate operational comparison](https://safeguard.sh/resources/blog/dependabot-vs-renovate-operational-experience).

### 5.6 Why the governor & sentinel can't stay prose: acceptance & reward-hacking data
Agentic PRs are accepted less and rejected mostly for fit/size reasons [empirical study of
agentic PRs](https://arxiv.org/html/2509.14745v3); METR found ~half of test-passing SWE-bench
PRs would not be merged by maintainers and 30%+ spontaneous reward-hacking in some evals
[METR](https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/).
Vendor telemetry (treat as directional) puts AI PR acceptance far below human PRs
[LinearB 2026](https://linearb.io/library/ai-in-software-development). **Implication:** your
F5/F6/F9 fixes are not hygiene — they are the difference between a fleet whose output compounds
and one whose PRs get bulk-closed.

### 5.7 Skill engineering itself
Anthropic's guidance (orchestrator-workers + evaluator-optimizer with *concrete, capped*
pass conditions; progressive disclosure; SKILL.md <500 lines) matches your structure — your gap
is the evaluator being prose instead of a deterministic artifact check (F9). SkillsBench shows
curated skills add +16.6 pp on average but with high harness variance — i.e., **eval on the
models/harness you actually run** (F17). *Sources:*
[Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents),
[Equipping agents with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills),
[SkillsBench](https://arxiv.org/abs/2602.12670).

## 6. Target architecture (V3 of the machine, same skill)

Keep the five laws, the ledger, the engine, the watchdog ladder. Add a thin deterministic
`fleet.py` spine and re-platform liveness; change nothing about what a worker *judges*.

```
systemd timer / GH events
  └─ fleet.py snapshot        (0 tokens: main sha, PR states, worktrees, pool probes → state/)
  └─ fleet.py wake            (stateless headless run, fresh session; ladder-rotates like today)
        reads: state/*.json + ledger + negative ledger     (never parses prose state)
        decides ONLY: dispatch / fix / escalate / merge / report
Workers (unchanged judgment, new rails):
  fleet.py claim → draft PR ── evidence.py red-green (artifact) ── fleet.py publish (ready PR / issue)
Governor (in fleet.py wake, before any dispatch):
  open fleet PRs ≥ cap? merge-rate(14d) < floor? kill-switch present? pool probe dry? → tend queue only
Weekly: fleet.py stats → owner digest issue (merged/rejected, acceptance %, freeze hours, top equivalent-noise files)
```

Design principles, stated once so future PRs can cite them:

1. **If it happens every wake, it's code, not prose.** Prose remains for judgment and for the
   scar-tissue *why*.
2. **The objective is merged PRs per owner-minute.** Every throughput rule ("never park") must
   be paired with a governor rule ("never flood").
3. **Every "no" is data.** Rejections, equivalents, and supersessions are first-class rows.
4. **Evidence is an artifact, not a claim.** Scripts produce it; PR bodies are rendered from it.
5. **Sessions are disposable; files are the memory.** (Ralph, and your own journal instinct,
   taken to its conclusion.)

## 7. Roadmap

**Done in the PR accompanying this audit** (each with fail-to-pass evidence):
B1 priority ranking (+test), B3 watchdog `EnvironmentFile` + installer writes `watchdog.env`,
B4 installer `install()` recursion (+staged-install regression test), B5 heartbeat honesty
(+rotation-test assertion), B6 skill suites wired into CI, Quick Start ↔ batch-rule wording.

**P0 — make the machine complete & self-limiting** (issues filed with this audit, 2026-10-21):
1. [#11](https://github.com/cassandre60/skills/issues/11) Ship fleet state as code: snapshot script + timer, JSON schemas for journal/PR-state (F1, F2).
2. [#12](https://github.com/cassandre60/skills/issues/12) Negative ledger + per-mutant dispositions, synced from closed PRs/issues (F5).
3. [#13](https://github.com/cassandre60/skills/issues/13) Governor + metrics: WIP/rate caps, kill switch, `fleet.py stats`, owner digest (F6, F7).

**P1 — make every PR cheaper to trust:**
4. [#14](https://github.com/cassandre60/skills/issues/14) `evidence.py` (performed red/green + pass-to-pass) and `verify-gate` enforcement (F9).
5. [#15](https://github.com/cassandre60/skills/issues/15) Risk-ranked discovery + issue/CI intake for `ledger next` (F8).
6. [#16](https://github.com/cassandre60/skills/issues/16) Real-mutator adapters, baseline-relative timeouts, survivor re-confirmation,
   incremental verdict cache (F10).
7. [#17](https://github.com/cassandre60/skills/issues/17) `fleet.py publish` + draft-PR claiming + Stream-B 0-token scanner (F3, F11);
   `ledger_mode` + `merge_policy` config (F4, F18).

**P2 — re-platform & prove it:**
8. [#18](https://github.com/cassandre60/skills/issues/18) Stateless-wake Orchestrator + harness adapter table (opencode/Claude Code/Codex headless);
   event-driven wakes (F12, F14).
9. [#19](https://github.com/cassandre60/skills/issues/19) `doctor` preflight + sandboxing guidance for the VM (F15).
10. [#20](https://github.com/cassandre60/skills/issues/20) Eval harness v2: orchestrator-behavior cases, trigger evals, with/without-skill runs on the
    fleet's actual models (F17).

**Metrics to run the machine by** (from `fleet.py stats`, reviewed weekly):
north-star = *merged fleet PRs ÷ owner review minutes*; guardrails = 14-day acceptance rate
(floor ~60% before auto-anything), open fleet PRs (cap), median CI iterations/PR (≤2 by
policy — verify), duplicate/re-proposal count (target 0), freeze hours/week, cost per merged PR
per model (routes the ladder with data instead of hope).

**What not to do:** don't add Streams C/D before P0 lands (more work types into an ungoverned
queue makes F6 worse); don't auto-merge before F7 shows a stable acceptance rate; don't replace
the markdown ledger with a database (git-tracked append-only is a feature — add the negative
ledger *beside* it); don't build a multi-VM coordinator (the queue/governor design already
tolerates a second fleet via draft-PR claims).

## Appendix A — file-by-file notes (abridged)

- `common.py` — solid; `machine_budget` dead (F18); glob engine lacks `[...]`/`{a,b}` (fine, but
  say so in `stack-adapters`); `test_for_source` returns a mapped path even when absent at HEAD —
  callers handle it, but the contract deserves a comment.
- `ledger.py` — newest-wins/escaping/union-merge all tested; silent row-skip + 200-PR truncation
  (F18); date-granularity ties under union merge are position-dependent — acceptable, note it.
- `mutation_engine.py` — best-engineered file in the skill (SourceGuard, lexer mask, seeded
  sampling); timeout/flake/adapter items in F10; `--output-issue` first-survivor-only (F3).
- `orchestrator.py` — worktree registry & cleanup safety are exemplary; name overpromises (no
  orchestration of PRs/models — that gap is F2); `verify-gate` gaps in F9.
- `test_weakening_check.py` — good heuristic tripwire; unused by `verify-gate` (F9); rename/copy
  handling drops the *old* path's removed assertions on test-file renames (minor).
- `assets/watchdog.sh` — see F13/B3–B5; `install-watchdog.sh` — B4 (fixed).
- `references/05` — highest-value prose in the repo; also the file with the most unshipped
  machinery (F1). `references/04` — canonical protocol is clear; batching contradiction (F18).
- `evals/` — well-formed, stale scope (F17). `tests/` — 50→52 tests with this audit's PR, now in CI (B6).

## Appendix B — verification performed for this audit

- `python3 -m unittest discover -s tests` in `skills/autonomous-improvement`: **50 tests OK**
  pre-audit; **52 OK** after the accompanying PR's fixes.
- `bash tests/watchdog_rotation_test.sh`: PASS (incl. new stale-heartbeat assertion).
- Staged `install-watchdog.sh` with stubbed `systemctl`/`systemd-analyze`/`loginctl`:
  pre-fix exit **139** (recursion segfault, B4); post-fix exit 0 for default and custom dirs,
  unit rewritten correctly, `watchdog.env` created mode `0600`.
- Priority-order test red→green transcript in the accompanying PR body (Law 1, applied to itself).
