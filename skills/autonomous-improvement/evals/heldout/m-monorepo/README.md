# m-monorepo

Two packages.

- `packages/pycalc` (Python): from `packages/pycalc` run
  `python3 -m unittest discover -s tests -t .`
- `packages/tsutils` (TypeScript): from `packages/tsutils` run
  `node --test test/`

Both use their toolchain's built-in runner; there is nothing to install.
