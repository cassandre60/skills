#!/usr/bin/env python3
"""Linter for the skills repository.

Enforces:
1. Every skill lives in skills/<name>/ and contains SKILL.md
2. SKILL.md has valid YAML frontmatter with `name:` matching <name> and a non-empty `description:`
3. Relative links/paths mentioned inside the skill directory resolve to real files
4. No hardcoded API keys / secrets (sk-..., ghp_..., etc.)
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"

SECRET_PATTERNS = [
    (re.compile(r"sk-[a-zA-Z0-9_-]{20,}"), "OpenAI/gateway API key pattern"),
    (re.compile(r"ghp_[a-zA-Z0-9]{30,}"), "GitHub personal access token"),
    (re.compile(r"github_pat_[a-zA-Z0-9_]{60,}"), "Fine-grained GitHub token"),
]

def check_frontmatter(skill_dir: Path, skill_md: Path, issues: list):
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        issues.append(f"{skill_dir.name}: SKILL.md missing opening YAML frontmatter ('---')")
        return text

    parts = text.split("---", 2)
    if len(parts) < 3:
        issues.append(f"{skill_dir.name}: SKILL.md malformed YAML frontmatter (unclosed '---')")
        return text

    frontmatter = parts[1]
    name_match = re.search(r"^name:\s*([^\s#]+)", frontmatter, re.M)
    desc_match = re.search(r"^description:\s*(.+)", frontmatter, re.M)

    if not name_match:
        issues.append(f"{skill_dir.name}: SKILL.md frontmatter missing 'name:'")
    elif name_match.group(1).strip() != skill_dir.name:
        issues.append(
            f"{skill_dir.name}: SKILL.md frontmatter name '{name_match.group(1)}' does not match directory '{skill_dir.name}'"
        )

    if not desc_match or not desc_match.group(1).strip():
        issues.append(f"{skill_dir.name}: SKILL.md frontmatter missing or empty 'description:'")

    return text

def check_secrets(skill_dir: Path, issues: list):
    for path in skill_dir.rglob("*"):
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(content):
                issues.append(f"{path.relative_to(ROOT)}: possible leaked credential ({label})")

def check_relative_references(skill_dir: Path, issues: list):
    """Ensure explicit relative path references inside skill files point to real targets."""
    path_pattern = re.compile(r"`((?:references|scripts|assets)/[a-zA-Z0-9_\-\./]+)`")
    link_pattern = re.compile(r"\[(?:[^\]]+)\]\((?!https?://|#|mailto:)([^\)]+)\)")

    for path in skill_dir.rglob("*.md"):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")

        # Check explicit relative markdown links
        for match in link_pattern.finditer(content):
            target_str = match.group(1).split("#")[0].strip()
            if not target_str:
                continue
            resolved = (path.parent / target_str).resolve()
            if not resolved.exists():
                issues.append(
                    f"{path.relative_to(ROOT)}: broken markdown link '({target_str})'"
                )

        # Check backtick path mentions like `references/foo.md` or `scripts/bar.py`
        for match in path_pattern.finditer(content):
            target_str = match.group(1).strip()
            resolved = (skill_dir / target_str).resolve()
            if not resolved.exists():
                # Allow examples/templates that end in .example / .template
                issues.append(
                    f"{path.relative_to(ROOT)}: referenced path `{target_str}` does not exist in skill"
                )

def main():
    if not SKILLS_DIR.exists() or not SKILLS_DIR.is_dir():
        print(f"Error: skills directory not found at {SKILLS_DIR}")
        sys.exit(1)

    issues = []
    skill_dirs = [p for p in SKILLS_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")]

    if not skill_dirs:
        issues.append("No skills found under skills/")

    for skill_dir in sorted(skill_dirs):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            issues.append(f"{skill_dir.name}: missing SKILL.md")
            continue

        check_frontmatter(skill_dir, skill_md, issues)
        check_secrets(skill_dir, issues)
        check_relative_references(skill_dir, issues)

    if issues:
        print("Skill check failed:\n")
        for issue in issues:
            print(f"  FAIL: {issue}")
        print(f"\n{len(issues)} problem(s) found.")
        sys.exit(1)

    print(f"OK: verified {len(skill_dirs)} skill(s) across frontmatter, secrets, and internal references.")

if __name__ == "__main__":
    main()
