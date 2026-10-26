#!/usr/bin/env python3
"""Locate CLIProxyAPI request logs and report whether capture is enabled.

This script does not read request bodies. It answers three questions quickly:

  1. Which config file is present, and what does it say about request logging?
  2. Where is the auth directory, and therefore where are the logs?
  3. Which request captures and error logs exist, and how new are they?

The config parser is deliberately narrow: it reads only the keys needed for
log discovery and ignores everything else. CLIProxyAPI versions differ in where
these keys live, so the script does not assume a nesting level; it records the
last uncommented occurrence of each key and warns when a value is absent.

Usage:
    python3 scripts/find_request_logs.py
    python3 scripts/find_request_logs.py --config ~/.cli-proxy-api/config.yaml
    python3 scripts/find_request_logs.py --logs-dir ~/.cli-proxy-api/logs --limit 5
    python3 scripts/find_request_logs.py --json

Exit codes: 0 inspection completed | 2 invalid arguments or an explicitly
requested config file is missing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

CONFIG_KEYS = {
    "auth-dir",
    "request-log",
    "logging-to-file",
    "debug",
    "logs-max-total-size-mb",
    "error-logs-max-files",
    "port",
}
# CLIProxyAPI v8 nests these keys. Older layouts use other parents or top-level
# keys, so record the full path and prefer the v8 spelling when both exist.
V8_PATHS = {
    "auth-dir": "oauth.auth-dir",
    "request-log": "observability.logs.request-log",
    "logging-to-file": "observability.logs.logging-to-file",
    "debug": "observability.logs.debug",
    "logs-max-total-size-mb": "observability.logs.logs-max-total-size-mb",
    "error-logs-max-files": "observability.logs.error-logs-max-files",
    "port": "server.port",
}
FILENAME_TIMESTAMP_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{6})")
REQUEST_NAME_RE = re.compile(r"^(?:error-)?(?P<endpoint>v1-.*?)-\d{4}-\d{2}-\d{2}T\d{6}-[0-9a-f]+$")
DEFAULT_LIMIT = 10


@dataclass
class ConfigValue:
    value: Any
    line: int
    path: str
    occurrences: int = 1


@dataclass
class ConfigReport:
    path: Path | None
    values: dict[str, ConfigValue] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {
            "path": str(self.path) if self.path else None,
            "found": self.path is not None,
            "values": {key: item.value for key, item in self.values.items()},
            "sources": {
                key: {
                    "path": item.path,
                    "line": item.line,
                    "occurrences": item.occurrences,
                }
                for key, item in self.values.items()
            },
            "warnings": list(self.warnings),
        }


@dataclass
class LogEntry:
    path: Path
    kind: str
    endpoint: str
    captured_at: datetime | None
    modified_at: datetime
    size: int

    def as_json(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "name": self.path.name,
            "kind": self.kind,
            "endpoint": self.endpoint,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
            "modified_at": self.modified_at.isoformat(),
            "size": self.size,
        }


@dataclass
class DirectoryReport:
    path: Path
    source: str
    exists: bool
    requests: list[LogEntry] = field(default_factory=list)
    errors: list[LogEntry] = field(default_factory=list)
    others: list[LogEntry] = field(default_factory=list)
    part_directories: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_json(self, limit: int) -> dict[str, Any]:
        def entries(items: list[LogEntry]) -> list[dict[str, Any]]:
            return [item.as_json() for item in items[:limit]] if limit else [
                item.as_json() for item in items
            ]

        return {
            "path": str(self.path),
            "source": self.source,
            "exists": self.exists,
            "request_count": len(self.requests),
            "error_count": len(self.errors),
            "other_count": len(self.others),
            "part_directories": [str(path) for path in self.part_directories],
            "requests": entries(self.requests),
            "errors": entries(self.errors),
            "others": entries(self.others),
            "warnings": list(self.warnings),
        }


def strip_inline_comment(raw_value: str) -> str:
    quote = ""
    for index, character in enumerate(raw_value):
        if quote:
            if character == quote:
                quote = ""
        elif character in "'\"":
            quote = character
        elif character == "#" and index > 0 and raw_value[index - 1].isspace():
            return raw_value[:index].rstrip()
    return raw_value.rstrip()


def parse_scalar(raw_value: str) -> Any:
    value = raw_value.strip()
    if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
        value = value[1:-1]
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def read_config(path: Path) -> ConfigReport:
    report = ConfigReport(path=path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        report.warnings.append(f"cannot read config: {error}")
        return report

    stack: list[tuple[int, str]] = []
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "- ")):
            continue
        match = re.match(r"^(?P<key>[A-Za-z0-9_-]+):\s*(?P<value>.*)$", stripped)
        if not match:
            continue
        key = match.group("key")
        indent = len(line) - len(line.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = ".".join(label for _, label in stack)
        full_path = f"{parent}.{key}" if parent else key
        stack.append((indent, key))
        if key not in CONFIG_KEYS:
            continue
        raw_value = strip_inline_comment(match.group("value").strip())
        if not raw_value:
            report.warnings.append(f"{key} has no value at {path}:{line_number}")
            continue
        value = parse_scalar(raw_value)
        v8_path = V8_PATHS.get(key)
        item = ConfigValue(value=value, line=line_number, path=full_path)
        previous = report.values.get(key)
        if previous is None:
            report.values[key] = item
        elif full_path == v8_path:
            item.occurrences = previous.occurrences + 1
            report.values[key] = item
        elif v8_path is not None and previous.path == v8_path:
            previous.occurrences += 1
            report.warnings.append(
                f"{key} at {path}:{line_number} is outside the v8 path; "
                f"the v8 value at line {previous.line} wins"
            )
        else:
            item.occurrences = previous.occurrences + 1
            report.values[key] = item
    return report


def candidate_config_paths() -> list[Path]:
    candidates: list[Path] = []
    environment = os.environ.get("CLI_PROXY_CONFIG")
    if environment:
        candidates.append(Path(environment).expanduser())
    candidates.extend(
        [
            Path("~/.cli-proxy-api/config.yaml").expanduser(),
            Path("~/.config/cli-proxy-api/config.yaml").expanduser(),
            Path("/etc/cli-proxy-api/config.yaml"),
        ]
    )
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in candidates:
        resolved = path.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def resolve_auth_dir(report: ConfigReport) -> Path | None:
    item = report.values.get("auth-dir")
    if item is None or not isinstance(item.value, str) or not item.value:
        return None
    path = Path(item.value).expanduser()
    if not path.is_absolute() and report.path is not None:
        path = report.path.parent / path
    return path.resolve()


def log_directories(
    config: ConfigReport,
    explicit: Path | None,
) -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    if explicit is not None:
        candidates.append((explicit.expanduser().resolve(), "explicit --logs-dir"))
    auth_dir = resolve_auth_dir(config)
    if auth_dir is not None:
        candidates.append((auth_dir / "logs", "auth-dir/logs"))
    candidates.extend(
        [
            (Path("~/.cli-proxy-api/logs").expanduser(), "default"),
            (Path("~/.config/cli-proxy-api/logs").expanduser(), "default"),
            (Path("/var/log/cli-proxy-api"), "default"),
        ]
    )
    if config.path is not None:
        candidates.append((config.path.parent / "logs", "config directory"))
    working_directory = Path.cwd()
    if list(working_directory.glob("v1-*.log")) or list(working_directory.glob("error-*.log")):
        candidates.append((working_directory, "working directory"))

    seen: set[Path] = set()
    unique: list[tuple[Path, str]] = []
    for path, source in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append((resolved, source))
    return unique


def classify(name: str) -> str | None:
    if not name.endswith(".log"):
        return None
    if name.startswith("error-"):
        return "error"
    if name.startswith("v1-"):
        return "request"
    return "other"


def capture_endpoint(name: str) -> str:
    match = REQUEST_NAME_RE.match(name.removesuffix(".log"))
    if match:
        return match.group("endpoint")
    return name.removesuffix(".log")


def capture_time(name: str) -> datetime | None:
    match = FILENAME_TIMESTAMP_RE.search(name)
    if not match:
        return None
    try:
        return datetime.strptime(f"{match.group('date')} {match.group('time')}", "%Y-%m-%d %H%M%S")
    except ValueError:
        return None


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.1f} GB"


def scan_directory(path: Path, source: str) -> DirectoryReport:
    report = DirectoryReport(path=path, source=source, exists=path.is_dir())
    if not report.exists:
        return report
    try:
        entries = list(path.iterdir())
    except OSError as error:
        report.warnings.append(f"cannot list directory: {error}")
        return report

    for entry in entries:
        if entry.is_dir() and entry.name.startswith("request-log-parts-"):
            report.part_directories.append(entry)
            continue
        kind = classify(entry.name)
        if kind is None or not entry.is_file():
            continue
        try:
            stat = entry.stat()
        except OSError as error:
            report.warnings.append(f"cannot stat {entry.name}: {error}")
            continue
        item = LogEntry(
            path=entry,
            kind=kind,
            endpoint=capture_endpoint(entry.name),
            captured_at=capture_time(entry.name),
            modified_at=datetime.fromtimestamp(stat.st_mtime),
            size=stat.st_size,
        )
        if kind == "request":
            report.requests.append(item)
        elif kind == "error":
            report.errors.append(item)
        else:
            report.others.append(item)

    for items in (report.requests, report.errors, report.others):
        items.sort(
            key=lambda item: (item.captured_at or item.modified_at, item.modified_at),
            reverse=True,
        )
    report.part_directories.sort()
    return report


def config_warnings(config: ConfigReport) -> list[str]:
    warnings = list(config.warnings)
    request_log = config.values.get("request-log")
    logging_to_file = config.values.get("logging-to-file")
    if request_log is None:
        if config.path is None:
            warnings.append(
                "request-log status is unknown because no config file was found; do not assume "
                "request bodies are being captured."
            )
        else:
            warnings.append(
                "request-log is not set in this config; do not assume request bodies are being "
                "captured. Check the active config or the management API."
            )
    elif request_log.value is False:
        warnings.append(
            "request-log is false in this config: no new request captures will be written. "
            "Existing v1-*.log files may be from an earlier run or another config."
        )
    elif request_log.value is True:
        warnings.append(
            "request-log is true in this config: new request captures should appear under "
            "auth-dir/logs when the running process uses this file."
        )
    if logging_to_file is None:
        warnings.append(
            "logging-to-file status is unknown because no config file was found."
            if config.path is None
            else "logging-to-file is not set in this config."
        )
    elif logging_to_file.value is False:
        warnings.append(
            "logging-to-file is false: application logs go to stdout, not rotating files. "
            "This is independent of request-log."
        )
    return warnings


def directory_notes(report: DirectoryReport, config: ConfigReport) -> list[str]:
    notes: list[str] = []
    if not report.exists:
        return notes
    request_log = config.values.get("request-log")
    if report.errors and (request_log is None or request_log.value is False):
        notes.append(
            "error-*.log files are present even though request logging is not enabled; their "
            "presence does not prove that request bodies are captured."
        )
    if report.part_directories:
        notes.append(
            f"{plural(len(report.part_directories), 'request-log-parts directory', 'request-log-parts directories')} "
            "present; large bodies may be split across them."
        )
    return notes


def management_logs_url(config: ConfigReport) -> str | None:
    port = config.values.get("port")
    if port is None or not isinstance(port.value, int):
        return None
    return f"http://127.0.0.1:{port.value}/v0/management/logs"


def display_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    word = singular if count == 1 else (plural_form or f"{singular}s")
    return f"{count} {word}"


def print_human(
    config: ConfigReport,
    directories: list[DirectoryReport],
    limit: int,
) -> None:
    if config.path is None:
        print("Config: not found")
        print("  Checked the environment, the default locations, and /etc.")
    else:
        print(f"Config: {config.path}")
        print("  Local file only; a running process may use a different path or Home-synthesized settings.")
        for key in (
            "auth-dir",
            "request-log",
            "logging-to-file",
            "debug",
            "logs-max-total-size-mb",
            "error-logs-max-files",
            "port",
        ):
            item = config.values.get(key)
            if item is not None:
                label = "server port" if key == "port" else key
                print(f"  {label}: {display_value(item.value)}  [{item.path}]")
    print()

    existing = [report for report in directories if report.exists]
    missing = [report for report in directories if not report.exists]
    if not existing:
        print("No log directories found. Checked:")
        for report in missing:
            print(f"  {report.path}  [{report.source}]")
        print()
    for report in existing:
        print(f"Log directory: {report.path}  [{report.source}]")
        print(
            f"  {plural(len(report.requests), 'request capture')}, "
            f"{plural(len(report.errors), 'error log')}, "
            f"{plural(len(report.others), 'other log')}, "
            f"{plural(len(report.part_directories), 'part directory', 'part directories')}"
        )
        if report.requests:
            print("\n  Newest request captures:")
            print("  captured              size        file")
            items = report.requests if limit == 0 else report.requests[:limit]
            for item in items:
                moment = item.captured_at.strftime("%Y-%m-%d %H:%M:%S") if item.captured_at else "unknown"
                print(f"  {moment:<20}  {human_size(item.size):>9}  {item.path.name}")
        if report.errors:
            print("\n  Newest error logs:")
            print("  captured              size        file")
            items = report.errors if limit == 0 else report.errors[:limit]
            for item in items:
                moment = item.captured_at.strftime("%Y-%m-%d %H:%M:%S") if item.captured_at else "unknown"
                print(f"  {moment:<20}  {human_size(item.size):>9}  {item.path.name}")
        notes = directory_notes(report, config)
        if notes:
            print()
            for note in notes:
                print(f"  note: {note}")
        print()
    if missing:
        print("Also checked (not found): " + ", ".join(str(report.path) for report in missing))
        print()

    warnings = config_warnings(config)
    if warnings:
        print("Verdict")
        for warning in warnings:
            print(f"- {warning}")
        endpoint = management_logs_url(config)
        if endpoint:
            print(
                f"- Management endpoint: {endpoint} returns application logs only, never "
                "request bodies. It requires the configured management key."
            )
        print()

def build_json_report(
    config: ConfigReport,
    directories: list[DirectoryReport],
    limit: int,
) -> dict[str, Any]:
    directory_reports: list[dict[str, Any]] = []
    for report in directories:
        item = report.as_json(limit)
        item["notes"] = directory_notes(report, config)
        directory_reports.append(item)
    return {
        "config": config.as_json(),
        "log_directories": directory_reports,
        "management_logs_url": management_logs_url(config),
        "warnings": config_warnings(config),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Locate CLIProxyAPI request logs and report logging settings.",
    )
    parser.add_argument("--config", type=Path, help="explicit config file to inspect")
    parser.add_argument("--logs-dir", type=Path, help="explicit logs directory to include")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"newest entries to list per directory (default: {DEFAULT_LIMIT}; 0 = all)",
    )
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    args = parser.parse_args(argv)
    if args.limit < 0:
        print("error: --limit cannot be negative", file=sys.stderr)
        return 2

    explicit_config = args.config.expanduser().resolve() if args.config else None
    if explicit_config is not None and not explicit_config.is_file():
        print(f"error: config not found: {explicit_config}", file=sys.stderr)
        return 2

    config_path = explicit_config
    checked_paths: list[Path] = []
    if config_path is None:
        for candidate in candidate_config_paths():
            checked_paths.append(candidate)
            if candidate.is_file():
                config_path = candidate
                break
    config = read_config(config_path) if config_path is not None else ConfigReport(path=None)
    if config.path is None:
        config.warnings.append(
            "no config file found; log locations below come from defaults and the working directory"
        )
        config.warnings.append(
            "checked: " + ", ".join(str(path) for path in checked_paths)
        )

    directories = [
        scan_directory(path, source)
        for path, source in log_directories(config, args.logs_dir)
    ]
    if args.json:
        print(json.dumps(build_json_report(config, directories, args.limit), indent=2))
    else:
        print_human(config, directories, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
