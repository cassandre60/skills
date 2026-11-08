# State Machine Backlog

> One row per finding from the implicit-state-machines lens. The map pass
> appends new rows; a fix pass rewrites only the **Status** cell. Rows are
> append-only and dedup by **Id**; if a finding changes, add a new id and mark
> the old row `SUPERSEDED` with the reason.
>
> The lens is complete only when no row is `OPEN`. `EXAMPLE` rows are ignored.
>
> `Severity` is `high`, `medium`, or `low`. Later passes take the
> highest-severity `OPEN` row (topmost on a tie).
>
> Escape `|` inside a cell as `\|` so the table renders; the status cell is a
> bare token.

| Id | Machine | Severity | Encoded At | Finding | Trigger | Failing Test | Status |
|---|---|---|---|---|---|---|---|
| example-1 | example | high | `src/example/service.ts:42` | `isSaving` and `isEditing` can both be true | save while editing | `tests/example/service.test.ts::rejects_save_while_editing` | EXAMPLE |

## Status vocabulary

- `OPEN` — proven, not fixed.
- `FIXED #<pr>` — fixed by that PR; the failing test is now green.
- `WONTFIX <reason>` — accepted as intentional; the reason is the audit trail.
- `SUPERSEDED <reason>` — a newer row replaces this finding.
