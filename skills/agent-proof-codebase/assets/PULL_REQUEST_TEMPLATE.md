<!-- Fill every section. CI reads the Class line. One concern per PR. -->

**Class:** <!-- one of: bugfix | perf | refactor | feature | test-only | docs | dependency | gate-change | spec-change -->

## Problem
<!-- What is wrong or missing? Link the issue/invariant (INV-xxx). -->

## Root cause / rationale
<!-- Why does it happen / why this design? -->

## Evidence (required for the class)
- [ ] bugfix: regression test that FAILS on the base commit and passes here (`fail_to_pass_check.sh` output pasted below)
- [ ] perf: before/after benchmark output on the pinned harness + equivalence test vs the old implementation
- [ ] refactor: no test files modified; mutation score not lower; differential/equivalence evidence
- [ ] feature: SPEC.md invariants added or updated first; property tests for each
- [ ] test-only: names the mutant(s) or ratchet this kills
- [ ] gate-change / spec-change: explains what it stops catching (if anything) and why; human approval required

```
<paste gate output here>
```

## Risk and rollback
<!-- What could break? How do we undo it? -->

## Honest status
- Ran locally: <!-- list commands -->
- Not run / unverified: <!-- be specific -->
- Protected paths touched: <!-- none, or list and justify -->
