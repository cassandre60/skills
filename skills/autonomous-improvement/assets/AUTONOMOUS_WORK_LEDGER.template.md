# Autonomous Work Ledger

> One row per audit. New rows go directly below the header. The authoritative
> row for a target is the newest by **Date** (topmost wins on a tie). Rows are
> append-only: never edit an old row, supersede it with a new one.
>
> `Content Hash` is `<source9>:<companion9>` from git blob hashes at HEAD. For
> Stream A, source = the audited file, companion = its mapped test. For Stream B,
> companion is `-`. A single hash (no `:`) is a legacy row and matches on source
> only.
>
> Only `COMPLETED` and `SOUND` count as audited. `EXAMPLE` rows are ignored.

| Date | Stream | Target File / Module | Content Hash | Status | PR / Issue | Summary / Outcome | Re-Audit Trigger |
|---|---|---|---|---|---|---|---|
| 2026-01-01 | A (Mutation) | `src/example/service.ts` | `abc123def:def456abc` | EXAMPLE | — | Delete this row; it is ignored by the parser | — |

## Target Candidates (optional index)

Leave this section out unless the project wants a human-readable index. The
`next` command derives candidates from `config.json` globs via `git ls-files`;
a second list here is not read by any script and tends to become a second
backlog.
