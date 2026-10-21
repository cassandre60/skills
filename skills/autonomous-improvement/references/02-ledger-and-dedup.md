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
  always `-`. No test exists → `<source9>:-`.
- **Status** is an audit (`COMPLETED` — gaps fixed or documented; `SOUND` — no
  gaps) or a negative disposition (`REJECTED` — owner closed the PR unmerged;
  `WONTFIX` — deliberately out of scope; `SUPERSEDED` — replaced by the PR named
  in the PR column). `EXAMPLE` is ignored by the parser.
- **Target** is repo-root-relative and stored in backticks.

Legacy rows with a single hash (no `:`) are still accepted. They match on the
source half only and are flagged `legacy` in `--json` output; re-audit them when
convenient. New rows always use the composite form.

## Authoritative row: newest wins

`record` inserts new rows directly below the table header (newest first). For
each target, the authoritative row is the greatest `(Date, -position)`:
the newest date wins; on a tie the topmost row wins. Rows appended by hand at
the bottom with a newer date also win. Only `COMPLETED` and `SOUND` count as
audited; dispositions are tracked in their own map under the same rule.

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
deduplicate parallel work. Instead, **open a draft PR early** (an empty or WIP
commit is enough). The file-overlap check above then protects the target. This is
why the in-progress status was removed from the schema.

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
