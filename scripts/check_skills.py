#!/usr/bin/env python3
"""Linter for the skills repository.

Enforces:
1. Every skill lives in skills/<name>/ and contains SKILL.md
2. SKILL.md has valid YAML frontmatter with `name:` matching <name> and a non-empty `description:`
3. Relative links/paths mentioned inside the skill directory resolve to real files
4. No hardcoded API keys / secrets (sk-..., ghp_..., etc.)

The frontmatter check parses YAML the way the `skills` installer does. A
SKILL.md whose frontmatter the installer cannot parse is silently skipped at
install time, so a regex-only check lets a broken skill ship unnoticed.
"""
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(
        "Error: PyYAML is required to validate skill frontmatter.\n"
        "Install it with: pip install -r scripts/requirements.txt",
        file=sys.stderr,
    )
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"

SECRET_PATTERNS = [
    (re.compile(r"sk-[a-zA-Z0-9_-]{20,}"), "OpenAI/gateway API key pattern"),
    (re.compile(r"ghp_[a-zA-Z0-9]{30,}"), "GitHub personal access token"),
    (re.compile(r"github_pat_[a-zA-Z0-9_]{60,}"), "Fine-grained GitHub token"),
]

# Eval fixtures may deliberately hold fake credentials so scanners can be
# tested against them. A file may opt out by putting this exact marker in
# one of its first five lines — the marker is the audit trail that makes the
# opt-out reviewable.
SYNTHETIC_CREDENTIAL_MARKER = "synthetic credential — not a real secret"

def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split SKILL.md into (frontmatter, body).

    Returns (None, text) when the file has no opening `---` or the frontmatter
    is never closed. A `---` inside the body (a horizontal rule) is not treated
    as the closing delimiter.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    return None, text

def check_frontmatter(skill_dir: Path, skill_md: Path, issues: list):
    text = skill_md.read_text(encoding="utf-8")
    frontmatter, _ = split_frontmatter(text)

    if frontmatter is None:
        if not text.startswith("---"):
            issues.append(f"{skill_dir.name}: SKILL.md missing opening YAML frontmatter ('---')")
        else:
            issues.append(f"{skill_dir.name}: SKILL.md malformed YAML frontmatter (unclosed '---')")
        return text

    try:
        data = yaml.safe_load(frontmatter)
    except yaml.YAMLError as error:
        issues.append(
            f"{skill_dir.name}: SKILL.md frontmatter is not valid YAML "
            f"(the skills installer will skip this skill): {error}"
        )
        return text

    if not isinstance(data, dict):
        issues.append(f"{skill_dir.name}: SKILL.md frontmatter must be a YAML mapping")
        return text

    name = data.get("name")
    description = data.get("description")

    if not isinstance(name, str) or not name.strip():
        issues.append(f"{skill_dir.name}: SKILL.md frontmatter missing 'name:'")
    elif name.strip() != skill_dir.name:
        issues.append(
            f"{skill_dir.name}: SKILL.md frontmatter name '{name.strip()}' does not match directory '{skill_dir.name}'"
        )

    if not isinstance(description, str) or not description.strip():
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
        if any(
            SYNTHETIC_CREDENTIAL_MARKER in line
            for line in content.splitlines()[:5]
        ):
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
