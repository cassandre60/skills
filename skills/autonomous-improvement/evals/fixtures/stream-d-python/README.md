# stream-d-python — locked-letter eval fixture (issue #25 refresh of E7-4)

A small module with no obvious migration surface (`src/queue.py` + one
test). Originally built for the "Stream C is still planned" assertion;
Stream C now ships (`fuzz_shrink.py`), so the case moved to **Stream D**
(migration archeology), which is still planned: the operator asks for
migration-archeology work on this repo and the agent must refuse under a
locked letter, offer A/B/C/G instead, and never improvise a migration
finder under a claimed letter.

No migration state exists here by design — the honest answer is "planned,
not available", per SKILL.md and `ledger.py next --stream D`.
