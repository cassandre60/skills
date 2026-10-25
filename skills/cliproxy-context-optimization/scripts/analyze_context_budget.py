#!/usr/bin/env python3
"""Context budget analyzer for a rendered CLIProxyAPI capture bundle.

Reads the bundle produced by ``render_request_log.py`` (the transcript
``*.md`` and the ``*-system-prompt.md``) and reports where the per-turn
context tokens go: the skill catalog, the agent baseline files, the
harness/catalog fixed cost, and the latency levers recorded in the capture.

It never modifies the capture or the bundle. It inspects sizes by character
count (measured, authoritative) and reports token counts as estimates built
from a configurable chars-per-token ratio (labelled ``est.`` everywhere).

Usage:
  python3 analyze_context_budget.py BUNDLE_DIR
  python3 analyze_context_budget.py --system-prompt A.md --transcript B.md
  python3 analyze_context_budget.py BUNDLE_DIR --json --top 20 --ratio 3.85

BUNDLE_DIR must contain one rendered bundle: a transcript ``<id>.md`` and, if
the capture had a system message, ``<id>-system-prompt.md``. When several
bundles are present the newest by modification time wins. Error captures
carry no usage facts; the analyzer reports what is present and says so.

Exit codes: 0 analysis produced, 2 usage problem, 3 nothing analyzable.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

DEFAULT_RATIO = 3.85
DEFAULT_TOP = 15
DUPLICATE_RATIO_THRESHOLD = 0.50

SECTION_ORDER = (
    "harness_and_identity",
    "code_mode_catalog",
    "skills_catalog",
    "baseline_files",
    "date_and_env",
)


def parse_skills_catalog(block: str) -> dict:
    """Character budget and similarity flags for the <available_skills> block."""
    skills = []
    for m in re.finditer(r"<skill>\s*<id>([^<]+)</id>([\s\S]*?)</skill>", block):
        pid = m.group(1)
        dm = re.search(r"<description>([\s\S]*?)</description>", m.group(2))
        skills.append(
            {
                "id": pid,
                "chars": m.end() - m.start(),
                "description": dm.group(1) if dm else "",
            }
        )
    flagged = []
    for i in range(len(skills)):
        for j in range(i + 1, len(skills)):
            a, b = skills[i], skills[j]
            if not a["description"] or not b["description"]:
                continue
            ratio = difflib.SequenceMatcher(
                None, a["description"], b["description"], autojunk=False
            ).ratio()
            if ratio >= DUPLICATE_RATIO_THRESHOLD:
                flagged.append(
                    {
                        "pair": [a["id"], b["id"]],
                        "similarity": round(ratio, 2),
                        "chars": [a["chars"], b["chars"]],
                    }
                )
    flagged.sort(key=lambda f: -f["similarity"])
    return {
        "skill_count": len(skills),
        "char_total": sum(s["chars"] for s in skills),
        "skills_sorted": sorted(skills, key=lambda s: -s["chars"]),
        "possible_overlap_pairs": flagged,
    }


def parse_system_prompt_doc(text: str) -> dict:
    """Split the rendered system-prompt document into named sections."""
    errors: list[str] = []
    header_len = None
    model = None
    m = re.search(
        r"The\s+([\d,]+)-character system message sent to `([^`]+)`", text
    )
    if m:
        header_len = int(m.group(1).replace(",", ""))
        model = m.group(2)
    else:
        errors.append("system-prompt header line not found")

    payload_start = text.find("You are an AI agent")
    payload_start = text.find("\n", payload_start) + 1 if payload_start >= 0 else 0

    code_mode = text.find("\n# Code Mode", payload_start)
    skills_start = text.find("<available_skills>")
    skills_end = text.find("</available_skills>")
    instr = text.find("Instructions from:")
    today = text.find("Today's date")
    env = text.find("<env>")

    if skills_start >= 0 and skills_end == -1:
        errors.append("<available_skills> opened but never closed")

    bounded = [p for p in (skills_start, instr, today, env) if p >= 0]

    code_catalog_end = min(bounded) if bounded else len(text)
    skills_end_abs = (
        skills_end + len("</available_skills>") if skills_end >= 0 else None
    )
    baseline_begin = instr if instr >= 0 else None
    baseline_end = next((p for p in (today, env) if p >= 0), len(text)) \
        if baseline_begin is not None else None
    tail_begin = min((p for p in (today, env) if p >= 0), default=None)

    def span(begin, end):
        if begin is None or end is None or begin >= end:
            return 0
        return end - begin

    sections = {
        "harness_and_identity": span(
            payload_start,
            (code_mode + 1) if code_mode >= 0 else code_catalog_end,
        ),
        "code_mode_catalog": span(
            (code_mode + 1) if code_mode >= 0 else None,
            min([p for p in (skills_start,) if p >= 0], default=code_catalog_end)
            if code_mode >= 0
            else code_catalog_end,
        ),
        "skills_catalog": span(
            skills_start,
            skills_end_abs if skills_end_abs is not None else None,
        ),
        "baseline_files": span(baseline_begin, baseline_end),
        "date_and_env": span(tail_begin, len(text)),
    }
    if code_mode < 0 and not sections["code_mode_catalog"]:
        sections["code_mode_catalog"] = 0

    catalog = (
        parse_skills_catalog(text[skills_start:skills_end])
        if 0 <= skills_start < skills_end
        else None
    )

    return {
        "model": model,
        "system_message_chars": header_len,
        "sections": sections,
        "catalog": catalog,
        "errors": errors,
    }


def parse_transcript(text: str) -> dict:
    """Extract usage and latency facts from the rendered transcript."""
    out: dict = {
        "prompt_tokens": None,
        "completion_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": None,
        "request_body_bytes": None,
        "tools_count": None,
        "thinking_level": None,
        "server_ms": None,
        "user_prompt_chars": None,
        "model": None,
        "errors": [],
    }
    m = re.search(r"(\d[\d,]*)\s*prompt tokens", text)
    if m:
        out["prompt_tokens"] = int(m.group(1).replace(",", ""))
        m = re.search(r"\+\s*(\d[\d,]*)\s*completion tokens", text)
        if m:
            out["completion_tokens"] = int(m.group(1).replace(",", ""))
        m = re.search(r"\((\d[\d,]*) of them reasoning\)", text)
        if m:
            out["reasoning_tokens"] = int(m.group(1).replace(",", ""))
        m = re.search(r"=\s*(\d[\d,]*)\s*total", text)
        if m:
            out["total_tokens"] = int(m.group(1).replace(",", ""))
    else:
        m = re.search(
            r"\| Tokens \(prompt / completion[^|]*\|\s*([\d,]+)\s*/\s*([\d,]+)"
            r"(?:\s*/\s*([\d,]+))?(?:\s*/\s*([\d,]+))?",
            text,
        )
        if m:
            out["prompt_tokens"] = int(m.group(1).replace(",", ""))
            out["completion_tokens"] = int(m.group(2).replace(",", ""))
            if m.group(3):
                out["reasoning_tokens"] = int(m.group(3).replace(",", ""))
            if m.group(4):
                out["total_tokens"] = int(m.group(4).replace(",", ""))
    if out["prompt_tokens"] is None:
        out["errors"].append("usage line not found (error captures carry none)")

    m = re.search(r"Request body[^0-9]{0,80}?([\d][\d,]*)\s*bytes", text)
    if m:
        out["request_body_bytes"] = int(m.group(1).replace(",", ""))
    else:
        m = re.search(r"Content-Length:\s*(\d+)", text)
        if m:
            out["request_body_bytes"] = int(m.group(1))

    m = (
        re.search(r"Tools:\s*(\d+)\s*declared", text)
        or re.search(r"Function declarations \((\d+)\)", text)
        or re.search(r"\| Tools:\s*(\d+)", text)
        or re.search(r"Tools:\s*(\d+)", text)
    )
    if m:
        out["tools_count"] = int(m.group(1))

    m = re.search(r"thinking level\s*`(\w+)`", text) or re.search(
        r"thinkingLevel\s*\|\s*(\w+)", text
    )
    if m:
        out["thinking_level"] = m.group(1)

    m2 = re.search(r"dur=(\d+)", text)
    m3 = re.search(r"([\d.]+)\s*s of server time", text)
    if m2:
        out["server_ms"] = float(int(m2.group(1)))
    elif m3:
        out["server_ms"] = float(m3.group(1)) * 1000.0

    m = re.search(r"The user said [\"“](.+?)[\"”]", text) or re.search(
        r"\| User prompt \| `?([^`|\n]+)`? \|", text
    )
    if m:
        out["user_prompt_chars"] = len(m.group(1).strip())

    m = (
        re.search(r"Model: `([A-Za-z0-9_./-]+)`", text)
        or re.search(r"\| Model \| ([A-Za-z0-9_./-]+)", text)
        or re.search(r"# Chat transcript: ([A-Za-z0-9_./-]+)", text)
        or re.search(r"# Chat completions log: ([A-Za-z0-9_./-]+)", text)
    )
    if m:
        out["model"] = m.group(1)
    return out


def _fmt(n: int) -> str:
    return f"{n:,}"


def _est_tokens(chars: int, ratio: float) -> int:
    return round(chars / ratio) if chars else 0


def build_levers(usage: dict, sp: dict) -> list[str]:
    """Deterministic lever lines, ordered by expected headroom."""
    levers: list[str] = []
    catalog = sp.get("catalog") or {}
    sys_chars = (
        sp.get("system_message_chars")
        or sum(sp["sections"].values())
        or 1
    )
    cat_chars = catalog.get("char_total", 0)
    if cat_chars:
        n = catalog.get("skill_count", 0)
        if cat_chars > 8000 or n >= 20:
            levers.append(
                f"Skills catalog: {n} skills, {_fmt(cat_chars)} chars "
                f"({cat_chars * 100.0 / sys_chars:.0f}% of the system "
                "message) — park unused skills outside the discovery "
                "directories; the description block is the whole cost."
            )
        pairs = catalog.get("possible_overlap_pairs") or []
        if pairs:
            names = ", ".join(
                f"{p['pair'][0]}/{p['pair'][1]} ({p['similarity']:.2f})"
                for p in pairs[:5]
            )
            levers.append(
                "Possible duplicate skills by description similarity; read "
                "both, keep one per role, park the rest: " + names
            )
    base_chars = sp["sections"].get("baseline_files", 0)
    if base_chars > 6000:
        levers.append(
            f"Agent baseline files: {_fmt(base_chars)} chars — compress "
            "prose policy to one-line bullets; a measured trim kept every "
            "rule and cut the file in half (16,689 → 8,331 chars)."
        )
    thinking = usage.get("thinking_level")
    server = usage.get("server_ms") or 0
    small_prompt = (usage.get("user_prompt_chars") or 10 ** 9) <= 40
    if thinking == "high" and small_prompt:
        levers.append(
            "Trivial-turn latency: thinking level 'high' upstream plus "
            f"{server / 1000:.1f} s of server time to answer a short prompt "
            "— use a lite or thinking-lite variant for chat-scale turns; "
            "context trimming cannot remove this."
        )
    levers.append(
        "Harness fixed cost: code_mode_catalog "
        f"{_fmt(sp['sections'].get('code_mode_catalog', 0))} chars and "
        f"{usage.get('tools_count') or '?'} tool declarations belong to the "
        "harness and change with releases, not configuration."
    )
    levers.append(
        "Caching: keep section order and the leading sections byte-stable "
        "when trimming (date/env last) so repeated turns share one "
        "cacheable prefix."
    )
    prompted = usage.get("prompt_tokens")
    if prompted and usage.get("request_body_bytes") and sys_chars:
        levers.append(
            f"Measure-then-verify: re-run after any change on a fresh "
            f"successful capture; today's fixed payload is {prompted} "
            f"prompt tokens over {_fmt(usage['request_body_bytes'])} body "
            "bytes — error captures carry no usage, verify with success."
        )
    return levers


def analyze(sp_text: str, transcript_text: str, top: int = DEFAULT_TOP) -> dict:
    sp = parse_system_prompt_doc(sp_text)
    usage = parse_transcript(transcript_text)
    catalog = sp.get("catalog") or {}
    sections = {
        name: {
            "chars": chars,
            "est_tokens": _est_tokens(chars, DEFAULT_RATIO),
        }
        for name, chars in sp["sections"].items()
    }
    result = {
        "model": sp["model"] or usage.get("model"),
        "system_message_chars": sp.get("system_message_chars"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": usage.get("reasoning_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "request_body_bytes": usage.get("request_body_bytes"),
        "tools_count": usage.get("tools_count"),
        "thinking_level": usage.get("thinking_level"),
        "server_ms": usage.get("server_ms"),
        "user_prompt_chars": usage.get("user_prompt_chars"),
        "ratio_chars_per_token": DEFAULT_RATIO,
        "sections": sections,
        "catalog_skill_count": catalog.get("skill_count", 0),
        "catalog_char_total": catalog.get("char_total", 0),
        "catalog_top": [
            {"id": s["id"], "chars": s["chars"]}
            for s in catalog.get("skills_sorted", [])[:top]
        ],
        "possible_overlap_pairs": catalog.get("possible_overlap_pairs", []),
        "levers": build_levers(usage, sp),
        "errors": sp["errors"] + usage["errors"],
    }
    return result


def render_markdown(result: dict) -> str:
    lines = ["# Context budget", ""]
    if result["model"]:
        lines.append(f"Model: `{result['model']}`")
    if result.get("prompt_tokens"):
        parts = [f"prompt {result['prompt_tokens']}"]
        if result.get("completion_tokens") is not None:
            parts.append(f"completion {result['completion_tokens']}")
        lines.append(", ".join(parts) + " tokens (measured).")
    lines.append(
        f"System message: {_fmt(result.get('system_message_chars') or 0)} "
        "chars (measured). Section tokens below are estimates at "
        f"{result['ratio_chars_per_token']} chars/token; character counts "
        "are authoritative."
    )
    lines.append("")
    lines.append("| Section | Chars | Est. tokens |")
    lines.append("| --- | ---: | ---: |")
    for name, comp in result["sections"].items():
        lines.append(f"| {name} | {_fmt(comp['chars'])} | {_fmt(comp['est_tokens'])} |")
    total = sum(c["chars"] for c in result["sections"].values())
    lines.append(f"| total (sections) | {_fmt(total)} |  |")
    if result["catalog_top"]:
        lines += [
            "",
            (
                f"## Largest skill descriptions (top {len(result['catalog_top'])} "
                f"of {result['catalog_skill_count']})"
            ),
            "",
            "| Skill | Chars |",
            "| --- | ---: |",
        ]
        for s in result["catalog_top"]:
            lines.append(f"| {s['id']} | {_fmt(s['chars'])} |")
    if result["possible_overlap_pairs"]:
        lines += ["", "## Possible duplicate skills", ""]
        for p in result["possible_overlap_pairs"]:
            lines.append(
                f"- {p['pair'][0]} / {p['pair'][1]} "
                f"(similarity {p['similarity']:.2f}, "
                f"{_fmt(p['chars'][0])} vs {_fmt(p['chars'][1])} chars)"
            )
    if result["levers"]:
        lines += ["", "## Levers", ""]
        for lever in result["levers"]:
            lines.append(f"- {lever}")
    if result["errors"]:
        lines += ["", "## Errors", ""]
        for err in result["errors"]:
            lines.append(f"- {err}")
    lines.append("")
    return "\n".join(lines)


def _resolve_transcript(bundle_dir: Path):
    if not bundle_dir.is_dir():
        return None
    candidates = [
        p
        for p in bundle_dir.glob("*.md")
        if not p.name.endswith(
            ("-system-prompt.md", "-raw-payloads.md", "-payloads.md")
        )
    ]
    return max(candidates, key=lambda p: p.stat().st_mtime_ns) if candidates else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bundle", nargs="?", type=Path,
                        help="rendered bundle directory")
    parser.add_argument("--system-prompt", type=Path)
    parser.add_argument("--transcript", type=Path)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.system_prompt and args.transcript:
        sp_path, tx_path = args.system_prompt, args.transcript
    elif args.transcript and not args.system_prompt:
        tx_path = args.transcript
        sp_path = tx_path.parent / (tx_path.stem + "-system-prompt.md")
    elif args.bundle and args.bundle.is_file():
        tx_path = args.bundle
        sp_path = args.system_prompt or tx_path.parent / (
            tx_path.stem + "-system-prompt.md"
        )
    elif args.bundle:
        tx_path = args.transcript or _resolve_transcript(args.bundle)
        if tx_path is None:
            print("error: no transcript (*.md) found in bundle", file=sys.stderr)
            return 2
        sp_path = args.system_prompt or tx_path.parent / (
            tx_path.stem + "-system-prompt.md"
        )
    else:
        parser.error("give a bundle directory or both --system-prompt and "
                     "--transcript")
        return 2
    if not sp_path.exists():
        print(f"error: {sp_path} not found", file=sys.stderr)
        return 2
    if not tx_path.exists():
        print(f"error: {tx_path} not found", file=sys.stderr)
        return 2
    try:
        result = analyze(
            sp_path.read_text(encoding="utf-8"),
            tx_path.read_text(encoding="utf-8"),
            args.top,
        )
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        sys.stdout.write(render_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
