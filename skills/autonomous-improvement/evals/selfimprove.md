# Self-improvement protocol (Phase 5.5)

The loop that lets the skill improve itself without ever editing its own
referee. One iteration:

1. **Read** dev-split matrix records under `evals/results/` and run records
   under `.autonomous/run-records/`; select runs that scored below
   threshold, with their transcripts (local, never committed).
2. **Propose exactly one change**: the file, the diff, and the evidence that
   motivates it. `selfimprove.py propose` prints the candidates and the
   shape; a proposing agent (or the owner) writes the diff.
3. **Apply it in a worktree** of the skill repo: `selfimprove.py apply
   --diff <patch> --motivation "<evidence>"`. The tool REFUSES — mechanically,
   before anything is applied — any diff that touches protected paths: the
   five-laws table, the stream contract and tier sections of
   `references/08-recursive-self-improvement.md`, `evals/` (fixtures, cases,
   gates, heldout), `tests/pins.json` and `tests/pins.baseline.json`,
   `scripts/autonomous/test_weakening_check.py`, and the merge policy.
4. **Evaluate**: `selfimprove.py evaluate --worktree <path>` runs the
   deterministic eval layer (`evals/run.py --deterministic`) and the unit
   suite scoped to the worktree, then checks `evals/gates.json` against the
   recorded matrix results. The agent-run dev matrix spends owner tokens —
   it runs only with the owner's explicit go-ahead (see
   `.autonomous/owner-asks.md`); without matrix records the loop CANNOT
   verify an improvement and must revert.
5. **Keep or revert**: gates pass ⇒ keep and open a DRAFT PR with the
   evidence. Gates fail or unverifiable ⇒ `selfimprove.py revert --worktree
   <path> --why "<what was tried and why it lost>"`, which appends the
   negative-ledger line to `.autonomous/negative-ledger.md` (untracked).
6. **Never merge.** The improver proposes; the owner (or CI on the owner's
   behalf) decides. The held-out split is shown to the improver only as
   aggregate verdicts, never per-case.

Invariants (test-pinned): a diff touching a protected path is refused
before application; a non-improving change is reverted; the negative-ledger
line is written on every revert.
