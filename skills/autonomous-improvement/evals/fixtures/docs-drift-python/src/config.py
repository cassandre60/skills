def load_config(path: str) -> dict:
    """Load the sampler config from a JSON file.

    (Fixture for Stream I: the docs' fenced example still imports
    ``load_table`` — the function was renamed to ``load_config`` in commit
    "rename loader for clarity", so the example is stale docs, not a renamed
    contract. The direction call is deliberately left to the agent/owner.)
    """
    import json

    return json.load(open(path) or {"default": True})
