# Streaming config loader

The example below is the canonical way to load the sampler's config in this
repo. It appears in the onboarding and in every worker template, so a stale
name here poisons every future turn that reads it.

```python  # docs-truth: synced with src/config.py
from src.config import load_table

config = load_table("settings.json")
print(config)
```
