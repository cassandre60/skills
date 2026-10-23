# Issue 42: median is wrong for even-length input

`src/calculator.py`'s `median` returns the upper middle element instead of
the average of the two middle elements whenever the list has an even count.

## Expected

 median([1, 2, 3, 4]) == 2.5

## Actual

 median([1, 2, 3, 4]) == 3

## Repro hint

Any even-length list; the upper-middle index ``n // 2`` is returned without
averaging with index ``n // 2 - 1``.
