"""Small integer helpers."""

def add(a, b):
    return a + b

def clamp(v, lo, hi):
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v
