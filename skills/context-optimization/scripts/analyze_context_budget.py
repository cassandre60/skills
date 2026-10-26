#!/usr/bin/env python3
"""Context budget analyzer for one agent model turn, from any capture source.

Reports where the per-turn context tokens go — the skill catalog, the agent
instruction baseline files, the harness catalogs and tool declarations, the
date/env tail, and the latency levers recorded in the capture — and pairs
every measured lever with a verification step on a fresh successful capture.

Supported sources (all read-only):

- a rendered capture bundle (the CLIProxyAPI adapter's output): the transcript
  ``<id>.md`` plus ``<id>-system-prompt.md``,
- explicit ``--system-prompt``/``--transcript`` rendered Markdown paths, or
- a raw OpenAI-compatible pair: ``--request`` (chat completion request JSON
  with ``messages`` and ``tools``) together with ``--response`` (response JSON
  with ``usage``), plus optional ``--latency-ms`` when the source recorded it.

It never modifies the capture or the bundle. It inspects sizes by character
count (measured, authoritative) and reports token counts as estimates built
from a fixed chars-per-token ratio (labelled ``est.`` everywhere). Sectioning
recognizes the OpenCode system-prompt taxonomy and degrades to an
``unmapped_system_message`` total when the markers belong to another harness;
error captures carry no usage facts and the analyzer says so.

Usage:
  python3 analyze_context_budget.py BUNDLE_DIR
  python3 analyze_context_budget.py --system-prompt A.md --transcript B.md
  python3 analyze_context_budget.py --request send.json --response recv.json
  python3 analyze_context_budget.py BUNDLE_DIR --json --top 20

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
UNMAPPED_SECTION = "unmapped_system_message"


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


def sectionize_system_text(text: str) -> tuple[dict, dict | None, list[str]]:
    """Split a system message body into OpenCode-taxonomy sections.

    Works on the raw system message or the rendered document alike. When no
    known anchor appears, every known section is zero and the whole text is
    reported under ``unmapped_system_message`` so totals stay measured.
    """
    errors: list[str] = []
    payload_start = text.find("You are an AI agent")
    payload_start = text.find("\n", payload_start) + 1 if payload_start >= 0 else 0

    code_mode = text.find("\n# Code Mode", payload_start)
    skills_start = text.find("<available_skills>")
    skills_end = text.find("</available_skills>")
    instr = text.find("Instructions from:")
    today = text.find("Today's date")
    env = text.find("<env>")

    if max(code_mode, skills_start, instr, today, env) < 0:
        # No taxonomy anchor from any harness: report one unmapped total
        # instead of misattributing the whole text to a known section.
        return ({UNMAPPED_SECTION: len(text), **{name: 0 for name in SECTION_ORDER}},
                None,
                errors)

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

    recognized = sum(sections.values())
    if not recognized:
        sections = {UNMAPPED_SECTION: len(text), **sections}

    return sections, catalog, errors


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

    sections, catalog, sect_errors = sectionize_system_text(text)
    return {
        "model": model,
        "system_message_chars": header_len,
        "sections": sections,
        "catalog": catalog,
        "errors": errors + sect_errors,
    }


def parse_transcript(text: str) -> dict:
    """Extract usage and latency facts from the rendered transcript."""
    out: dict = {
        "prompt_tokens": None,
        "completion_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": None,
        "cached_tokens": None,
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


def _find_thinking_key(node) -> str | None:
    """Locate a thinking/reasoning effort key anywhere in a request JSON."""
    if isinstance(node, dict):
        for key in ("thinkingLevel", "thinking_level", "reasoning_effort"):
            value = node.get(key)
            if isinstance(value, str) and value:
                return value
        for value in node.values():
            found = _find_thinking_key(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_thinking_key(item)
            if found:
                return found
    return None


def _content_chars(content) -> tuple[int, list[str]]:
    """Measured characters in a message content field."""
    errors: list[str] = []
    if isinstance(content, str):
        return len(content), errors
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                total += len(part["text"])
            else:
                errors.append("non-text content part; chars undercounted")
        return total, errors
    if content is None:
        return 0, errors
    errors.append("unmapped content shape; chars undercounted")
    return 0, errors


def parse_openai_request(json_text: str, request_bytes: int | None) -> dict:
    """Parse an OpenAI-compatible chat completion request JSON."""
    out: dict = {
        "model": None,
        "system_message_chars": None,
        "sections": {},
        "catalog": None,
        "tools_count": None,
        "request_body_bytes": request_bytes,
        "user_prompt_chars": None,
        "thinking_level": None,
        "errors": [],
    }
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        out["errors"].append(f"request JSON invalid: {exc}")
        return out
    if not isinstance(payload, dict):
        out["errors"].append("request JSON is not an object")
        return out

    model = payload.get("model")
    if isinstance(model, str):
        out["model"] = model
    if not isinstance(payload.get("messages"), list):
        out["errors"].append("request JSON has no messages array")
        return out

    system_parts: list[str] = []
    content_errors: list[str] = []
    for message in payload["messages"]:
        if not isinstance(message, dict):
            out["errors"].append("message entry is not an object")
            continue
        role = message.get("role")
        chars, part_errors = _content_chars(message.get("content"))
        content_errors.extend(part_errors)
        if role in ("system", "developer"):
            if isinstance(message.get("content"), str):
                system_parts.append(message["content"] + "\n")
            else:
                out["errors"].append(
                    "system message content not plain text; sectioning skipped "
                    f"for {chars} chars"
                )
        elif out["user_prompt_chars"] is None and isinstance(message.get("content"), str):
            out["user_prompt_chars"] = len(message["content"].strip())
    out["errors"].extend(content_errors[:5])

    system_text = "".join(system_parts)
    out["system_message_chars"] = len(system_text)
    if system_text:
        sections, catalog, sect_errors = sectionize_system_text(system_text)
        out["sections"] = sections
        out["catalog"] = catalog
        out["errors"].extend(sect_errors)
    else:
        out["sections"] = {UNMAPPED_SECTION: 0, **{name: 0 for name in SECTION_ORDER}}

    tools = payload.get("tools")
    if isinstance(tools, list):
        out["tools_count"] = len(tools)
    elif isinstance(payload.get("functions"), list):
        out["tools_count"] = len(payload["functions"])
    elif tools is not None:
        out["errors"].append("tools field is present but not an array")

    out["thinking_level"] = _find_thinking_key(payload)
    return out


def parse_openai_response(json_text: str) -> dict:
    """Parse an OpenAI-compatible response JSON for usage facts."""
    out: dict = {
        "prompt_tokens": None,
        "completion_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": None,
        "cached_tokens": None,
        "user_prompt_chars": None,
        "model": None,
        "errors": [],
    }
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        out["errors"].append(f"response JSON invalid: {exc}")
        return out
    if not isinstance(payload, dict):
        out["errors"].append("response JSON is not an object")
        return out
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        out["errors"].append("response JSON has no usage object (failed turn?)")
        return out
    for key, target in (
        ("prompt_tokens", "prompt_tokens"),
        ("completion_tokens", "completion_tokens"),
        ("total_tokens", "total_tokens"),
    ):
        value = usage.get(key)
        if isinstance(value, int):
            out[target] = value
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict) and isinstance(details.get("reasoning_tokens"), int):
        out["reasoning_tokens"] = details["reasoning_tokens"]
    prompt_details = usage.get("prompt_tokens_details")
    if isinstance(prompt_details, dict) and isinstance(
        prompt_details.get("cached_tokens"), int
    ):
        out["cached_tokens"] = prompt_details["cached_tokens"]
    if not isinstance(out["prompt_tokens"], int) and isinstance(
        usage.get("input_tokens"), int
    ):
        out["prompt_tokens"] = usage["input_tokens"]
        completion = usage.get("output_tokens")
        if isinstance(completion, int):
            out["completion_tokens"] = completion
    model = payload.get("model")
    if isinstance(model, str):
        out["model"] = model
    if out["prompt_tokens"] is None:
        out["errors"].append("usage line not found (error captures carry none)")
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
    unmapped = sp["sections"].get(UNMAPPED_SECTION, 0)
    if unmapped:
        levers.append(
            f"Unmapped system body: {_fmt(unmapped)} chars did not match a "
            "known section taxonomy — bill it as instruction fixed cost "
            "and read it before claiming any lever."
        )
    levers.append(
        "Harness fixed cost: code_mode_catalog "
        f"{_fmt(sp['sections'].get('code_mode_catalog', 0))} chars and "
        f"{usage.get('tools_count') or '?'} tool declarations belong to the "
        "harness and change with releases, not configuration."
    )
    cached = usage.get("cached_tokens")
    prompted = usage.get("prompt_tokens")
    if isinstance(cached, int) and prompted and 0 < cached < prompted:
        levers.append(
            f"Provider cache served {cached} of {prompted} prompt tokens — "
            "protect that discount by keeping the stable prefix byte-stable; "
            "trim the tail, never churn the head."
        )
    levers.append(
        "Caching: keep section order and the leading sections byte-stable "
        "when trimming (date/env last) so repeated turns share one "
        "cacheable prefix."
    )
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
    return _assemble(sp, usage, top=top)


def analyze_pair(
    request_text: str,
    response_text: str,
    request_bytes: int | None = None,
    latency_ms: float | None = None,
    top: int = DEFAULT_TOP,
) -> dict:
    """Analyze one turn from a raw OpenAI-compatible request/response pair."""
    sp = parse_openai_request(request_text, request_bytes)
    usage = parse_openai_response(response_text)
    usage["request_body_bytes"] = sp.get("request_body_bytes")
    usage["tools_count"] = sp.get("tools_count")
    usage["thinking_level"] = sp.get("thinking_level")
    usage["user_prompt_chars"] = sp.get("user_prompt_chars") or usage.get(
        "user_prompt_chars"
    )
    if latency_ms is not None:
        usage["server_ms"] = float(latency_ms)
    return _assemble(sp, usage, top=top)


def _assemble(sp: dict, usage: dict, top: int) -> dict:
    catalog = sp.get("catalog") or {}
    sections = {
        name: {
            "chars": chars,
            "est_tokens": _est_tokens(chars, DEFAULT_RATIO),
        }
        for name, chars in sp["sections"].items()
    }
    return {
        "model": sp["model"] or usage.get("model"),
        "system_message_chars": sp.get("system_message_chars"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": usage.get("reasoning_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "cached_tokens": usage.get("cached_tokens"),
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
        "errors": sp["errors"] + list(usage.get("errors", [])),
    }


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
    parser.add_argument("--request", type=Path,
                        help="OpenAI-compatible request JSON")
    parser.add_argument("--response", type=Path,
                        help="OpenAI-compatible response JSON")
    parser.add_argument("--latency-ms", type=float,
                        help="recorded server time for the request/response pair")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.request or args.response:
        if not (args.request and args.response):
            parser.error("--request and --response go together")
            return 2
        for path in (args.request, args.response):
            if not path.exists():
                print(f"error: {path} not found", file=sys.stderr)
                return 2
        try:
            result = analyze_pair(
                args.request.read_text(encoding="utf-8"),
                args.response.read_text(encoding="utf-8"),
                request_bytes=args.request.stat().st_size,
                latency_ms=args.latency_ms,
                top=args.top,
            )
        except (ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            sys.stdout.write(render_markdown(result))
        return 0

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
        parser.error("give a bundle directory, a request/response pair, or "
                     "both --system-prompt and --transcript")
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
