# Worked Examples

## Stream A, end to end

Repo: a Python project. `src/pricing.py` has:

```python
def discount(total, member):
    if member is not None and total > 100:
        return total * 0.9
    return total
```

Mapped test: `tests/test_pricing.py`, which checks the full-price path (including
the exact `100` boundary) but never a member over 100.

### 1. Pick the target

```console
$ python3 scripts/autonomous/ledger.py next --stream A
NEXT TARGET FOUND: src/pricing.py
Current source hash: 1a2b3c4d5
Mapped test:         tests/test_pricing.py
Composite hash:      1a2b3c4d5:9f8e7d6c5
Last audited hash:   NEVER
```

### 2. Run the engine

```console
$ python3 scripts/autonomous/mutation_engine.py \
    --source src/pricing.py \
    --test-cmd "python3 -m pytest -x -q tests/test_pricing.py" \
    --seed 0 --max-mutants 20
...
  [1/3] SURVIVED: line 2: if member is not None and total > 100:
        -> if member is None and total > 100: (...)
  [2/3] SURVIVED: line 2: ... and total > 100:
        -> ... or total > 100: (...)
  [3/3] KILLED: line 2: total > 100 -> total >= 100
```

(Exact mutants depend on the rule pack and seed.)

### 3. Triage

- `member is None` — real gap: a member with a total over 100 should get the
  discount; the current test never checks it.
- `and -> or` — real gap: a non-member over 100 would take the discount branch.
- `> -> >=` — killed by an existing test that checks the exact `100` boundary.

Pick **one** finding for this PR: the member path.

### 4. Write the red/green test

With the `member is None` mutant applied, this test fails:

```python
def test_member_over_threshold_gets_discount():
    assert discount(200, member="alice") == 180
```

On clean source it passes. Run it twice, once with the mutant and once without:

```console
$ python3 -m pytest tests/test_pricing.py -k member_over_threshold -q
# with mutant applied: 1 failed
# clean source:        1 passed
```

Record both commands and the base sha in the PR body. The test passes on
`origin/main`; the mutant was the defect.

### 5. Commit, gate, open the PR, then record

```console
$ git add tests/test_pricing.py
$ git commit -m "test(pricing): cover member discount branch"
$ python3 scripts/autonomous/orchestrator.py verify-gate --worktree .
$ git push -u origin test/pricing-member-discount
$ gh pr create --title "test(pricing): cover member discount branch" --body "..."
$ python3 scripts/autonomous/ledger.py record --stream A \
    --target src/pricing.py --status COMPLETED --pr "#123" \
    --summary "3 mutants, seed 0: 2 survivors; member branch gap fixed"
$ git add docs/AUTONOMOUS_WORK_LEDGER.md
$ git commit -m "docs: record pricing audit"
$ git push
```

The next `next --stream A` skips `src/pricing.py` while both its source and test
hashes are unchanged. If the test file is later weakened or deleted, the
composite hash changes and the file becomes eligible again.

## Stream B: a good finding and a bad one

### Good

> `tests/test_import.py:42` — `test_import_does_not_crash` calls
> `import_studies(path)` and asserts nothing. The importer could return zero
> records, or write the wrong rows, and this test would still pass. Evidence: the
> test body has no read-back; deleting the importer's write call leaves it green.
> Proposed assertion: import one fixture with 3 rows, then assert
> `store.count() == 3` and the first row's id.

Why it is good: names the behavior that is unprotected, the evidence is
mechanical, and the fix is a stronger assertion, not a style edit.

### Bad

> `tests/test_import.py` — rename `test_import_does_not_crash` to
> `test_import_persists_rows` for clarity.

Why it is bad: it changes no behavior. The test can still pass while the
importer writes nothing. It is cosmetic churn and should be rejected.
