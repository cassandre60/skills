"""Baseline implementation: correct but quadratic (Stream G fixture)."""


def dedup(xs):
    """First-occurrence-order dedup via a quadratic membership scan."""
    seen = []
    for x in xs:
        if x not in seen:
            seen.append(x)
    return seen
