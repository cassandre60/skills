"""The 'faster because it does less' trap: fast, but not equivalent."""


def dedup(xs):
    """Sorted-set dedup: drops the first-occurrence order contract."""
    return sorted(set(xs))
