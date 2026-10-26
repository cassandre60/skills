# The Toolbox — Per-Tool Contracts

Every tool in `SKILL.md`'s table has a contract here: when to reach for it,
what its **finder** is (the cheap, ideally 0-token step that enumerates
candidates), what its **oracle** is (the command or proof that decides — "if
the oracle cannot be a command, it is not a tool"), what its **evidence**
must attach to the PR, and the **output format** the pass emits. Deep
checklists live in `03-streams-and-triage.md`; the stream contract behind
each tool's letter is in `08-recursive-self-improvement.md`.

Two rules bind every tool, and they are laws, not advice:

1. **A finding counts only with a file, a line, a concrete trigger, and a
   failing test.** A finding without a failing test is a hypothesis, not a
   result; it goes to the issue tracker, not the ledger.
2. **One lens and one output format per pass.** An open-ended request
   ("improve my app") produces generic advice. Name the lens, name the
   output, run the pass, stop.

## Audit passes and the refutation pass

Tools marked *audit* (adversarial audit, lens passes, spec-audit) are model
reads, not scripts. Their discipline:

- The **finder** is whatever 0-token shortlist exists (`stream_b_scan.py`,
  `docs_truth.py`, the ledger); the model judges the shortlist, it does not
  browse for work.
- The pass emits its **one output format** (below), then a **second,
  separate pass tries to refute every finding** — re-derive it from the code,
  look for the guard that already handles it, try the counterexample. Only
  findings that survive refutation are reported, and each survivor still
  needs its failing test before anything lands.
- False alarms are the main way audit passes waste budget; refutation exists
  to spend the budget on real findings instead.

## The tools

### docs-reconcile (stream I)

- **Reach for it when:** docs, plans, or changelogs may drift from what the
  code does — always part of a first cycle.
- **Finder:** `python3 scripts/autonomous/docs_truth.py check --json`.
- **Oracle:** a contradiction between a doc claim and the traced code/symbol;
  the tool traces spec ids and referenced paths mechanically.
- **Evidence:** each contradiction with file+line on both sides and the
  one authoritative place that should hold the rule.
- **Output format:** `doc <path>:<line> says X; <code path>:<line> does Y;
  authoritative home: <path>`.
- **Deep dive:** the tool's `--help`; repo-level spec in `SPEC.md` if present.

### spec-audit (stream I)

- **Reach for it when:** the repo claims rules (a SPEC, an AGENTS file, gate
  docs) that may lack enforcement or a test that can fail.
- **Finder:** `python3 scripts/autonomous/docs_truth.py check --spec SPEC.md`
  (auto-detects a SPEC file by default).
- **Oracle:** for each rule: the code that enforces it and a test that would
  fail if the rule broke. A rule with neither is a finding.
- **Evidence:** the rule, the enforcement point, the failing test — or the
  gap stated plainly.
- **Output format:** `rule <id>: enforced at <path>:<line>; proven by
  <test name>` / `rule <id>: NO ENFORCEMENT` / `rule <id>: NO FAILING TEST`.

### mutation testing (stream A)

- **Reach for it when:** the question is whether the suite actually protects
  behavior — after the foundation passes, on a repo with a real test suite.
- **Finder:** `mutation_engine.py --source <target> --test-cmd "<scoped cmd>"`
  (regex rule packs; `--backend stryker` normalizes external reports).
- **Oracle:** a surviving mutant triaged as a real gap; the new test is red
  with the mutant applied and green on clean source (Law 1's test-only arm).
- **Evidence:** named mutant (file:line:id), red/green commands and results,
  which oracle files the kill criterion named, base sha.
- **Output format:** the engine's verdict list (`KILLED` / `SURVIVED` /
  `UNCOMPILABLE` / `TIMEOUT`), triaged per survivor.
- **Deep dive:** `01-mutation-engine.md` (statuses, safety, sampling).

### adversarial audit (stream B)

- **Reach for it when:** tests may be vacuous, mock-bound, or timing bombs —
  and as the home of any **lens pass** (below).
- **Finder:** `stream_b_scan.py <paths> --json` (0-token smells: vacuous
  asserts, sleeps, mock-returns-X-assert-X). Its output is triage input,
  never findings.
- **Oracle:** the stronger assertion written and shown to fail on the broken
  behavior it now catches.
- **Evidence:** the vacuous/flaky behavior, the stronger assertion, a
  before/after run.
- **Output format:** one finding per row: `file:line — kind — what the test
  fails to catch — the assertion that would catch it`.
- **Deep dive:** the harness checklist in `03` ("measure what production
  measures", mock fidelity, sleep/sync discipline).

### defect repair (stream E)

- **Reach for it when:** an issue or a red CI run claims a real bug.
- **Finder:** `repair.py intake --reports <ci-or-issue exports>`.
- **Oracle:** `verify-red` (repro red on unmodified base) then `verify-green`
  (green after the fix, spot-checked).
- **Evidence:** the evidence artifact with base sha, red and green runs.
- **Output format:** `issue → repro → fix files → red/green exits`.

### flake & determinism (stream F)

- **Reach for it when:** a test fails intermittently or inherits the host
  environment (ambient env vars, wall clock, port contention).
- **Finder:** `flakes.py finder --history <ci history>`.
- **Oracle:** `flakes.py oracle` — N runs (contract: ≥ 20) that reproduce the
  flake and pass under the controlled environment; the fix pins the
  environment explicitly.
- **Evidence:** the flaky run history, the N-run proof, the environment diff.
- **Output format:** `test — failure class — uncontrolled input — the pin`.

### performance (stream G)

- **Reach for it when:** a hot path has a number to beat. Measured, not
  guessed: the benchmark exists before the optimization.
- **Finder:** profiling or the owner's report; never a hunch.
- **Oracle:** `perf_gate.py` — bench delta **and** output equivalence
  (differential against the old implementation), both mandatory.
- **Evidence:** before/after numbers on a pinned harness, spread not just
  median.
- **Output format:** `fn — old/new timings (spread) — equivalence verdict`.

### security & dependencies (streams H+K)

- **Reach for it when:** secrets, dependency advisories, or a risky version
  bump are in play. Ask what data leaves the device or server, whether
  secrets sit in code or git history, how inputs validate, which permissions
  are unnecessary.
- **Finder:** `advisory.py secrets` (redacting scan), `advisory.py deps
  --advisory <feed> --manifest <lock>`.
- **Oracle:** the scan diff (secrets), the advisory/manifest intersection
  (deps), the `bump` oracle: tests pass **after** the bump against the new
  oracle-first test-cmd.
- **Evidence:** the finding with location and the remediation's proof.
- **Output format:** `kind — location — exposure — remediation — proof`.

### simplification (stream J)

- **Reach for it when:** dead or redundant code is suspected. Deletion is
  judgment-heavy: know what depends on what.
- **Finder:** `simplify_gate.py finder` (usage/coverage shortlist).
- **Oracle:** `simplify_gate.py oracle` — tests stay green **and** the
  mutation score does not fall in the touched area.
- **Evidence:** the before/after metrics and the green run.
- **Output format:** `symbol — why dead — metrics before/after`.

### fuzz (stream C)

- **Reach for it when:** a parser, decoder, or consumer must survive hostile
  input.
- **Finder:** `fuzz_shrink.py run --harness <cmd> --corpus <dir>`.
- **Oracle:** a recorded, shrunk counterexample that replays red
  (`fuzz_shrink.py replay`) and is fixed by the change.
- **Evidence:** the regression file, the replay result, the fix.
- **Output format:** `crash — input (shrunk) — first bad commit/line`.

### lens pass (B-shaped audit)

- **Reach for it when:** a risk class needs one focused read. One lens per
  pass; separate passes find different things. The standing menu:

| Lens | The question the pass asks |
|---|---|
| time & dates | time zones, DST, a wrong device clock, ordering across midnight |
| data loss | a crash mid-write, interrupted saves, upgrades between versions, backup then restore |
| double events | double taps, retries, two tabs, background/foreground races |
| odd inputs | empty, huge, malformed, unusual characters, wrong types |
| failing surroundings | offline, slow network, permission denied, disk full |
| failure paths | for each way an action can fail: what the user sees, and can they recover? |

- **Finder:** the tool's own shortlist where one exists; otherwise the lens
  names the files (bounded by the machine budget).
- **Oracle:** a failing test for each surviving finding.
- **Evidence:** file, line, concrete trigger, failing test.
- **Output format:** `file:line — lens — trigger — failing test name`.

### meta (stream L — gated)

- **Reach for it when:** the fleet reviews its own rules — never by running
  rounds against the skill's own code; the method is reading the fleet's
  session history (`09-session-history-review.md`).
- **Finder:** `meta.py lessons --fixture-dir <dir>` over the journal,
  nudges, watchdog log, snapshots, ledger, and PR/issue timeline.
- **Oracle:** `meta.py replay` — the proposal's rule, replayed against the
  recorded evidence it claims to prevent.
- **Evidence:** the rule it would have prevented, the evidence, the scope.
- **Output format:** a proposal; **never a merge**. Referee surfaces (the
  five laws, the stream contract, eval sets, tier floors, merge policy) are
  issues the owner decides.

### scheduled environment pass (external)

- **Reach for it when:** the cadence fires — at least every 12 months, plus
  harness prompt-shape changes, skill-library churn, or a first-time outsized
  token/latency bill on a trivial model turn. Runs through the sibling skill
  `context-optimization` (formerly `cliproxy-log-inspection`).
- **Finder:** the external skill's
  `../context-optimization/scripts/find_request_logs.py`,
  `render_request_log.py`, and `analyze_context_budget.py` on a fresh
  successful capture; per-section budget (skills catalog, baseline files,
  Code Mode catalog, date/env tail), skill ranking, duplicate flags, captured
  thinking level and `Server-Timing`.
- **Oracle:** after each applied lever, a fresh successful capture with a
  before/after budget on owner-addressable sections. Error captures cannot
  prove savings; a dead-provider fallback is itself a finding to report.
- **Evidence:** measured character counts and captured usage. A complete run
  records: capture id, model, prompt tokens, section budgets, each lever
  applied with before/after chars, and what was parked/restored.
- **Output format:** a before/after table plus a lever log; no repo ledger row.
  Full method and worked receipts: `../context-optimization/references/03-context-budget.md`
  in the sibling skill.
- **Refutation:** read both skills before parking a duplicate pair; verify a
  parked skill is truly out of every discovery directory; re-measure with the
  same model and endpoint before claiming a token drop.

## Adapting the plan

The toolbox is a menu, not a sequence. Derive the order from the repo: a
repo with drift in its docs gets the foundation passes first; a repo whose
CI is red gets defect repair before any new audit; a repo whose suite is
thin gets mutation testing after a test audit, not before; a repo with no
gates gets `gates-and-vocabulary.md`'s fallback, not a lecture. When two
tools plausibly apply, take the one whose oracle is a command and whose
finder is 0-token — and put the other on the menu with one line of why.
The executable form of this section is `scripts/autonomous/survey.py`: it
ranks these same signals with zero tokens and outputs the pick (`chosen`),
the runner-up, and the reason.

The executable form of this adaptation is `survey.py` (`python3 scripts/autonomous/survey.py [--json]`),
which scores each pass from 0-token signals (red CI, docs drift, test smells,
secrets, ledger coverage, baseline-green test commands) without human prompts or
model tokens.
