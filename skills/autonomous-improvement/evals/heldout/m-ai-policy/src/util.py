"""Slugify headings into URL-safe fragments."""

import re

def slugify(text):
    slug = text.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")
