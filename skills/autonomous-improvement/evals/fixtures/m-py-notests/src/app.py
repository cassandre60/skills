"""Parse compact range expressions like "1-4" and "10"."""

def parse_range(text):
    text = text.strip()
    if "-" in text:
        lo, hi = text.split("-", 1)
        return (int(lo), int(hi))
    value = int(text)
    return (value, value)
