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

## Statuses

| State | Meaning | Counted in kill rate |
|---|---|---|
| `KILLED` | Targeted tests failed under the mutant. | yes |
| `SURVIVED` | Tests passed; candidate gap, triage required. | yes |
| `UNCONFIRMED` | Passed once, then the clean suite flaked or the mutant stopped surviving on re-application. | no |
| `UNCOMPILABLE` | An anchored compiler-error pattern matched. | no |
| `TIMEOUT` | Test command exceeded the per-mutant timeout. | configurable |

Timeouts are **baseline-relative** by default: per-mutant timeout =
`max(--timeout-factor (5) × baseline, --timeout-floor (20s))`, because a fixed
timeout on a loaded box manufactures kill rate; `--timeout <s>` still forces a
fixed value. `--timeout-counts-as {killed,survived,excluded}` (default
`killed`) controls how timeouts count. A missing test executable aborts the
run as an environment failure; it is not a kill. A failing baseline aborts
before any mutant is applied.

A survivor is only reported after **confirmation** (`--confirm-survivors 1`
by default): the clean suite must stay green across extra runs and the mutant
must still pass when re-applied, otherwise it lands in `UNCONFIRMED` — one
pass on a flaky suite is a coin flip, not a gap. Verdicts are cached in
`.autonomous/mutant-cache.json`, keyed by the exact source bytes + test
command, so unchanged pairs reuse verdicts (`--no-cache` bypasses; only
killed and confirmed-survived verdicts are stored).

## Backends

| Backend | How | Notes |
|---|---|---|
| `sregex` | built-in rule packs | guaranteed available, zero-dependency |
| `stryker` | `--backend stryker` or config `mutation_backend: stryker` + `mutation_backends.stryker.{cmd,report}` | normalizes the Stryker mutation-report JSON (mutated lines reconstructed from locations) into the same schema |
| `auto` (default) | configured external when its binary is found | otherwise falls back to `sregex` with a printed note |

`--backend-cmd`/`--backend-report` override config for one run. mutmut /
cargo-mutants / gremlins / PIT adapters are not wired yet; their reports need
the same normalization treatment before they can feed triage honestly.

Compile classification is anchored per language (for example
`^\S+\.dart:\d+:\d+: Error: ` and `^\s*(SyntaxError|IndentationError|TabError):`),
so ordinary assertion failures that contain words like "error" are not
misclassified. It is still a heuristic; counts are reported separately.

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

`--output-issue` is **deprecated**: it wrote one survivor to a local markdown
file nothing consumed. File survivors as real, deduped GitHub issues with
`fleet.py publish --issues-from <engine.json>` instead (or ingest them into
the mutant ledger with `mutants.py ingest`).
