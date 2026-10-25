#!/usr/bin/env python3
"""Render a CLIProxyAPI request-log capture as a readable Markdown bundle.

Supported input
---------------
A capture written by CLIProxyAPI's `request-log` setting for
`POST /v1/chat/completions`, for example:

    v1-chat-completions-2026-10-25T233717-a2056a8e.log

The capture may describe a successful exchange, a non-streamed JSON reply, a
tool-call turn, or a failure that never reached a model. Other endpoints and
capture shapes are rejected with an explicit message rather than guessed at.

Outputs
-------
    <stem>.md                    readable transcript
    <stem>-system-prompt.md      system/developer context, verbatim
    <stem>-raw-payloads.md       every header, body, and stream payload

The transcript is presentation only. The raw reference keeps request bodies as
pretty-printed JSON (values unchanged) and stream payloads byte-for-byte.

Usage
-----
    python3 scripts/render_request_log.py CAPTURE.log
    python3 scripts/render_request_log.py CAPTURE.log --output-dir /tmp/readable
    python3 scripts/render_request_log.py CAPTURE.log --force

Exit codes: 0 success | 2 usage error | 3 unsupported capture | 4 verification failure
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

SECTION_RE = re.compile(r"^=== (.+?) ===$")
API_REQUEST_RE = re.compile(r"^API REQUEST(?: (\d+))?$")
API_RESPONSE_RE = re.compile(r"^API RESPONSE(?: (\d+))?$")
TIMESTAMP_RE = re.compile(r"^(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)$")
BACKTICK_RUN_RE = re.compile(r"`+")
TOOL_OUTPUT_PREVIEW_LIMIT = 2_000


class CaptureError(Exception):
    """The file is not a capture this renderer can read."""


class UnsupportedCapture(CaptureError):
    """The capture is valid CLIProxyAPI output but not a supported shape."""


class VerificationError(Exception):
    """A rendered document failed its content-fidelity check."""


@dataclass
class Section:
    name: str
    lines: list[str]


@dataclass
class RequestBlock:
    meta: list[tuple[str, str]]
    headers: list[tuple[str, str]]
    body: str


@dataclass
class ResponseBlock:
    timestamp: str
    status: str
    headers: list[tuple[str, str]]
    body: str


@dataclass
class Attempt:
    number: int | None
    request: RequestBlock | None = None
    response: ResponseBlock | None = None


@dataclass
class Completion:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict[str, Any] | None = None
    choice_count: int = 1
    event_count: int = 0
    streamed: bool = False
    error: dict[str, Any] | None = None
    error_body: str = ""


@dataclass
class RenderedBundle:
    transcript: str
    system_prompt: str | None
    raw_reference: str
    expected_json_blocks: list[Any]
    raw_stream_payloads: list[str]
    thinking: str
    reply: str
    system_contents: list[str] = field(default_factory=list)


@dataclass
class Capture:
    request_info: dict[str, str]
    request_headers: list[tuple[str, str]]
    request_body: dict[str, Any]
    attempts: list[Attempt]
    downstream: ResponseBlock
    leftovers: list[Section]


def split_sections(text: str) -> list[Section]:
    sections: list[Section] = []
    name: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        match = SECTION_RE.match(line)
        if match:
            if name is not None:
                sections.append(Section(name, lines))
            name = match.group(1)
            lines = []
        elif name is not None:
            lines.append(line)
    if name is not None:
        sections.append(Section(name, lines))
    if not sections:
        raise CaptureError("no '=== SECTION ===' markers found; is this a CLIProxyAPI request log?")
    return sections


def parse_key_value_lines(lines: Iterable[str], context: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for line in lines:
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise CaptureError(f"{context}: expected 'Key: Value', got {line!r}")
        if value.startswith(" "):
            value = value.removeprefix(" ")
        pairs.append((key, value))
    return pairs


def strip_blank_edges(lines: Sequence[str]) -> list[str]:
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return list(lines[start:end])


def split_at_label(lines: Sequence[str], label: str) -> tuple[list[str], list[str]]:
    for index, line in enumerate(lines):
        if line.strip() == label:
            return list(lines[:index]), list(lines[index + 1 :])
    return list(lines), []


def parse_request_block(section: Section) -> RequestBlock:
    before_headers, after_headers = split_at_label(section.lines, "Headers:")
    header_lines, body_lines = split_at_label(after_headers, "Body:")
    meta = parse_key_value_lines(before_headers, section.name)
    headers = parse_key_value_lines(header_lines, section.name)
    body = "\n".join(body_lines).strip("\n")
    return RequestBlock(meta=meta, headers=headers, body=body)


def parse_response_block(section: Section) -> ResponseBlock:
    lines = strip_blank_edges(section.lines)
    index = 0
    timestamp = ""
    if index < len(lines) and lines[index].startswith("Timestamp: "):
        timestamp = lines[index].removeprefix("Timestamp: ")
        index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    # An upstream failure can be written as a bare error object directly after
    # the timestamp, with no status, headers, or "Body:" label.
    if index < len(lines) and lines[index].lstrip().startswith(("{", "[")):
        return ResponseBlock(
            timestamp=timestamp,
            status="",
            headers=[],
            body="\n".join(lines[index:]).strip("\n"),
        )
    status = ""
    if index < len(lines) and lines[index].startswith("Status: "):
        status = lines[index].removeprefix("Status: ")
        index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index < len(lines) and lines[index].lstrip().startswith(("{", "[")):
        return ResponseBlock(
            timestamp=timestamp,
            status=status,
            headers=[],
            body="\n".join(lines[index:]).strip("\n"),
        )
    if index < len(lines) and lines[index].strip() == "Headers:":
        index += 1
    header_lines: list[str] = []
    while index < len(lines) and lines[index].strip():
        header_lines.append(lines[index])
        index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index < len(lines) and lines[index].strip() == "Body:":
        index += 1
    body = "\n".join(lines[index:]).strip("\n")
    return ResponseBlock(
        timestamp=timestamp,
        status=status,
        headers=parse_key_value_lines(header_lines, section.name),
        body=body,
    )


def group_attempts(sections: Sequence[Section]) -> list[Attempt]:
    attempts: dict[int | None, Attempt] = {}
    for section in sections:
        request_match = API_REQUEST_RE.match(section.name)
        if request_match:
            number = int(request_match.group(1)) if request_match.group(1) else None
            attempts.setdefault(number, Attempt(number)).request = parse_request_block(section)
            continue
        response_match = API_RESPONSE_RE.match(section.name)
        if response_match:
            number = int(response_match.group(1)) if response_match.group(1) else None
            attempts.setdefault(number, Attempt(number)).response = parse_response_block(section)
    ordered = sorted(attempts.values(), key=lambda item: (item.number is None, item.number or 0))
    return ordered


def parse_sse(text: str) -> tuple[list[str], list[str]]:
    payloads: list[str] = []
    ignored: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            current.append(line[len("data:") :].lstrip())
        elif not line.strip():
            if current:
                payloads.append("\n".join(current))
                current = []
        elif line.strip():
            ignored.append(line)
    if current:
        payloads.append("\n".join(current))
    return payloads, ignored


def parse_timestamp(value: str) -> datetime | None:
    match = TIMESTAMP_RE.match(value)
    if not match:
        return None
    prefix, fraction, suffix = match.groups()
    fraction = (fraction or ".0")[:7].ljust(2, "0")
    if suffix.endswith("Z"):
        suffix = suffix[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(f"{prefix}{fraction}{suffix}")
    except ValueError:
        return None


def format_moment(value: datetime) -> str:
    offset = value.strftime("%z")
    if offset:
        offset = f"{offset[:3]}:{offset[3:]}"
    return f"{value.strftime('%d %B %Y at %H:%M:%S')} {offset}".strip()


def format_duration(seconds: float) -> str:
    return f"{seconds:.1f} s"


def inline_code(value: str) -> str:
    marker = "`" * (max((len(run) for run in BACKTICK_RUN_RE.findall(value)), default=0) + 1)
    return f"{marker}{value}{marker}"


def fence(content: str, info: str = "text") -> str:
    longest = max((len(run) for run in BACKTICK_RUN_RE.findall(content)), default=0)
    marker = "`" * max(3, longest + 1)
    trailing = "" if content.endswith("\n") or not content else "\n"
    return f"{marker}{info}\n{content}{trailing}{marker}"


def blockquote(text: str) -> str:
    lines = text.strip("\n").splitlines()
    return "\n".join(f"> {line}" if line else ">" for line in lines)


def table_cell(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("|", "\\|")
    return escaped.replace("\r\n", "<br>").replace("\n", "<br>")


def render_table(rows: Iterable[tuple[str, str]], headings: tuple[str, str] = ("Field", "Value")) -> str:
    lines = [f"| {headings[0]} | {headings[1]} |", "| --- | --- |"]
    for key, value in rows:
        lines.append(f"| {table_cell(key)} | {table_cell(value)} |")
    return "\n".join(lines)


def pretty_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
            else:
                raise UnsupportedCapture(
                    "message content contains a non-text part; only plain text and text parts "
                    "are supported (the raw reference would preserve it, but the transcript cannot)"
                )
        return "".join(texts)
    raise UnsupportedCapture(f"unsupported message content type: {type(content).__name__}")


def json_object(value: Any, context: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise UnsupportedCapture(f"{context} is not a JSON object")
    return value


def text_value(value: Any, context: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise UnsupportedCapture(f"{context} is not text")
    return value


def merge_tool_calls(chunks: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for position, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            raise UnsupportedCapture("tool call is not a JSON object")
        index = chunk.get("index", position)
        if not isinstance(index, int):
            raise UnsupportedCapture("tool call index is not an integer")
        entry = merged.setdefault(index, {"id": "", "type": "", "function": {"name": "", "arguments": ""}})
        if chunk.get("id"):
            entry["id"] = chunk["id"]
        if chunk.get("type"):
            entry["type"] = chunk["type"]
        function = json_object(chunk.get("function"), "tool call function")
        if function.get("name"):
            entry["function"]["name"] = function["name"]
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            current = entry["function"]["arguments"]
            entry["function"]["arguments"] = (current if isinstance(current, str) else "") + arguments
        elif arguments:
            entry["function"]["arguments"] = arguments
    return [merged[index] for index in sorted(merged)]


def decode_completion(response: ResponseBlock) -> Completion:
    content_type = dict(response.headers).get("Content-Type", "")
    body = response.body.strip()
    if not body:
        return Completion(error={"message": "empty response body"}, error_body="")
    if "text/event-stream" in content_type or body.startswith("data:"):
        payloads, _ = parse_sse(response.body)
        completion = Completion(
            streamed=True,
            event_count=sum(1 for payload in payloads if payload.strip() != "[DONE]"),
        )
        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        tool_chunks: list[dict[str, Any]] = []
        for payload in payloads:
            if payload.strip() == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                raise UnsupportedCapture(f"stream payload is not JSON: {payload[:120]!r}")
            if not isinstance(event, dict):
                raise UnsupportedCapture("stream payload is not a JSON object")
            if event.get("error"):
                completion.error = (
                    event["error"] if isinstance(event["error"], dict) else {"message": str(event["error"])}
                )
                completion.error_body = payload
                return completion
            usage = event.get("usage")
            if usage is not None:
                completion.usage = json_object(usage, "stream usage")
            choices_value = event.get("choices") or []
            if not isinstance(choices_value, list):
                raise UnsupportedCapture("stream choices is not a JSON array")
            completion.choice_count = max(completion.choice_count, len(choices_value))
            if not choices_value:
                continue
            choice = json_object(choices_value[0], "stream choice")
            delta = json_object(
                choice.get("delta") or choice.get("message") or {},
                "stream delta",
            )
            reasoning_content = delta.get("reasoning_content")
            if reasoning_content is not None:
                reasoning_parts.append(
                    text_value(reasoning_content, "delta.reasoning_content")
                )
            reasoning = delta.get("reasoning")
            if reasoning is not None:
                reasoning_parts.append(text_value(reasoning, "delta.reasoning"))
            content = delta.get("content")
            if content is not None:
                content_parts.append(text_value(content, "delta.content"))
            tool_calls_value = delta.get("tool_calls") or []
            if not isinstance(tool_calls_value, list):
                raise UnsupportedCapture("delta.tool_calls is not a JSON array")
            tool_chunks.extend(tool_calls_value)
            if choice.get("finish_reason"):
                completion.finish_reason = str(choice["finish_reason"])
        completion.reasoning = "".join(reasoning_parts)
        completion.content = "".join(content_parts)
        completion.tool_calls = merge_tool_calls(tool_chunks)
        return completion

    try:
        body_json = json.loads(body)
    except json.JSONDecodeError:
        raise UnsupportedCapture(
            "response is neither SSE nor JSON; re-run with the raw log for inspection"
        )
    if isinstance(body_json, dict) and body_json.get("error"):
        error = body_json["error"]
        return Completion(
            error=error if isinstance(error, dict) else {"message": str(error)},
            error_body=body,
        )
    if not isinstance(body_json, dict) or not body_json.get("choices"):
        raise UnsupportedCapture("JSON response has no choices and no error object")
    choices = body_json["choices"]
    if not isinstance(choices, list) or not choices:
        raise UnsupportedCapture("JSON response choices is not a non-empty array")
    choice = json_object(choices[0], "response choice")
    message = json_object(choice.get("message"), "response message")
    content = text_value(message.get("content"), "message.content")
    reasoning = text_value(
        message.get("reasoning_content") or message.get("reasoning"),
        "message reasoning",
    )
    usage = body_json.get("usage")
    if usage is not None and not isinstance(usage, dict):
        raise UnsupportedCapture("response usage is not a JSON object")
    tool_calls = message.get("tool_calls") or []
    if not isinstance(tool_calls, list):
        raise UnsupportedCapture("message.tool_calls is not a JSON array")
    completion = Completion(
        content=content,
        reasoning=reasoning,
        usage=usage,
        finish_reason=str(choice.get("finish_reason") or ""),
        choice_count=len(choices),
    )
    completion.tool_calls = merge_tool_calls(tool_calls)
    return completion


def error_details(completion: Completion, status: str) -> tuple[str, str, str]:
    error = completion.error or {}
    code = str(error.get("code") or error.get("type") or "")
    message = str(error.get("message") or "").strip()
    nested = error.get("last_upstream_error")
    if isinstance(nested, str):
        nested_status = ""
        nested_message = ""
        try:
            nested_json = json.loads(nested)
        except json.JSONDecodeError:
            nested_json = None
        if isinstance(nested_json, dict):
            nested_error = nested_json.get("error") or {}
            if isinstance(nested_error, dict):
                nested_status = str(nested_error.get("status") or nested_error.get("code") or "").strip()
                nested_message = str(nested_error.get("message") or "").strip()
        else:
            # CLIProxyAPI truncates long upstream errors in the capture, which
            # leaves the nested JSON unterminated. Pull the readable fields out
            # without pretending the fragment is valid JSON.
            message_match = re.search(r'"message"\s*:\s*"([^"]*)"', nested)
            status_match = re.search(r'"status"\s*:\s*"([^"]*)"', nested)
            if message_match:
                nested_message = message_match.group(1)
            if status_match:
                nested_status = status_match.group(1)
        if nested_message:
            wrapper = re.split(r" \(last error:", message, maxsplit=1)[0].strip()
            detail = (
                f"upstream reported {nested_status}: {nested_message}"
                if nested_status
                else nested_message
            )
            message = f"{wrapper} — {detail}" if wrapper else detail
    fallback = completion.error_body.splitlines()[0][:80] if completion.error_body else ""
    label = code or fallback or "request failed"
    return status or "?", label, message


def render_usage(usage: dict[str, Any] | None) -> list[str]:
    if not usage:
        return []
    rows: list[tuple[str, str]] = []
    if isinstance(usage.get("prompt_tokens"), int):
        rows.append(("Prompt tokens", f"{usage['prompt_tokens']:,}"))
    if isinstance(usage.get("completion_tokens"), int):
        rows.append(("Completion tokens", f"{usage['completion_tokens']:,}"))
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict) and isinstance(details.get("reasoning_tokens"), int):
        rows.append(("Reasoning tokens", f"{details['reasoning_tokens']:,}"))
    if isinstance(usage.get("total_tokens"), int):
        rows.append(("Total tokens", f"{usage['total_tokens']:,}"))
    return ["", "**Usage**", "", render_table(rows), ""] if rows else []


def render_tool_calls(
    tool_calls: Sequence[dict[str, Any]],
    heading: str = "### Assistant — tool calls",
) -> list[str]:
    if not tool_calls:
        return []
    lines = [heading, ""]
    for number, call in enumerate(tool_calls, start=1):
        function = call.get("function") or {}
        name = function.get("name") or "unknown"
        arguments = function.get("arguments") or ""
        lines.append(f"**{number}. `{name}`**")
        lines.append("")
        if isinstance(arguments, str) and arguments:
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                lines.append(fence(arguments))
            else:
                lines.append(fence(pretty_json(parsed), "json"))
            lines.append("")
        elif arguments:
            lines.append(fence(pretty_json(arguments), "json"))
            lines.append("")
        else:
            lines.append("_No arguments were captured._")
            lines.append("")
    return lines


def message_heading(role: str, index: int) -> str:
    if role == "user":
        return "### User"
    if role == "assistant":
        return f"### Assistant (message {index})"
    if role == "tool":
        return f"### Tool result (message {index})"
    return f"### {role.title()} (message {index})"


def render_history(messages: Sequence[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for index, message in enumerate(messages, start=1):
        role = str(message.get("role") or "unknown")
        if role in {"system", "developer"}:
            continue
        text = message_text(message)
        lines.append(message_heading(role, index))
        lines.append("")
        if role == "tool" and message.get("tool_call_id"):
            lines.append(f"Tool call ID: {inline_code(str(message['tool_call_id']))}")
            lines.append("")
        if text:
            if role == "tool":
                if len(text) > TOOL_OUTPUT_PREVIEW_LIMIT:
                    lines.append(
                        f"_Tool output: {len(text):,} characters. Showing the first "
                        f"{TOOL_OUTPUT_PREVIEW_LIMIT:,}; the complete value is in the raw reference._"
                    )
                    lines.append("")
                    lines.append(fence(text[:TOOL_OUTPUT_PREVIEW_LIMIT]))
                else:
                    lines.append(fence(text))
                lines.append("")
            else:
                lines.append(text)
                lines.append("")
        elif not (role == "assistant" and message.get("tool_calls")):
            lines.append("_No text content._")
            lines.append("")
        if role == "assistant" and isinstance(message.get("tool_calls"), list):
            calls = merge_tool_calls(message["tool_calls"])
            lines.extend(
                render_tool_calls(
                    calls,
                    heading=f"### Assistant (message {index}) — tool calls",
                )
            )
    return lines


def collect_system_messages(messages: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    collected: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "")
        if role not in {"system", "developer"}:
            continue
        collected.append({"role": role, "content": message_text(message)})
    return collected


def count_label(count: int, singular: str, plural_form: str | None = None) -> str:
    word = singular if count == 1 else (plural_form or f"{singular}s")
    return f"{count} {word}"


def system_prompt_document(
    messages: Sequence[dict[str, str]],
    model: str,
    started: datetime | None,
    transcript_name: str,
) -> str:
    total = sum(len(message["content"]) for message in messages)
    lines = ["# System prompt", ""]
    moment = f" on {format_moment(started)}" if started else ""
    lines.append(
        f"{count_label(len(messages), 'system/developer message')}, {total:,} characters in "
        f"total, sent to `{model}`{moment}. Reproduced verbatim; the surrounding document adds "
        f"headings, the message text itself is unchanged. Back to the "
        f"[transcript]({transcript_name})."
    )
    lines.append("")
    for number, message in enumerate(messages, start=1):
        lines.append(f"## Message {number} ({message['role']})")
        lines.append("")
        if message["content"]:
            lines.append(message["content"])
        else:
            lines.append("_Empty message._")
        if not message["content"].endswith("\n"):
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def attempt_summary(attempts: Sequence[Attempt]) -> str:
    if not attempts:
        return "none recorded"
    parts = []
    for attempt in attempts:
        label = f"attempt {attempt.number}" if attempt.number is not None else "attempt (unnumbered)"
        if attempt.response and attempt.response.status:
            parts.append(f"{label}: HTTP {attempt.response.status}")
        elif attempt.response:
            parts.append(f"{label}: response recorded without a status")
        elif attempt.request:
            parts.append(f"{label}: request sent, no response recorded")
        else:
            parts.append(f"{label}: empty")
    return "; ".join(parts)


def request_parameter_lines(body: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if "stream" in body:
        lines.append(f"- Streamed: {'yes' if body['stream'] else 'no'}.")
    if isinstance(body.get("stream_options"), dict) and "include_usage" in body["stream_options"]:
        lines.append(
            f"- Usage included in the stream: "
            f"{'yes' if body['stream_options']['include_usage'] else 'no'}."
        )
    if isinstance(body.get("max_completion_tokens"), int):
        lines.append(f"- Maximum completion tokens: {body['max_completion_tokens']:,}.")
    elif isinstance(body.get("max_tokens"), int):
        lines.append(f"- Maximum completion tokens: {body['max_tokens']:,}.")
    if body.get("reasoning_effort"):
        lines.append(f"- Reasoning effort: {inline_code(str(body['reasoning_effort']))}.")
    if isinstance(body.get("tools"), list):
        names = [
            str(tool.get("function", {}).get("name"))
            for tool in body["tools"]
            if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
        ]
        suffix = f" — {', '.join(names)}" if names else ""
        lines.append(f"- Tools declared: {len(body['tools'])}{suffix}.")
    return lines


def render_documents(
    source: Path,
    request_info: dict[str, str],
    request_headers: list[tuple[str, str]],
    request_body: dict[str, Any],
    attempts: Sequence[Attempt],
    downstream: ResponseBlock,
    completion: Completion,
    leftover_sections: Sequence[Section],
) -> RenderedBundle:
    model = str(request_body.get("model") or "unknown model")
    messages = request_body.get("messages") or []
    if not isinstance(messages, list):
        raise UnsupportedCapture("request body 'messages' is not a list")
    system_messages = collect_system_messages(messages)
    started = parse_timestamp(request_info.get("Timestamp", ""))
    response_times = [
        parse_timestamp(attempt.response.timestamp)
        for attempt in attempts
        if attempt.response and attempt.response.timestamp
    ]
    response_times = [moment for moment in response_times if moment is not None]
    finished = response_times[-1] if response_times else None
    duration = (
        format_duration((finished - started).total_seconds())
        if started and finished and finished >= started
        else None
    )
    endpoint = f"{request_info.get('Method', 'POST')} {request_info.get('URL', '/v1/chat/completions')}"
    failed = completion.error is not None or bool(
        downstream.status and not downstream.status.startswith("2")
    )
    status, error_code, error_message = error_details(completion, downstream.status)

    expected_json_blocks: list[Any] = [request_body]
    raw_payloads: list[str] = []
    raw_parts: list[str] = []
    raw_parts.append("# Raw payload reference")
    raw_parts.append("")
    raw_parts.append(
        f"Complete request and response data for the [transcript]({source.stem}.md). Request "
        "bodies are pretty-printed JSON; text values inside them are unchanged. Stream payloads "
        "are the exact `data:` values from the log."
    )
    raw_parts.append("")
    raw_parts.append("## Downstream request")
    raw_parts.append("")
    raw_parts.append("### Metadata")
    raw_parts.append("")
    raw_parts.append(render_table(request_info.items()))
    raw_parts.append("")
    raw_parts.append("### Headers")
    raw_parts.append("")
    raw_parts.append(render_table(request_headers, ("Header", "Value")))
    raw_parts.append("")
    raw_parts.append("### Body")
    raw_parts.append("")
    raw_parts.append(fence(pretty_json(request_body), "json"))
    raw_parts.append("")
    for attempt in attempts:
        label = f"Upstream attempt {attempt.number}" if attempt.number is not None else "Upstream attempt"
        raw_parts.append(f"## {label}")
        raw_parts.append("")
        if attempt.request:
            raw_parts.append("### Request metadata")
            raw_parts.append("")
            raw_parts.append(render_table(attempt.request.meta))
            raw_parts.append("")
            raw_parts.append("### Request headers")
            raw_parts.append("")
            raw_parts.append(render_table(attempt.request.headers, ("Header", "Value")))
            raw_parts.append("")
            raw_parts.append("### Request body")
            raw_parts.append("")
            if attempt.request.body:
                try:
                    request_json = json.loads(attempt.request.body)
                except json.JSONDecodeError:
                    raw_parts.append(fence(attempt.request.body))
                else:
                    expected_json_blocks.append(request_json)
                    raw_parts.append(fence(pretty_json(request_json), "json"))
            else:
                raw_parts.append("_No request body was captured._")
            raw_parts.append("")
        else:
            raw_parts.append("_No upstream request was sent._")
            raw_parts.append("")
        if attempt.response:
            raw_parts.append("### Response")
            raw_parts.append("")
            rows = []
            if attempt.response.timestamp:
                rows.append(("Timestamp", attempt.response.timestamp))
            rows.append(("Status", attempt.response.status or "not recorded"))
            raw_parts.append(render_table(rows))
            raw_parts.append("")
            raw_parts.append("#### Response headers")
            raw_parts.append("")
            raw_parts.append(render_table(attempt.response.headers, ("Header", "Value")))
            raw_parts.append("")
            raw_parts.append("#### Response body")
            raw_parts.append("")
            raw_parts.extend(render_raw_body(attempt.response.body, raw_payloads, expected_json_blocks))
            raw_parts.append("")
    raw_parts.append("## Downstream response")
    raw_parts.append("")
    rows = [("Status", downstream.status or "not recorded")]
    raw_parts.append(render_table(rows))
    raw_parts.append("")
    raw_parts.append("### Headers")
    raw_parts.append("")
    raw_parts.append(render_table(downstream.headers, ("Header", "Value")))
    raw_parts.append("")
    raw_parts.append("### Body")
    raw_parts.append("")
    raw_parts.extend(render_raw_body(downstream.body, raw_payloads, expected_json_blocks))
    raw_parts.append("")
    if leftover_sections:
        raw_parts.append("## Other log sections")
        raw_parts.append("")
        for section in leftover_sections:
            raw_parts.append(f"### {section.name}")
            raw_parts.append("")
            raw_parts.append(fence("\n".join(strip_blank_edges(section.lines))))
            raw_parts.append("")

    transcript: list[str] = []
    if failed:
        transcript.append(f"# Failed request: {model}")
        transcript.append("")
        context = f" on {format_moment(started)}" if started else ""
        transcript.append(
            f"A {endpoint} request{context} returned HTTP {status}. The gateway reported "
            f"{inline_code(error_code)}; no model reply was produced."
        )
        transcript.append("")
        if error_message:
            transcript.append(blockquote(error_message))
            transcript.append("")
        transcript.append("## Request")
        transcript.append("")
        transcript.extend(render_history(messages))
    else:
        transcript.append(f"# Chat transcript: {model}")
        transcript.append("")
        context = f" on {format_moment(started)}" if started else ""
        duration_text = f" in {duration}" if duration else ""
        transcript.append(
            f"A {endpoint} request{context} completed{duration_text}. The exchange below shows "
            "the conversation sent to the model and the reply it produced."
        )
        transcript.append("")
        transcript.append("## The exchange")
        transcript.append("")
        transcript.extend(render_history(messages))
        if completion.reasoning:
            transcript.append("### Assistant — thinking")
            transcript.append("")
            transcript.append(blockquote(completion.reasoning))
            transcript.append("")
        transcript.append("### Assistant — reply")
        transcript.append("")
        if completion.content:
            transcript.append(completion.content)
        else:
            transcript.append("_The model returned no text._")
        transcript.append("")
        if completion.tool_calls:
            transcript.extend(render_tool_calls(completion.tool_calls))
        if completion.choice_count > 1:
            transcript.append(
                f"_This capture contains {completion.choice_count} choices; the transcript shows "
                "choice 0. Every choice is preserved in the raw reference._"
            )
            transcript.append("")

    transcript.append("## Request details")
    transcript.append("")
    transcript.append(f"- Endpoint: {inline_code(endpoint)}.")
    if request_info.get("Version"):
        transcript.append(f"- Proxy version: {request_info['Version']}.")
    session = dict(request_headers).get("X-Session-Id")
    if session:
        transcript.append(f"- Session: {inline_code(session)}.")
    transcript.append(f"- Model: {inline_code(model)}.")
    transcript.extend(request_parameter_lines(request_body))
    if system_messages:
        total_system = sum(len(item["content"]) for item in system_messages)
        transcript.append(
            f"- System prompt: {count_label(len(system_messages), 'message')}, "
            f"{total_system:,} characters — "
            f"[read the full text]({source.stem}-system-prompt.md)."
        )
    else:
        transcript.append("- System prompt: none captured.")
    transcript.append("")
    transcript.append("## Upstream")
    transcript.append("")
    upstream_url = ""
    if attempts and attempts[0].request:
        upstream_url = dict(attempts[0].request.meta).get("Upstream URL", "")
    if upstream_url:
        transcript.append(f"- URL: {upstream_url}.")
    transcript.append(f"- Attempts: {attempt_summary(attempts)}.")
    if not any(attempt.request for attempt in attempts):
        transcript.append("- No upstream request section was written; the gateway rejected the request before dispatch.")
    transcript.append("")
    transcript.append("## Response")
    transcript.append("")
    transcript.append(f"- HTTP {status}.")
    if completion.finish_reason:
        transcript.append(f"- Finish reason: {inline_code(completion.finish_reason)}.")
    if completion.streamed:
        transcript.append(f"- Stream events: {completion.event_count}.")
    transcript.extend(render_usage(completion.usage))
    if failed:
        transcript.append(f"- Error: {inline_code(error_code)}.")
    else:
        transcript.append(f"- Model reply: {len(completion.content):,} characters.")
        if completion.reasoning:
            transcript.append(f"- Model thinking: {len(completion.reasoning):,} characters.")
        if completion.tool_calls:
            transcript.append(f"- Tool calls: {len(completion.tool_calls)}.")
    transcript.append("")
    transcript.append("## Full data")
    transcript.append("")
    transcript.append(
        f"Complete headers, request bodies, and every raw stream payload are in "
        f"[the raw reference]({source.stem}-raw-payloads.md)."
    )
    transcript.append("")

    system_doc = (
        system_prompt_document(system_messages, model, started, f"{source.stem}.md")
        if system_messages
        else None
    )
    return RenderedBundle(
        transcript="\n".join(transcript),
        system_prompt=system_doc,
        raw_reference="\n".join(raw_parts),
        expected_json_blocks=expected_json_blocks,
        raw_stream_payloads=raw_payloads,
        thinking=completion.reasoning,
        reply=completion.content,
        system_contents=[message["content"] for message in system_messages],
    )


def render_raw_body(body: str, payloads: list[str], expected_json_blocks: list[Any]) -> list[str]:
    if not body.strip():
        return ["_No response body was captured._"]
    payload_lines, ignored = parse_sse(body)
    content_type_hint = body.lstrip().startswith("data:")
    if payload_lines and (content_type_hint or len(payload_lines) > 1):
        lines: list[str] = []
        for number, payload in enumerate(payload_lines, start=1):
            lines.append(f"##### Event {number}")
            lines.append("")
            if payload.strip() == "[DONE]":
                lines.append(fence("[DONE]"))
                payloads.append("[DONE]")
            else:
                payloads.append(payload)
                try:
                    parsed_payload = json.loads(payload)
                except json.JSONDecodeError:
                    lines.append(fence(payload))
                else:
                    lines.append(fence(payload, "json"))
                    expected_json_blocks.append(parsed_payload)
            lines.append("")
        if ignored:
            lines.append("##### Non-data SSE lines")
            lines.append("")
            lines.append(fence("\n".join(ignored)))
            lines.append("")
        return lines
    try:
        body_json = json.loads(body)
    except json.JSONDecodeError:
        return [fence(body)]
    expected_json_blocks.append(body_json)
    return [fence(pretty_json(body_json), "json")]


def extract_json_fences(text: str) -> list[Any]:
    blocks: list[Any] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = re.match(r"^(`{3,})([A-Za-z0-9_-]*)\s*$", lines[index])
        if not match:
            index += 1
            continue
        marker = match.group(1)
        info = match.group(2)
        start = index + 1
        index = start
        while index < len(lines) and not (
            len(lines[index]) >= len(marker) and set(lines[index]) == {"`"} and len(lines[index]) == len(marker)
        ):
            index += 1
        if info == "json":
            blocks.append(json.loads("\n".join(lines[start:index])))
        index += 1
    return blocks


def verify_bundle(
    transcript_path: Path,
    raw_path: Path,
    bundle: RenderedBundle,
    system_path: Path | None = None,
) -> None:
    raw_text = raw_path.read_text(encoding="utf-8")
    transcript_text = transcript_path.read_text(encoding="utf-8")

    rendered_json = extract_json_fences(raw_text)
    if rendered_json != bundle.expected_json_blocks:
        raise VerificationError(
            f"JSON payloads in {raw_path.name} do not match the capture "
            f"({len(rendered_json)} rendered, {len(bundle.expected_json_blocks)} expected)"
        )
    for payload in bundle.raw_stream_payloads:
        if payload == "[DONE]":
            if "```text\n[DONE]\n```" not in raw_text:
                raise VerificationError("the [DONE] terminator is missing from the raw reference")
        elif payload not in raw_text:
            raise VerificationError("a stream payload is missing from the raw reference")

    if bundle.system_contents:
        if system_path is None or not system_path.is_file():
            raise VerificationError("the system prompt document is missing")
        system_text = system_path.read_text(encoding="utf-8")
        for number, content in enumerate(bundle.system_contents, start=1):
            if content:
                if content not in system_text:
                    raise VerificationError(
                        f"system message {number} does not match the captured text"
                    )
            elif f"## Message {number} " not in system_text:
                raise VerificationError(f"empty system message {number} is missing its heading")

    if bundle.thinking:
        expected = "### Assistant — thinking\n\n" + blockquote(bundle.thinking) + "\n"
        if expected not in transcript_text:
            raise VerificationError("the thinking block does not match the captured reasoning")
    if bundle.reply:
        expected = "### Assistant — reply\n\n" + bundle.reply + "\n"
        if expected not in transcript_text:
            raise VerificationError("the reply block does not match the captured reply")


def load_capture(path: Path) -> Capture:
    text = path.read_text(encoding="utf-8")
    sections = split_sections(text)
    by_name: dict[str, Section] = {}
    for section in sections:
        if section.name in by_name:
            raise UnsupportedCapture(f"duplicate section {section.name!r}")
        by_name[section.name] = section
    for required in ("REQUEST INFO", "HEADERS", "REQUEST BODY", "RESPONSE"):
        if required not in by_name:
            raise UnsupportedCapture(f"missing required section {required!r}")

    request_info = dict(parse_key_value_lines(by_name["REQUEST INFO"].lines, "REQUEST INFO"))
    if request_info.get("URL") != "/v1/chat/completions":
        raise UnsupportedCapture(
            f"only /v1/chat/completions captures are supported; this one is "
            f"{request_info.get('URL', 'unknown')!r}"
        )
    request_headers = parse_key_value_lines(by_name["HEADERS"].lines, "HEADERS")
    request_body_raw = "\n".join(by_name["REQUEST BODY"].lines).strip()
    try:
        request_body = json.loads(request_body_raw)
    except json.JSONDecodeError as error:
        raise UnsupportedCapture(f"REQUEST BODY is not valid JSON: {error}") from error
    if not isinstance(request_body, dict):
        raise UnsupportedCapture("REQUEST BODY is not a JSON object")

    attempts = group_attempts(sections)
    downstream = parse_response_block(by_name["RESPONSE"])
    consumed = {"REQUEST INFO", "HEADERS", "REQUEST BODY", "RESPONSE"}
    consumed.update(section.name for section in sections if API_REQUEST_RE.match(section.name))
    consumed.update(section.name for section in sections if API_RESPONSE_RE.match(section.name))
    leftovers = [section for section in sections if section.name not in consumed]
    return Capture(
        request_info=request_info,
        request_headers=request_headers,
        request_body=request_body,
        attempts=attempts,
        downstream=downstream,
        leftovers=leftovers,
    )


def write_temp_file(directory: Path, name: str, content: str) -> Path:
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=directory,
        prefix=f".{name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(content)
        temp_name = handle.name
    return Path(temp_name)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a CLIProxyAPI chat-completions capture as readable Markdown.",
    )
    parser.add_argument("capture", type=Path, help="path to v1-chat-completions-*.log")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd(),
        help="directory for the Markdown files (default: current directory)",
    )
    parser.add_argument("--force", action="store_true", help="overwrite existing output files")
    args = parser.parse_args(argv)

    source = args.capture.expanduser().resolve()
    if not source.is_file():
        print(f"error: capture not found: {source}", file=sys.stderr)
        return 2

    try:
        capture = load_capture(source)
        completion = decode_completion(capture.downstream)
        bundle = render_documents(
            source,
            capture.request_info,
            capture.request_headers,
            capture.request_body,
            capture.attempts,
            capture.downstream,
            completion,
            capture.leftovers,
        )
    except UnsupportedCapture as error:
        print(f"unsupported capture: {error}", file=sys.stderr)
        return 3
    except CaptureError as error:
        print(f"error: {error}", file=sys.stderr)
        return 3

    output_dir = args.output_dir.expanduser().resolve()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"error: cannot create output directory: {error}", file=sys.stderr)
        return 2
    transcript_path = output_dir / f"{source.stem}.md"
    system_path = output_dir / f"{source.stem}-system-prompt.md"
    raw_path = output_dir / f"{source.stem}-raw-payloads.md"
    payloads = [(transcript_path, bundle.transcript)]
    if bundle.system_prompt is not None:
        payloads.append((system_path, bundle.system_prompt))
    payloads.append((raw_path, bundle.raw_reference))
    existing = [path for path, _ in payloads if path.exists()]
    if existing and not args.force:
        names = ", ".join(path.name for path in existing)
        print(
            f"error: output already exists ({names}); pass --force to overwrite",
            file=sys.stderr,
        )
        return 2

    written: dict[Path, Path] = {}
    try:
        for target, content in payloads:
            written[target] = write_temp_file(output_dir, target.name, content)
        verify_bundle(
            written[transcript_path],
            written[raw_path],
            bundle,
            written.get(system_path),
        )
        for target, temp_path in written.items():
            temp_path.replace(target)
    except VerificationError as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 4
    except OSError as error:
        print(f"error: cannot write output: {error}", file=sys.stderr)
        return 2
    finally:
        for temp_path in written.values():
            if temp_path.exists():
                temp_path.unlink()

    print(f"transcript:     {transcript_path}")
    if bundle.system_prompt is not None:
        print(f"system prompt:  {system_path}")
    print(f"raw payloads:   {raw_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
