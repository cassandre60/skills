# Autonomous Mutant Ledger

> One row per surviving mutant. New rows go directly below the header. The
> authoritative row for a mutant id is the newest by **Date** (topmost wins on a
> tie). Rows are append-only: never edit an old row, supersede it with a new one.
>
> A mutant id is `file:line:hash(mutated text, sha256, first 12 hex chars)`.
> `OPEN` is active state and only counts while **Source Hash** still matches the
> file's blob hash at HEAD. `KILLED-BY`, `EQUIVALENT`, `NOISE`, and `WONTFIX` are
> terminal: that exact mutant is never re-reported, even after the file changes.
>
> `EXAMPLE` rows are ignored. Write `—` when there is no PR.

| Date | Mutant ID | Source Hash | Disposition | PR / Issue | Reason |
|---|---|---|---|---|---|
| 2026-01-01 | `src/example/service.py:12:abc123def456` | `000000000` | EXAMPLE | — | Delete this row; it is ignored by the parser |
