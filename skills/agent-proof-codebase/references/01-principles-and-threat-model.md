# Principles and threat model

Contents: the idea, the adversary, the catalogue of gaming patterns, design tenets, robust vs antifragile, limits.

## The idea
Code is cheap to produce and expensive to trust. Verification is the scarce resource. A gated codebase
makes acceptance mechanically expensive to fake, so any change that is merged, from any author (human,
weak agent, strong agent), is high-signal by construction: it fixes a real defect, improves a measured
property, or clears a proven edge case, with machine-checked evidence attached.

Two answers exist to floods of low-quality changes: social (close the door, restrict who may submit) and
technical (leave the door open and make it a verifier). This skill implements the technical answer.
The design question for every gate: what does it cost a sloppy or adversarial author to get a bad change
through, and is that cost higher than doing the real work?

## The adversary
Not malice. Optimization pressure. Whoever (or whatever) is rewarded for "CI is green" will find the
cheapest path to green, and the cheapest path is often not the correct one. Research on coding agents
measures this directly: agents hardcode expected outputs, special-case test inputs, and edit test files
when it is easy. Visible tests are an overfittable surface, and the problem worsens exactly in the regime
this skill creates (many retries against a fixed gate).

Consequences for design:
- A gate is only as strong as its resistance to being gamed. Ask "what is the cheapest way to pass
  without being correct, and have I closed it?" for every gate.
- Fixed held-out test sets are a weak defense on their own. Prefer generated inputs with fresh seeds,
  properties over examples, and mutation testing, which measures test strength directly.
- LLM judges can triage and explain. Authority must come from deterministic, non-LLM checks.
- The author must not be able to modify the referee (see protect-the-referee.md).

## Catalogue of gaming patterns
Use this list when designing gates and when building the red-team corpus. Each pattern has the gate
family that counters it.

| # | Pattern | Looks like | Counter |
|---|---------|-----------|---------|
| G1 | Hardcode expected outputs | `if input == X: return Y` | property/generated tests, mutation |
| G2 | Special-case visible test inputs | branches keyed to fixture values | fresh random inputs, differential oracle |
| G3 | Edit/delete the failing test | test removed or rewritten to pass | protected paths, test-weakening tripwire, fail-to-pass |
| G4 | Skip/xfail/ignore | `skip`, `xit`, `@Ignore`, `.only` | tripwire, CI rule forbidding skips without ticket |
| G5 | Weaken assertions | `assertTrue(x)`, wider tolerance, removed `expect` | mutation testing, tripwire |
| G6 | Swallow errors | catch-all returning default | mutation (removed throw), error-path properties, lint bans |
| G7 | Mock the subject | test mocks the very function under test | review rubric, mutation, integration-level properties |
| G8 | Tautological tests | assert implementation equals itself, blind snapshot updates | independent oracle, mutation |
| G9 | Assertion-free tests | test calls code, asserts nothing | mutation, "tests must assert" lint |
| G10 | Lint/type suppression | `@ts-ignore`, `# type: ignore`, `// ignore:`, `eslint-disable` | suppression ratchet, tripwire |
| G11 | Lower the thresholds | coverage floor lowered, ratchet baseline edited | protected paths |
| G12 | Edit CI/gate config | job removed, step made non-blocking | protected paths, trusted-checkout scripts |
| G13 | Env special-casing | `if CI:` branches, test-only flags in prod code | banned-API/pattern checks, review |
| G14 | Sleep/retry masking | `sleep(2)`, retry-until-green around flaky logic | flake policy, deterministic seams |
| G15 | Seed fixing to hide flakes | fixed seed where randomness should vary | seed logging, nightly random seeds |
| G16 | Delete the feature or code path | failing behavior removed | spec traceability, feature-presence invariants |
| G17 | Change the spec to match code | INV edited or retired | protected spec, spec-change PR class |
| G18 | Widen types | `any`, `dynamic`, `Object` | strict analysis, suppression ratchet |
| G19 | Pad metrics | dead code or trivial tests to raise coverage | mutation score, dead-code detection, LOC ratchet |
| G20 | Add a dependency that does the job | vendored/third-party shortcut | dependency budget with written reasons |

## Design tenets
1. **The referee is out of the author's reach.** Gate definitions, specs, baselines, CI config and red-team
   corpus are protected paths; authoritative runs happen server-side from trusted definitions.
2. **Measure test strength, not test existence.** Coverage says code ran; mutation testing says tests
   notice when code is wrong.
3. **Specify with properties, models and oracles.** Examples can be memorized, invariants cannot.
4. **Control nondeterminism.** Inject clock, randomness, storage and network so every failure replays from a seed.
5. **Ratchets.** Metrics that may only improve; loosening requires a protected-path PR.
6. **Constrain the solution space structurally.** Make the bad path impossible (layering rules, banned APIs,
   strict types), not merely discouraged.
7. **Evidence contract.** Every PR declares its class and ships class-specific, machine-checked proof.
8. **Test the gates.** A red-team corpus of known-bad changes must be rejected 100%; escapes become new cases.

## Robust vs antifragile
A fixed gate set makes a codebase robust. It becomes antifragile only through a closed loop:

    escape -> root cause -> permanent gate

Every bug that reaches users, every bad patch that slipped through, every near miss becomes an invariant,
a regression test verified to fail on the old code, a banned pattern, or a red-team case. Log each one in
the escapes table in GATES.md. Over time the gate set becomes a compressed memory of everything that has
gone wrong here, which a freshly prompted agent cannot reproduce.

## How much rigor, where
Extreme assurance is expensive. SQLite, often cited as the existence proof, reports test code several
hundred times the size of the library, 100% branch and MC/DC coverage on its core, and dedicated
out-of-memory, I/O-error and crash testing. Spend that level only where errors are catastrophic (data
integrity, money, security, scheduling/ordering correctness). Use risk tiers (assess-and-plan.md) and be
frugal elsewhere. "A million tests" is not the goal; strength per test is.

## Honest limits (state these to the user)
- Gates verify conformance to the spec, not the truth of the spec. A wrong invariant is enforced perfectly.
  The spec is the human bottleneck and deserves the most careful review in the repo.
- Goodhart applies to gates: agents will optimize mutation score or benchmarks directly. Use several
  independent signals, diff-scoped checks, a growing red-team corpus, and human review of gate changes.
- Equivalent mutants and false positives exist; provide an audited suppression path or people will fight the tool.
- Over-constraint stalls legitimate work. Start ratchets at today's values and tighten gradually.
- Taste (UX feel, naming, product judgement) is not fully gateable; keep a human in that loop.
- An adversary with environment access wins; gates must run hermetically on a server the author cannot edit.
- Retrofitting determinism and purity is far costlier than building them in; small scope makes everything cheaper.
- Gate maintenance is real engineering work. Budget for it, and delete gates that no longer pay for themselves.
