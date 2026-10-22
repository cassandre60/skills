# Mutation Engine

Regex-based, per-language rule packs. No AST — every survivor needs human
triage. This is the zero-dependency fallback, not a replacement for Stryker,
PIT, mutmut, or cargo-mutants.

## Rule packs

`scripts/autonomous/mutation_rules/<language>.json`. A pack is selected by the
source file extension, or forced with `--lang`. Packs ship for Python, Dart,
TypeScript, JavaScript, and Go.

```json
{
  "name": "python",
  "extensions": [".py"],
  "line_comment": "#",
  "block_comment": null,
  "string_quotes": ["'", "\""],
  "triple_quotes": true,
  "skip_line_prefixes": ["import ", "from "],
  "rules": [
    {"pattern": "...", "replacement": "...", "description": "..."}
  ],
  "compile_failure_patterns": ["^\\s*(SyntaxError|IndentationError|TabError):"]
}
```

Rules are applied per line, in order, to positions that a small lexer marks as
code (not string/comment). Multi-line strings and block comments are tracked
across lines. This is heuristic; verify any survivor that looks suspicious.

- `context_skip_prefix` on a rule skips a match when the text before it matches
  the regex (used to avoid mutating `for x in y`).
- Duplicate mutants are removed by `(line, mutated_text)`, so a generic `==`
  rule and a specific `== null` rule produce one mutant, not two.
- Relational rules require whitespace on both sides, so generics such as
  `List<int>` are not mutated.
- Unknown extensions exit 2 and list the supported ones. There is no silent
  "0 mutants, success".

## Backends

The regex engine ("sregex") stays the guaranteed-available backend:
zero-dependency, syntactic, honest that survivors need human triage. It is the
fallback, not a replacement for Stryker/PIT/mutmut/cargo-mutants/gremlins.

`--backend {auto,sregex}` (default `auto`): with no external-mutator adapter
built, `auto` resolves to `sregex` and prints that resolution, so a run's
backend is never implicit or silently renamed.

| Backend | Status |
|---|---|
| `sregex` | available (this engine) |
| `stryker` (JS/TS) | planned — needs a recorded report fixture |
| `mutmut` (Python) | planned — needs a recorded report fixture |
| `cargo-mutants` (Rust) | planned — needs a recorded report fixture |
| `gremlins` (Go) | planned — needs a recorded report fixture |
| `pit` (Java) | planned — needs a recorded report fixture |

An adapter normalizes its tool's report into the verdict schema above so
triage/ledger/evidence flows are unchanged. Its normalization is tested against
a **recorded** report of that tool before the backend table above changes; no
adapter ships without a fixture.

## Statuses

| State | Meaning | Counted in kill rate |
|---|---|---|
| `KILLED` | Targeted tests failed under the mutant. | yes |
| `SURVIVED` | Tests passed **and** the clean-source confirmation run held; candidate gap, triage required. | yes |
| `UNCOMPILABLE` | An anchored compiler-error pattern matched. | no |
| `TIMEOUT` | Test command exceeded the effective timeout. | configurable |
| `UNVERIFIED` | A survivor whose clean-source confirmation run failed: the suite is unreliable on clean source, so the pass proves nothing. Not work. | no |

`--timeout-counts-as {killed,survived,excluded}` (default `killed`) controls
timeouts. A missing test executable aborts the run as an environment failure; it
is not a kill. A failing baseline aborts before any mutant is applied.

Compile classification is anchored per language (for example
`^\S+\.dart:\d+:\d+: Error: ` and `^\s*(SyntaxError|IndentationError|TabError):`),
so ordinary assertion failures that contain words like "error" are not
misclassified. It is still a heuristic; counts are reported separately.

## Timeouts

The per-mutant timeout is baseline-relative by default: `max(5 x baseline
elapsed, 20s)` (#16). `--timeout`, when given explicitly, wins (flags are
preserved). A fixed timeout is a loaded-VM trap: TIMEOUT counts as KILLED by
default, so a contended box manufactures kill rate. Scaling off the measured
baseline keeps the classification honest regardless of machine load.
`--output-json` records both `timeout_mode` and `timeout_effective`.

## Survivor confirmation and verdict cache

`--confirm-survivors N` (default 1; `0` disables): before a pass under the
mutant is reported as `SURVIVED`, the same targeted command is re-run on
restored (clean) source. A failing or timing-out confirmation demotes the
mutant to `UNVERIFIED` and it never enters triage — a suite that cannot be
trusted on clean source grades mutants by coin flip (#16 item 3). Quarantining
a flaky suite is Stream F's job, not the engine's.

Verdicts are cached per (source bytes, test command) with a mutant id
(`line:sha12(mutated text)`) in `.autonomous/mutant-cache.json`:

- a second identical run reuses every verdict (`cache_hits` in the JSON);
- changing the source or the test command invalidates the cache by
  construction (the stored composite no longer matches);
- survived entries are stored only **after** confirmation, so a cache hit can
  never smuggle an unconfirmed pass into work;
- `--no-cache` bypasses the file; an unreadable cache is ignored with a
  printed warning, never trusted.

Add `.autonomous/mutant-cache.json` to `.gitignore`
(`assets/gitignore.fragment.txt`).

## Safety and crash recovery

Before mutation the engine:

1. Refuses a dirty source unless `--allow-dirty`.
2. Restores any stale `<file>.mutbak` left by a previous run (if its
   `<file>.mutlock` pid is dead). A live lock aborts with exit 3.
3. Writes a backup and pid lock, installs SIGTERM/SIGINT/SIGHUP handlers, and
   runs each test in its own process group so a killed run does not orphan test
   runners.

On SIGTERM the source is restored immediately. After SIGKILL the file stays
mutated with its sidecars; the next run restores it and says so. Add
`*.mutbak` and `*.mutlock` to `.gitignore` (`assets/gitignore.fragment.txt`).
`verify-gate` fails if either sidecar is present.

Sharding partitions the deduplicated mutant list **before** sampling:
`--shard 2/4` selects every fourth mutant, then `--max-mutants` samples within
that shard. Shards must run in **separate worktrees/checkouts**; the pid lock
makes accidental same-file concurrency fail loudly.

## Sampling and reproducibility

After deduplication, `--max-mutants N` takes a seeded random sample
(`--seed`, default 0) and sorts it by line, instead of taking the first N.
`--spread` takes at most one mutant per line first. The seed, shard, and counts
are written to `--output-json`, so a run can be reproduced.

## Scoping

- `--diff-base origin/main` mutates only lines added/changed in
  `git diff -U0 origin/main...HEAD -- <source>`.
- `--lines 10-20,42` limits mutation to explicit line ranges.
- `--source` must be inside the repository for `--diff-base`.

Scope is a cost control, not a claim that only changed lines matter. A
diff-scoped run can miss gaps elsewhere; the ledger records which scope was
audited.

## What the rule packs cannot reach

The engine mutates **text**, so a class of changes has no text to mutate. These
shapes are invisible to it, and a round that only runs the engine will report
a clean kill rate while a third of the real gaps sit untouched:

| Shape | Example |
|---|---|
| a plain field copy | `this.a = other.a` → `this.a = other.b` |
| a returned expression | `return a + b` → `return a + c` |
| a dropped argument | `f(a, b)` → `f(a)` |
| a swapped branch | `if (x) { A } else { B }` → the arms exchanged |
| a `??` fallback | `x ?? y` → `x ?? z`, or the fallback removed |
| a changed literal | `size: 150` → `size: 100` |
| a different separator | `join(", ")` → `join("; ")` |
| a dropped `&&` operand | `a && b` → `a` |
| a changed unit or key | a percentage written as a fraction |
| argument order | `f(a, b)` → `f(b, a)` |

**Budget a hand-curated supplement for them.** The supplement is a first-class
part of a round, judged by the same kill criterion: apply one mutant at a
time, run the targeted test, record the red run, restore, record the green
rerun. Twenty to thirty hand-applied mutants per round is a normal budget, and
on a live fleet they closed roughly **a third** of the pinned gaps. A round
that reports only the engine's kill rate has not measured the file.

## Two flags that change the number silently

- **`--timeout-counts-as`** defaults to `killed`. A mutant that hangs the suite
  is then counted as a kill, and a kill rate that includes hangs is not a
  measurement. Pass `--timeout-counts-as excluded` and report hangs as their
  own bucket — a hang is a behaviour worth knowing about, not a kill.
- **`--max-mutants`** **samples** (seeded, default 30) after deduplication.
  A run that samples reports a candidate count that is not the file's. Pass a
  value above the candidate count, or state the sample and the seed.

Both are visible in the run's `--output-json` (seed, shard, counts) — read it
before quoting a number.

## Triage a survivor

1. **Equivalent?** Does the mutation change observable behavior? If not, close
   it and record nothing.
2. **String/comment noise?** Check the line. The lexer catches most of it, not
   all of it.
3. **Already covered elsewhere?** Run the other test file with the mutant
   applied before claiming a gap.
4. **Worth a test?** Some mutations (`.last` → `.first` on an unordered
   collection) encode no invariant worth pinning.

For one real gap: write the smallest test that is **red with the mutant applied**
and **green on clean source**, cite the invariant if the repo has one, and keep
the PR to one test file plus at most one fixture.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Run completed (survivors are a result, not a crash) |
| 1 | Negative result requested: `--fail-on-survivors` and survivors exist |
| 2 | Usage error or unsupported input (unknown language/extension, bad flags) |
| 3 | Environment failure (missing tool, failing baseline, live lock) |

V1 used exit 2 for a failing baseline; V2 uses 3. `--fail-on-survivors` restores
the old gate behavior.

## Promotion to a gate

A regex engine should not block PRs until it has two green runs with zero false
positives on that repo and a triage log. Prefer an AST-based mutator for a
blocking gate. If this engine is promoted, run it diff-scoped and record the seed
and shard in CI logs.
