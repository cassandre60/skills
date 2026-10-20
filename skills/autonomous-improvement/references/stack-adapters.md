# Stack Adapters

The scripts are language-neutral. Only `config.json` names commands. The
examples below are starting points; replace them with the project's real
commands from `AGENTS.md`, `Makefile`, `justfile`, or CI.

## Python

```json
{
  "setup_cmds": [],
  "format_check_cmd": ["ruff", "format", "--check", "."],
  "analyze_cmd": ["ruff", "check"],
  "analyze_extensions": [".py"],
  "test_cmd": ["python3", "-m", "pytest", "-x", "-q"],
  "test_for_source": [
    {"src": "^src/(?:.*/)?([^/]+)\\.py$", "test": "tests/test_\\1.py"}
  ],
  "candidates": {
    "A": {"include": ["src/**/*.py"], "exclude": ["**/__init__.py"], "require_test": true},
    "B": {"include": ["tests/**/*.py"], "exclude": []}
  }
}
```

- Targeted test: `python3 -m pytest tests/test_app.py -k test_name -q`.
- If the project uses `mypy`/`pyright`, put it in `analyze_cmd`; simplest is one
  analyzer per config. Add a second command to `gate_cmd` if needed.
- `setup_cmds` may need `["python3","-m","pip","install","-e",".[dev]"]`.

## Dart / Flutter

```json
{
  "setup_cmds": [
    ["fvm", "flutter", "pub", "get"],
    ["fvm", "dart", "run", "build_runner", "build", "--delete-conflicting-outputs"]
  ],
  "format_check_cmd": ["fvm", "dart", "format", "--output=none", "--set-exit-if-changed", "lib", "test"],
  "analyze_cmd": ["fvm", "flutter", "analyze"],
  "analyze_extensions": [".dart"],
  "test_cmd": ["fvm", "flutter", "test"],
  "test_for_source": [{"src": "^lib/(.*)\\.dart$", "test": "test/\\1_test.dart"}],
  "candidates": {
    "A": {"include": ["lib/**/*.dart"], "exclude": ["**/*.g.dart", "**/*.freezed.dart"]},
    "B": {"include": ["test/**/*_test.dart"], "exclude": []}
  }
}
```

- Targeted test: `fvm flutter test test/domain/app_test.dart --plain-name '<name>'`.
- Generated files (`*.g.dart`, `*.freezed.dart`, mocks) must be excluded; do not
  mutate codegen.
- `dart format` needs the project's page width from `analysis_options.yaml`;
  run `pub get` first or it may fall back to 80 and rewrite everything.
- If the project uses `gates.sh t1`, put it in `gate_cmd`. It is optional.

## TypeScript / JavaScript

```json
{
  "setup_cmds": [["pnpm", "install", "--frozen-lockfile"]],
  "format_check_cmd": ["pnpm", "exec", "biome", "check", "."],
  "analyze_cmd": ["pnpm", "exec", "tsc", "--noEmit"],
  "analyze_extensions": [".ts", ".tsx", ".js", ".jsx"],
  "test_cmd": ["pnpm", "exec", "vitest", "run"],
  "test_for_source": [{"src": "^src/(.*)\\.(ts|tsx)$", "test": "src/\\1.test.\\2"}],
  "candidates": {
    "A": {"include": ["src/**/*.ts", "src/**/*.tsx"], "exclude": ["**/*.d.ts"]},
    "B": {"include": ["src/**/*.test.ts", "src/**/*.test.tsx", "tests/**/*.test.ts"], "exclude": []}
  }
}
```

- Targeted test: `pnpm exec vitest run src/app.test.ts -t '<name>'`.
- Jest: `pnpm exec jest <file> -t '<name>' --runInBand`.
- Do not put `--watch` in any config command; the local loop must terminate.
- `tsc --noEmit` is whole-project. If it is too slow, use the project's incremental
  or project-references command, or keep it in `gate_cmd`/CI.

## Go

```json
{
  "setup_cmds": [["go", "mod", "download"]],
  "format_check_cmd": ["gofmt", "-l", "."],
  "analyze_cmd": ["go", "vet"],
  "analyze_extensions": [".go"],
  "test_cmd": ["go", "test"],
  "test_for_source": [{"src": "^(.*)\\.go$", "test": "\\1_test.go"}],
  "candidates": {
    "A": {"include": ["**/*.go"], "exclude": ["**/*_test.go", "**/vendor/**"]},
    "B": {"include": ["**/*_test.go"], "exclude": ["**/vendor/**"]}
  }
}
```

- Targeted test: `go test ./pkg/... -run '^TestName$'`.
- `gofmt -l .` prints files needing formatting and exits 0; wrap it if you need a
  failing check: `["bash","-c","test -z \"$(gofmt -l .)\""]` is a shell, so prefer
  the project's lint entry point or accept `gate_cmd` checking the output.
- `go vet ./...` can be whole-module; scope it in `gate_cmd` if needed.

## Anything else

Read `AGENTS.md`, `CONTRIBUTING.md`, `Makefile`, `justfile`, `package.json`
scripts, or CI config and translate the project's own commands into
`format_check_cmd`, `analyze_cmd`, `test_cmd`, and `gate_cmd` as argv arrays.
Do not invent a command the project does not use.

## Command rules

- Every command is an **argv array**, never a shell string.
- If a step is not configured, it is skipped with `[skip] <step> not configured`.
- To run a shell pipeline deliberately, make the argv explicit:
  `["bash","-lc","<pipeline>"]`. The scripts never interpolate user values into
  a shell.
- Keep the targeted command targeted. Full-suite runs belong to CI.
