# m-rust-drift

A small listing CLI. It parses its options, prints them, and would apply them
to the listing it produces.

Options:

- `--sort` — sort the output before printing it.
- `--limit <N>` — cap the output at `N` rows.
- `--verbose` — print extra progress detail while running.

Build and run:

    cargo run -- --sort --limit 5
