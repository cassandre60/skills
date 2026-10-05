# Ecosystem map: tools per language

Contents: cross-language tools, then per ecosystem. Verify maintenance status and compatibility before adopting;
tooling changes. Where a row says "thin", plan to build a small harness or use `scripts/manual_mutation_runner.py`.

## Cross-language
- Static analysis/SAST: semgrep, CodeQL. Secret scanning: gitleaks, trufflehog. Dependency vulnerabilities:
  OSV-Scanner, plus ecosystem auditors. Dependency updates: Dependabot or Renovate.
- Hooks: pre-commit framework (convenience only, not authority). Benchmarks of CLIs: hyperfine.
- Formal/model checking for small kernels: TLA+, Alloy. Distributed-systems fault testing: Jepsen-style harnesses.
- Gate scripts in this skill (`scripts/`) are Python 3 standard library only and work with any language.

## Python
Lint/format/type: ruff, mypy or pyright. Tests: pytest. Property: Hypothesis. Mutation: mutmut, cosmic-ray.
Fuzz: Atheris (plus Hypothesis). Coverage: coverage.py with branch mode. Benchmarks: pytest-benchmark, pyperf.
Architecture: import-linter. Deps: pip-audit.

## JavaScript / TypeScript
Lint/type: ESLint or Biome, `tsc --strict` (also `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`).
Tests: Vitest or Jest. Property: fast-check. Mutation: StrykerJS. Coverage: c8/Istanbul.
Benchmarks: tinybench, Vitest bench. Architecture: dependency-cruiser, eslint-plugin-boundaries.
Deps: npm audit, OSV-Scanner.

## Rust
Lint/format: clippy (deny warnings), rustfmt. Property: proptest, quickcheck. Mutation: cargo-mutants.
Fuzz: cargo-fuzz (libFuzzer), afl.rs. UB/concurrency: Miri, Loom. Verification of small kernels: Kani.
Coverage: cargo-llvm-cov. Benchmarks: criterion, divan. Deps/licenses: cargo-deny, cargo-audit.

## Go
Lint: go vet, staticcheck, golangci-lint (depguard for import rules). Tests: `go test` with `-race`.
Fuzz: native `go test -fuzz`. Property: rapid, gopter. Mutation: go-mutesting, gremlins.
Benchmarks: `testing.B` with benchstat. Vulnerabilities: govulncheck. Coverage: `go test -cover`.

## Java / Kotlin
Lint/analysis: Error Prone, SpotBugs, detekt/ktlint. Property: jqwik, Kotest property testing.
Mutation: PIT (pitest). Coverage: JaCoCo. Benchmarks: JMH. Architecture: ArchUnit.
Fuzz: Jazzer. Deps: OWASP dependency-check.

## C# / .NET
Analyzers: Roslyn analyzers with warnings as errors. Property: FsCheck. Mutation: Stryker.NET.
Benchmarks: BenchmarkDotNet. Architecture: NetArchTest.

## Swift
Lint/format: SwiftLint, swift-format. Tests: XCTest/Swift Testing. Property: SwiftCheck.
Mutation: Muter. Sanitizers via Xcode (address, thread, undefined behavior).

## Dart / Flutter
Analysis: `dart analyze` with strict language options (strict-casts, strict-inference, strict-raw-types),
a strict lint set, warnings as errors in CI. Tests: `flutter test`, `dart test`; widget tests for key flows;
`integration_test` for end-to-end and performance timelines in profile mode.
Property: glados (verify current status) or hand-rolled seeded generators. Mutation: thin; use the
`mutation_test` package if it fits, otherwise `scripts/manual_mutation_runner.py`.
Coverage: `flutter test --coverage` plus lcov processing. Benchmarks: `benchmark_harness`, timeline summaries.
Architecture rules: custom lint rules or a plain test scanning imports; banned-API script for the core.
Deps: `dart pub outdated`, OSV-Scanner on `pubspec.lock`. Fuzzing: no mature tool; use large seeded property runs.
Remember the platform split: test the core with plain Dart on the VM (fast, no emulator) and keep Flutter-bound
code thin.

## C / C++
Sanitizers (ASan, UBSan, TSan, MSan), libFuzzer, AFL++, clang-tidy, Mull for mutation, gcov/llvm-cov,
Google Benchmark, Valgrind. Treat any parser of untrusted data as R0 and fuzz it continuously.

## Mobile and platform matrices
Define a risk-based matrix (minimum supported OS, current OS, one in between per platform; low-end and
current device class) rather than every version. Run the fast gates on emulators/simulators and key flows
and performance on real devices or a device farm in T3/T4. For local-first apps the network surface is small:
isolate it behind one module and test it with scripted fakes.
