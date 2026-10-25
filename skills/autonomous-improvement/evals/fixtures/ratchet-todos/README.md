# ratchet-todos

Phase 7 fixture: a python module whose measurable proxy is its TODO count
(grep -c TODO src/app.py). The ratchet stream's oracle accepts only diffs
that strictly reduce the count while the suite stays green and the diff
stays inside src/.

## Tests

    python3 -m unittest discover -s tests -t .
