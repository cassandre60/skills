def median(values):
    """Fixture bug (Stream E): wrong for even-length input.

    The seeded defect: ``sorted(values)[n // 2]`` returns the upper middle
    element. The correct even-length median is the average of the two middle
    elements. The docs example below still shows the buggy shape, so the
    bugreport in docs/bugreports/issue-42.md reproduces against this file.
    """
    return sorted(values)[len(values) // 2]
