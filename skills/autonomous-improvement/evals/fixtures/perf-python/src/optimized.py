"""The Builder's real optimization: hash-based, order- and value-equal."""


def dedup(xs):
    """Same first-occurrence-order dedup, O(n) via dict key order."""
    return list(dict.fromkeys(xs))
