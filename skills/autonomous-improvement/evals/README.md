# Behavioral evals

`evals.json` holds five behavior checks for an agent using this skill. They are
not run by `python3 -m unittest`; they need a fresh agent session so the test is
about judgment, not script output.

Each case names a fixture under `fixtures/`, a prompt, setup commands, expected
behavior, and forbidden behavior. Use `<skill>` for this folder and `<repo>` for
the temporary fixture copy. The setup commands are written for `bash`; run them
from inside `<repo>` unless the command says otherwise.

```sh
cp -r evals/fixtures/weak-python /tmp/weak-python
# substitute <skill> and <repo>, run the setup lines, then start a fresh agent
```

Grading is a checklist review of the transcript and `git diff`:

- Every `expected` item should be observable, not merely asserted by the agent.
- Any `forbidden` item is a failure even if the final diff looks small.
- The skill's own rule applies: a green test that also passes with the mutant
  applied is not evidence.

The Python fixtures use plain pytest-style test functions. Install `pytest` in
the eval environment or translate `test_cmd` to the runner you use; the point of
the eval is agent behavior, not the runner.
