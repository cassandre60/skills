#!/usr/bin/env python3
"""Fast, dependency-free scan that tells you where a repo stands before you plan gates.

Prints: languages and size, test-to-source ratio, CI/lint/coverage/mutation/property-test
signals, referee-protection signals, and the gate maturity level (L0-L5) it suggests.
It only looks at file names and greps; treat the output as a starting hypothesis.

Usage: assess_repo.py [path] [--json]
"""
import json
import os
import re
import sys

SKIP_DIRS = {".git", "node_modules", ".dart_tool", "build", "dist", "target", ".venv", "venv",
             "__pycache__", ".gradle", "Pods", ".idea", ".next", "vendor", "coverage", ".pub-cache"}
LANGS = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
         ".dart": "Dart", ".rs": "Rust", ".go": "Go", ".java": "Java", ".kt": "Kotlin", ".swift": "Swift",
         ".c": "C", ".h": "C/C++", ".cc": "C++", ".cpp": "C++", ".cs": "C#", ".rb": "Ruby", ".php": "PHP",
         ".sh": "Shell", ".lua": "Lua"}
TEST_RE = re.compile(r"(^|/)(tests?|__tests__|spec|specs|integration_test|e2e)(/|$)|(_test|_spec|\.test|\.spec)\.|(^|/)test_[^/]*$", re.I)
MANIFESTS = ["package.json", "pubspec.yaml", "Cargo.toml", "go.mod", "pyproject.toml", "requirements.txt",
             "pom.xml", "build.gradle", "build.gradle.kts", "Package.swift", "Gemfile", "composer.json"]
SIGNALS = {
    "property tests": r"\b(hypothesis|fast[-_]check|proptest|quickcheck|jqwik|glados|rapid|gopter|forAll|@given)\b",
    "mutation tooling": r"(stryker|mutmut|cosmic-ray|cargo-mutants|pitest|go-mutesting|gremlins|mutation_test|muter)",
    "fuzzing": r"(cargo-fuzz|libFuzzer|go test -fuzz|atheris|jazzer|afl)",
    "benchmarks": r"(criterion|divan|benchmark_harness|pytest-benchmark|tinybench|BenchmarkDotNet|testing\.B\b|JMH)",
    "strict analysis": r"(strict-casts|strict-inference|\"strict\"\s*:\s*true|--strict|#!\[deny\(warnings\)\]|-Werror|warningsAsErrors)",
}


def walk(root):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            yield os.path.join(dp, fn)


def count_lines(path):
    try:
        with open(path, "rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = os.path.abspath(args[0] if args else ".")
    as_json = "--json" in sys.argv

    langs, src_loc, test_loc = {}, 0, 0
    manifests, text_blobs = [], []
    files = list(walk(root))
    rel = [os.path.relpath(f, root).replace(os.sep, "/") for f in files]
    for f, r in zip(files, rel):
        ext = os.path.splitext(f)[1].lower()
        base = os.path.basename(f)
        if base in MANIFESTS:
            manifests.append(r)
        if ext in LANGS:
            n = count_lines(f)
            langs[LANGS[ext]] = langs.get(LANGS[ext], 0) + n
            if TEST_RE.search(r):
                test_loc += n
            else:
                src_loc += n
        if base in MANIFESTS or r.startswith((".github/", ".gitlab-ci")) or base.endswith((".yaml", ".yml", ".toml", ".json")) and count_lines(f) < 2000:
            try:
                with open(f, encoding="utf-8", errors="ignore") as fh:
                    text_blobs.append(fh.read(200000))
            except OSError:
                pass
    blob = "\n".join(text_blobs)
    for f, r in zip(files, rel):
        if TEST_RE.search(r) and os.path.splitext(f)[1].lower() in LANGS and len(blob) < 5_000_000:
            try:
                with open(f, encoding="utf-8", errors="ignore") as fh:
                    blob += "\n" + fh.read(50000)
            except OSError:
                pass

    has = lambda p: any(re.search(p, r) for r in rel)  # noqa: E731
    facts = {
        "languages_loc": dict(sorted(langs.items(), key=lambda kv: -kv[1])),
        "source_loc": src_loc,
        "test_loc": test_loc,
        "test_to_source_ratio": round(test_loc / src_loc, 2) if src_loc else None,
        "manifests": manifests[:12],
        "ci_config": has(r"^\.github/workflows/") or has(r"^\.gitlab-ci\.yml$") or has(r"^azure-pipelines") or has(r"^\.circleci/"),
        "lockfile": has(r"(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|pubspec\.lock|Cargo\.lock|go\.sum|poetry\.lock|uv\.lock)$"),
        "codeowners": has(r"(^|/)CODEOWNERS$"),
        "pr_template": has(r"pull_request_template"),
        "agent_docs": [r for r in rel if os.path.basename(r) in ("AGENTS.md", "CLAUDE.md")][:5],
        "spec_doc": [r for r in rel if os.path.basename(r).upper().startswith(("SPEC", "INVARIANTS", "QUALITY"))][:5],
        "gates_dir": has(r"^\.gates/") or has(r"(^|/)GATES\.md$"),
        "coverage_config": bool(re.search(r"(lcov|coverage|cov-report|c8|nyc|llvm-cov|jacoco|--coverage)", blob)),
        "pre_commit": has(r"^\.pre-commit-config\.yaml$") or has(r"^\.husky/"),
        "secret_scanning": bool(re.search(r"(gitleaks|trufflehog|detect-secrets)", blob)),
        "dep_audit": bool(re.search(r"(dependabot|renovate|osv-scanner|cargo-audit|cargo-deny|pip-audit|npm audit|govulncheck)", blob + "\n".join(rel))),
    }
    for name, pat in SIGNALS.items():
        facts[name] = bool(re.search(pat, blob, re.I))

    level = 0
    if facts["test_loc"] > 0: level = 1
    if level >= 1 and facts["ci_config"] and facts["coverage_config"]: level = 2
    if level >= 2 and (facts["property tests"] or facts["spec_doc"]) and facts["strict analysis"]: level = 3
    if level >= 3 and facts["mutation tooling"] and facts["codeowners"] and facts["gates_dir"]: level = 4
    if level >= 4 and facts["benchmarks"] and (facts["fuzzing"] or (facts["gates_dir"] and has(r"redteam"))): level = 5
    facts["suggested_maturity_level"] = f"L{level}"

    if as_json:
        print(json.dumps(facts, indent=2))
        return 0
    print(f"# Repo assessment: {root}\n")
    print("languages (LOC):", ", ".join(f"{k} {v}" for k, v in facts["languages_loc"].items()) or "none detected")
    print(f"source LOC {src_loc}, test LOC {test_loc}, ratio {facts['test_to_source_ratio']}")
    print("manifests:", ", ".join(manifests[:8]) or "none")
    print()
    for key in ("ci_config", "lockfile", "coverage_config", "strict analysis", "property tests", "mutation tooling",
                "fuzzing", "benchmarks", "codeowners", "pr_template", "pre_commit", "secret_scanning", "dep_audit", "gates_dir"):
        print(f"[{'x' if facts[key] else ' '}] {key}")
    print("agent docs:", facts["agent_docs"] or "none")
    print("spec/invariants docs:", facts["spec_doc"] or "none")
    print(f"\nsuggested maturity: {facts['suggested_maturity_level']}  (see references/assess-and-plan.md)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
