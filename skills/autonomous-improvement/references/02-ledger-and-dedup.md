# Ledger and Deduplication

## Schema

Eight columns, in this order:

```markdown
| Date | Stream | Target File / Module | Content Hash | Status | PR / Issue | Summary / Outcome | Re-Audit Trigger |
|---|---|---|---|---|---|---|---|
```

- **Content Hash** is `<source9>:<companion9>`, using git blob hashes truncated
  to 9 characters. For Stream A the source is the target and the companion is
  its mapped test. For Stream B the target is the test file and the companion is
  always `-`. No test exists → `<source9>:-`. **One hash per row** — a cell with
  several hashes is not a composite, it is unparsable.

  **The hash is the blob as it lands in `main`, not the blob that was
  audited.** A round whose PR changes the source records the *post-change*
  blob; a round that only adds tests records the unchanged blob, because the
  source did not change. Same for the companion half: a PR that edits the
  mapped test records the test's post-change blob. A pre-change hash is not
  "more conservative" — it is *wrong*: the dedup law compares the recorded
  hash against the file at HEAD, finds a difference, and re-audits a file that
  was just audited and fixed.
- **Status** is an audit (`COMPLETED` — gaps fixed or documented; `SOUND` — no
  gaps) or a negative disposition (`REJECTED` — owner closed the PR unmerged;
  `WONTFIX` — deliberately out of scope; `SUPERSEDED` — replaced by the PR named
  in the PR column). `EXAMPLE` is ignored by the parser.

  **The cell must hold the bare token and nothing else.** The parser compares
  the whole cell against the vocabulary above, so `COMPLETED (second round: 98
  candidates)` is not a `COMPLETED` row — it is **skipped, silently**. Put the
  detail in the Summary cell. A ledger whose rows are invisible still *looks*
  like a full registry, and the dedup law will re-audit every file in it.
- **Target** is repo-root-relative and stored in backticks. **One target per
  row**: the target cell is the lookup key, so a cell naming two paths
  (`lib/a.dart`, `test/a_test.dart`) protects neither — the row exists and can
  never match. Two files means two rows.

Legacy rows with a single hash (no `:`) are still accepted. They match on the
source half only and are flagged `legacy` in `--json` output; re-audit them when
convenient. New rows always use the composite form.

## Authoritative row: newest wins

`record` inserts new rows directly below the table header (newest first). For
each target, the authoritative row is the greatest `(Date, -position)`:
the newest date wins; on a tie the topmost row wins. Rows appended by hand at
the bottom with a newer date also win. Only `COMPLETED` and `SOUND` count as
audited; dispositions are tracked in their own map under the same rule.

**Insertion order is load-bearing.** Because a date tie is broken by position,
a batch of rows for the same target must be inserted **newest first**: a later
PR's row above an earlier PR's row. Inserted the other way round, the earlier
row becomes authoritative — and if it records a pre-change blob, the law
re-opens a file that was just fixed.

This fixes the V1 bug where the parser kept the oldest row and re-proposed every
re-audited file forever.

## Negative dispositions: the memory of no

An audit says "this is done". A disposition says "stop asking". Both are keyed
to the composite hash, so both go stale the same way:

- `next` skips a target whose newest disposition matches the current composite
  hash. The skip names the status, the PR, and the one-line reason, so a human
  can audit the refusal.
- Any content change (source or mapped test) invalidates the disposition and
  re-opens the target. A rejection is "not this diff", not "never this file".
- An audit at the current hash outranks a disposition: if work landed after the
  PR was closed, the audit wins and the target stays audited.
- `sync-rejections` scans `gh pr list --state closed`, takes PRs with
  `mergedAt: null`, and writes a `REJECTED` row (`#n`, PR title) for every file
  that is a configured candidate and still exists at HEAD. It is idempotent at
  the same hash and skips files whose current hash is already audited.

This is the difference between a colleague and a spammer: the fleet must not
re-propose work the owner already declined. `--dry-run` previews the rows;
`--json` reports them.

## Per-mutant dispositions

The work ledger is per file; the mutant ledger is per survivor. It is a second
git-tracked, append-only table, `docs/AUTONOMOUS_MUTANTS.md` by default
(`mutant_ledger_path`):

```markdown
| Date | Mutant ID | Source Hash | Disposition | PR / Issue | Reason |
|---|---|---|---|---|---|
```

- **Mutant ID** is `file:line:sha256(mutated text)[:12]`, computed by
  `mutants.py` from the mutation engine's `--output-json` report. Line numbers
  are part of the id: an edit above the mutant makes it a new id.
- **Dispositions** are `OPEN` (active), or terminal `KILLED-BY`, `EQUIVALENT`,
  `NOISE`, `WONTFIX`. Newest row per id wins, exactly like the work ledger.
- An `OPEN` row counts only while **Source Hash** matches the file's blob hash
  at HEAD, so a content change drops stale survivors and a fresh run
  repopulates them. Terminal rows are remembered forever for that exact id:
  the same equivalent mutant is never re-triaged after an unrelated change.
- `next` is file-level, so an audit row alone would hide the file. When a file
  is audited **and** has open survivors at its current hash, `next` returns it
  as a candidate with `"audit_state": "partial"` and `"open_mutants": N`
  (human output says "partially audited") until each survivor is killed or
  triaged. `check` prints the same warning.

```bash
python3 scripts/autonomous/mutation_engine.py --source src/app.py \
  --test-cmd "pytest -xq tests/test_app.py" --output-json run.json
python3 scripts/autonomous/mutants.py ingest --report run.json --json
python3 scripts/autonomous/mutants.py open --json
python3 scripts/autonomous/mutants.py record --id src/app.py:12:ab12cd34ef56 \
  --disposition EQUIVALENT --reason 'mutation does not change observable behavior'
python3 scripts/autonomous/mutants.py record --id src/app.py:20:0123456789ab \
  --disposition KILLED-BY --pr '#42' --reason 'new assertion on the boundary'
python3 scripts/autonomous/mutants.py check --id src/app.py:12:ab12cd34ef56
```

`ingest` is idempotent at the same hash and skips ids with a terminal row.
`open --source PATH` narrows to one file. A missing mutant ledger means "no
mutant-level memory" and is not an error, so adopting it is opt-in.

## Composite hash

A Stream A audit depends on the source **and** the test that proves it. If the
test is weakened or deleted, the old audit is stale. `record` computes the
mapped test automatically (config `test_for_source`, then conservative path
heuristics) and accepts `--test PATH` to override it. `next` and `check`
consider a target audited only when both halves match.

Hash semantics: **the blob at HEAD**. If the working tree differs,
`record` warns; `--strict` makes that fatal. Do not record a hash that does not
describe the audited bytes.

## Cell escaping and separators

Summaries may contain `|`, `\`, and newlines (mutation descriptions often do).
Cells are written with `|` → `\|`, `\` → `\\`, and CR/LF collapsed to spaces.
Rows must be parsed by splitting only on unescaped pipes. The round-trip is
tested.

The separator row may be rewritten by a formatter. `record` accepts compact,
spaced, and colon-aligned separators (`|---|`, `| --- |`, `| :--- | ---: |`)
and preserves the file's existing line endings (LF or CRLF).

## CLI

```bash
python3 scripts/autonomous/ledger.py next --stream A
python3 scripts/autonomous/ledger.py next --stream B --limit 5 --json
python3 scripts/autonomous/ledger.py record --stream A \
  --target src/app.py --status COMPLETED --pr '#42' \
  --summary '6 mutants: 2 killed, 4 triaged, 1 gap fixed'
python3 scripts/autonomous/ledger.py record --stream A \
  --target src/app.py --status REJECTED --pr '#51' \
  --summary 'owner closed: out of scope'
python3 scripts/autonomous/ledger.py sync-rejections --dry-run
python3 scripts/autonomous/ledger.py check --target src/app.py
python3 scripts/autonomous/ledger.py validate            # rows the parser cannot see
python3 scripts/autonomous/ledger.py validate --json
```

- Stream ids are `A`/`B`; `1`/`2` are aliases. `C`/`D` exit 2 with
  "planned, not implemented". No input ever exits 0 silently.
- Candidates come from `candidates.A` / `candidates.B` globs via
  `git ls-files`, ordered by `priority_paths` then lexicographically. Files that
  do not exist at HEAD are skipped. There is no hardcoded target list.
- Stream A candidates require a mapped existing test when
  `require_test: true` (the default). Stream B candidates are test files.
- `--pr` is optional for `SOUND` and required for `COMPLETED` by default
  (`pr_required` in config). Dispositions never require `--pr`, but the PR that
  produced the "no" belongs in the column.
- `record` refuses a target that does not exist at HEAD, and refuses a duplicate
  authoritative row with the same composite hash unless `--force`.
- `check` exits 0 when the current composite hash is audited, 1 otherwise. When a
  disposition matches the current hash it says so on the next line, so a "no" is
  never mistaken for "never looked at".
- **Risk-ranked discovery** (`next --rank risk`, default `lexicographic`
  unchanged): eligible candidates are scored from git in one pass — commits
  touching the file and lines changed over the last `--rank-window` commits
  (commit-count window, so output is deterministic for a fixed repo state),
  a word-match reference count as a fan-in proxy, LOC/branch-keyword size
  proxies, and an uncovered-lines factor when config names `coverage_report`
  (coverage.py JSON or lcov; absent → neutral). A candidate whose path is
  named in an open issue enters the **intake lane** and outranks any score
  in its tier. `priority_paths` remains a hard tier above the score — owner
  intent beats heuristics. `--json` attaches `rank: {score, why: [...]}` to
  every candidate so the Orchestrator can justify the pick without
  re-deriving it. The score is a documented heuristic, not a measurement.
- `validate` reports what the parser **cannot see** — rows whose Status cell is
  not a bare token, rows naming several targets or several hashes — and, per
  target, whether the authoritative row's composite hash still matches HEAD.
  It exits 1 when it finds any of those, and prints the visible-row count next
  to the row count so the two can never be confused. It changes nothing.

## Open-PR collision check

`next` makes one `gh pr list --state open --json number,title,headRefName,files`
call (and falls back to branch fields if that `gh` cannot expose `files`).

1. **File overlap** is primary: a target is claimed if its path appears in an
   open PR's file list. Exact paths only; no substring matching.
2. **Branch segments** are the fallback: normalize `_` to `-`, lowercase, and
   match a whole branch segment (for example `test/chess-fsrs-scheduler`
   claims `chess_fsrs_scheduler.dart`, but not `scheduler.dart`). Titles and
   bodies are never searched.
3. The skip message names the claiming PR, e.g. `open PR #123 test/foo`, so a
   human can check it.

If `gh` is missing or the repo has no remote, `next` prints one warning and
continues. `--require-gh` makes it fatal.

## Claiming work without a third status

The ledger is a git-tracked file: a row written in one agent's worktree is
invisible to other agents until merged, so an in-progress row cannot
deduplicate parallel work. Instead, **claim with a draft PR at minute 1**:
`fleet.py claim --target <path> --stream A` refuses targets already in an
open PR's files, creates the registered worktree, pushes a WIP commit, and
opens the draft with a stub naming target + composite hash. The file-overlap
check above then protects the target. This is why the in-progress status was
removed from the schema. When the work is done, `fleet.py publish --pr <n>`
renders the final body from `.autonomous/evidence.json` and flips the draft
ready — and refuses, leaving it draft, when the artifact is incomplete or
stale. Breaker output rides the same transport: `fleet.py publish
--issues-from <engine.json>` files one deduped issue per surviving mutant.

## Parallel appends: merge=union

Two agents appending below the header conflict by construction. Add both ledgers:

```
docs/AUTONOMOUS_WORK_LEDGER.md merge=union
docs/AUTONOMOUS_MUTANTS.md merge=union
```

from `assets/gitattributes.fragment.txt`. Union merge keeps both sides' inserted
lines; a scripted two-branch merge is in the test suite. Caveats:

- Rows must stay **append-only**. Never edit an existing row; supersede it with
  a new row. Newest-wins makes that safe.
- If the ledger is also a protected path, apply the protected-paths and
  gitattributes fragments together. Expect the protected-path gate to red on
  ledger PRs; that is the designed approval friction, not a reason to drop the
  row.
- Union merge does not resolve edits to the same existing line; it only keeps
  both additions. Do not rewrite the table.

## Paths and cwd

The repo root comes from `git rev-parse --show-toplevel`. All target paths are
repo-root-relative and all git commands run with that cwd. The ledger path comes
from config `ledger_path` (or `--ledger`), resolved against the repo root — never
from `__file__`. Running from a subdirectory produces the same result.

## Migration from commit-hash ledgers

Old rows that stored `git log` hashes remain "last audited commit" markers. New
rows use content hashes. Re-audit is triggered by content change, so re-audit or
supersede old rows as you touch them; never rewrite history in place.
