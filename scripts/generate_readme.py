#!/usr/bin/env python3
"""Regenerate the dynamic sections of README.md.

Two sections are derived from the repo instead of hand-written, so they
cannot drift when a skill is added, removed, or edited:

1. Skills table: one row per skills/*/SKILL.md, with the `description:`
   frontmatter field copied verbatim (that field is the trigger text a
   model sees, so any paraphrase here would be a second copy to drift).
2. Layout tree: top-level files plus per-subdirectory file counts per skill.

Marker pairs in README.md delimit the generated regions:

    <!-- SKILLS:START --> ... <!-- SKILLS:END -->
    <!-- LAYOUT:START --> ... <!-- LAYOUT:END -->

Usage:
    python3 scripts/generate_readme.py          # rewrite README.md in place
    python3 scripts/generate_readme.py --check  # exit 1 if README.md is stale
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
SKILLS_DIR = ROOT / "skills"

SKILLS_START = "<!-- SKILLS:START -->"
SKILLS_END = "<!-- SKILLS:END -->"
LAYOUT_START = "<!-- LAYOUT:START -->"
LAYOUT_END = "<!-- LAYOUT:END -->"


def parse_frontmatter(skill_md: Path) -> tuple[str, str]:
    """Return (name, description) from SKILL.md YAML frontmatter.

    Parsed with regex on purpose: the descriptions are single-line scalars
    and this keeps the generator dependency-free (CI runs it with plain
    python3, no pip install).
    """
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{skill_md}: missing opening YAML frontmatter")
    frontmatter = text.split("---", 2)[1]

    name_match = re.search(r"^name:\s*([^\s#]+)", frontmatter, re.M)
    if not name_match:
        raise ValueError(f"{skill_md}: frontmatter missing 'name:'")
    name = name_match.group(1).strip()

    # description: plus any indented continuation lines (folded scalar).
    lines = frontmatter.splitlines()
    desc_parts: list[str] = []
    in_desc = False
    for line in lines:
        if not in_desc:
            m = re.match(r"^description:\s*(.*)$", line)
            if m:
                desc_parts.append(m.group(1).strip())
                in_desc = True
        else:
            if line.startswith((" ", "\t")) and line.strip():
                desc_parts.append(line.strip())
            else:
                break
    description = " ".join(p for p in desc_parts if p)
    description = re.sub(r"\s+", " ", description).strip()
    if not description:
        raise ValueError(f"{skill_md}: frontmatter missing or empty 'description:'")
    return name, description


def render_skills_table() -> str:
    rows = []
    for skill_dir in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
        if skill_dir.name.startswith("."):
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        name, description = parse_frontmatter(skill_md)
        if name != skill_dir.name:
            raise ValueError(
                f"{skill_md}: frontmatter name '{name}' != directory '{skill_dir.name}'"
            )
        cell = description.replace("|", "\\|")
        rows.append(f"| **[{name}](./skills/{name}/SKILL.md)** | {cell} |")
    header = "| Skill | Use it when |\n|---|---|"
    return header + "\n" + "\n".join(rows)


def render_layout() -> str:
    out = ["skills/"]
    for skill_dir in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
        if skill_dir.name.startswith("."):
            continue
        out.append(f"  {skill_dir.name}/")
        top_files = sorted(
            p.name for p in skill_dir.iterdir() if p.is_file() and not p.name.startswith(".")
        )
        # Entry point first: it is the file the table links to.
        top_files = sorted(top_files, key=lambda n: (n != "SKILL.md", n))
        out.extend(f"    {n}" for n in top_files)
        subdirs = sorted(p for p in skill_dir.iterdir() if p.is_dir())
        subdirs = [p for p in subdirs if not p.name.startswith(".")]
        width = max([len(p.name) + 1 for p in subdirs] + [0])
        for sub in subdirs:
            count = sum(
                1 for p in sub.rglob("*") if p.is_file() and not any(
                    part.startswith(".") for part in p.relative_to(sub).parts
                )
            )
            noun = "file" if count == 1 else "files"
            out.append(f"    {(sub.name + '/').ljust(width)} # {count} {noun}")
    return "```\n" + "\n".join(out) + "\n```"


def replace_section(text: str, start: str, end: str, body: str) -> str:
    pattern = re.compile(
        re.escape(start) + r".*?" + re.escape(end), re.DOTALL
    )
    replacement = f"{start}\n{body}\n{end}"
    new_text, n = pattern.subn(replacement, text)
    if n != 1:
        raise ValueError(f"README.md must contain exactly one {start}...{end} block")
    return new_text


def main() -> None:
    check_only = "--check" in sys.argv
    text = README.read_text(encoding="utf-8")
    updated = replace_section(text, SKILLS_START, SKILLS_END, render_skills_table())
    updated = replace_section(updated, LAYOUT_START, LAYOUT_END, render_layout())
    if check_only:
        if updated != text:
            print("README.md dynamic sections are stale. Run: python3 scripts/generate_readme.py")
            sys.exit(1)
        print("OK: README.md dynamic sections are fresh.")
    else:
        README.write_text(updated, encoding="utf-8")
        print("README.md regenerated (Skills table, Layout).")


if __name__ == "__main__":
    main()
