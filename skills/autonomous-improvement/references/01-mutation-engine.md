# Tuning the Mutation Engine

Language-agnostic, regex-based. No AST — so every survivor needs human triage.

## What it does

Generates per-line semantic mutants by regex and runs one targeted test command per mutant:
- Relational: `==`/`!=`, `<`/`<=`, `>`/`>=`
- Logical: `&&`/`||`
- Booleans: `true`/`false`
- Collection predicates: `.isEmpty`/`.isNotEmpty`, `.first`/`.last` (where idiomatic)
- Null checks: `== null`/`!= null`
- Off-by-one: `+ 1`/`+ 2`, `- 1`/`- 2`

It counts `SURVIVED` (gap), `KILLED` (caught), and `UNCOMPILABLE` (noise — syntax/type error). Uncompilable is excluded from kill rate so compile failures don't inflate coverage. Output is `JSON` + one `markdown` issue draft for the first survivor.

## Triaging a survivor

Before filing, check:
1. **Equivalent mutant?** The mutation doesn't change behavior (e.g. swapping an unused branch, or `>=` vs `>` where equality is unreachable). Document and close, don't write a vacuous test.
2. **Inside a string or comment?** The regex has a quoted-text guard but it's not an AST — verify the line.
3. **Already covered elsewhere?** Another test file may kill it; run that file with the mutation applied before claiming a gap.
4. **Worth a test?** Some mutations (e.g. `.last` -> `.first` on an unordered collection) encode no invariant worth pinning.

## Language-specific notes

- **Dart/Flutter:** Guard `@` annotations; skip `import`/`export`/`part`; avoid mutating codegen files.
- **TypeScript/JS:** Avoid JSX attribute noise; `===`/`!==` variants need separate rules; skip `.d.ts`.
- **Python:** Mutate `is`/`is not`, `in`/`not in` carefully — many are idiomatic, not semantic.
- **Rust/Go/Java:** Add language predicates to `MUTATION_RULES` rather than relying on the defaults; prefer the ecosystem's AST-based mutator (cargo-mutants, Stryker, PIT, mutmut) when available — this script is the zero-dependency fallback.

## Promotion to a gate

A regex engine should never block PRs. Promote to a diff-scoped blocking gate only after:
- two runs with zero false positives on that repo, and
- a documented triage log showing survivors were non-equivalent.

Prefer an AST-based tool for blocking gates where one exists for that language.
