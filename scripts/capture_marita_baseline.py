#!/usr/bin/env python3
"""Capture and summarize a read-only MARITA benchmark run.

The collector deliberately treats the remote machine as an evidence source:
it only invokes read-only commands over SSH and copies selected files with
rsync.  The parsers are kept independent of transport so that an archive can
be audited and regression-tested without access to the original host.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import re
import shlex
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Sequence

SCHEMA_VERSION = 1
DEFAULT_MEMORY_LIMIT_GB = 10.0
STATUSES = (
    "success",
    "no_joinable_indexed",
    "timeout",
    "memory_limit",
    "interrupted",
    "error",
    "partial",
    "missing",
)
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
LOG_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+\[[^]]+\]\s+-\s*(?P<message>.*)$"
)
START_RE = re.compile(r"Starting rule discovery process\.")
DATABASE_URI_RE = re.compile(r"Using database URI:\s*.*?[/\\](?P<database>[^/\\]+?)(?:\.db)?$")
RULE_COUNT_RE = re.compile(r"Discovered\s+(?P<count>\d+)\s+rules\.")
FINISH_RE = re.compile(r"finished in\s+(?P<seconds>[\d.]+)\s+seconds\s+with\s+(?P<rules>\d+)\s+rules")
TIMEOUT_RE = re.compile(r"Execution timeout exceeded:\s*(?P<seconds>[\d.]+)s")
MEMORY_RE = re.compile(r"Memory usage exceeded:\s*(?P<gb>[\d.]+)\s*GB", re.IGNORECASE)
RSS_RE = re.compile(r"(?:peak\s+)?RSS(?:\s+peak)?[:=]\s*(?P<bytes>\d+)\s*bytes", re.IGNORECASE)
REPORT_RE = re.compile(r"Generated report:\s*(?P<path>\S+)")
EXECUTION_RE = re.compile(r"Saved execution time metrics:\s*(?P<path>\S+)")
GRAPH_RE = re.compile(r"ConstraintGraph\(Nodes:\s*(?P<nodes>\d+),\s*Edges:\s*(?P<edges>\d+)\)")
REPORT_RULES_RE = re.compile(r"Number of Rules Discovered:\s*(?P<count>\d+)")


@dataclass(frozen=True)
class CopySpec:
    """A remote path and its archive destination."""

    source: str
    destination: Path
    patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class RemoteFile:
    source: str
    archive: Path


def strip_ansi(text: str) -> str:
    """Remove terminal color sequences retained in benchmark logs."""

    return ANSI_RE.sub("", text)


def _timestamp(value: str) -> str:
    return dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S,%f").isoformat(timespec="milliseconds")


def _database_stem(value: str) -> str:
    name = Path(value).name
    return name[:-3] if name.lower().endswith(".db") else name


def _parse_log_line(line: str) -> tuple[str, str] | None:
    match = LOG_LINE_RE.match(strip_ansi(line.rstrip("\n")))
    if match is None:
        return None
    return _timestamp(match.group("timestamp")), match.group("message").strip()


def _new_attempt(log_path: str, timestamp: str) -> dict[str, Any]:
    return {
        "database": None,
        "started_at": timestamp,
        "ended_at": None,
        "status": "partial",
        "runtime_seconds": None,
        "rules_count": None,
        "memory_event_gb": None,
        "peak_rss_bytes": None,
        "reported_error": None,
        "log_path": log_path,
        "events": [],
    }


def _finish_attempt(attempt: dict[str, Any], timestamp: str | None, message: str | None = None) -> None:
    if timestamp is not None:
        attempt["ended_at"] = timestamp
    if message:
        attempt["events"].append(message)

    if attempt["status"] == "partial" and attempt["ended_at"] is None:
        attempt["ended_at"] = attempt["started_at"]


def _set_attempt_status(attempt: dict[str, Any], status: str, message: str) -> None:
    """Apply terminal status precedence while retaining the original event."""

    precedence = {
        "partial": 0,
        "success": 1,
        "interrupted": 2,
        "error": 3,
        "timeout": 4,
        "memory_limit": 5,
        "no_joinable_indexed": 6,
    }
    if precedence[status] >= precedence.get(attempt["status"], 0):
        attempt["status"] = status
    attempt["events"].append(message)


def _compact_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in attempt.items() if key not in {"events", "last_event_at"}}


def parse_log_attempts(text: str, log_path: str = "") -> list[dict[str, Any]]:
    """Group one ``global.log`` into attempts and reconcile terminal events.

    The logs contain several attempts for the same database.  A later timeout
    therefore does not erase an earlier successful attempt, and a memory event
    takes precedence over the signal that usually follows it.
    """

    attempts: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        parsed = _parse_log_line(raw_line)
        if parsed is None:
            continue
        timestamp, message = parsed
        if START_RE.search(message):
            if current is not None:
                _finish_attempt(current, current.get("last_event_at"))
                attempts.append(_compact_attempt(current))
            current = _new_attempt(log_path, timestamp)
            current["last_event_at"] = timestamp
            continue
        if current is None:
            continue

        current["last_event_at"] = timestamp
        database_match = DATABASE_URI_RE.search(message)
        if database_match:
            current["database"] = _database_stem(database_match.group("database"))

        count_match = RULE_COUNT_RE.search(message)
        if count_match:
            current["rules_count"] = int(count_match.group("count"))

        finish_match = FINISH_RE.search(message)
        if finish_match:
            current["runtime_seconds"] = float(finish_match.group("seconds"))
            current["rules_count"] = int(finish_match.group("rules"))

        timeout_match = TIMEOUT_RE.search(message)
        if timeout_match:
            current["runtime_seconds"] = float(timeout_match.group("seconds"))
            current["reported_error"] = message
            _set_attempt_status(current, "timeout", message)
        memory_match = MEMORY_RE.search(message)
        if memory_match:
            current["memory_event_gb"] = float(memory_match.group("gb"))
            current["reported_error"] = message
            _set_attempt_status(current, "memory_limit", message)
        rss_match = RSS_RE.search(message)
        if rss_match:
            current["peak_rss_bytes"] = int(rss_match.group("bytes"))
        if "no joinable" in message.lower() and "indexed" in message.lower():
            _set_attempt_status(current, "no_joinable_indexed", message)
        elif "Process completed successfully" in message:
            _set_attempt_status(current, "success", message)
        elif "Received signal" in message:
            _set_attempt_status(current, "interrupted", message)
        elif "Traceback" in message or "An error occurred" in message:
            current["reported_error"] = message
            _set_attempt_status(current, "error", message)

        report_match = REPORT_RE.search(message)
        if report_match:
            current["report_path"] = report_match.group("path")
        execution_match = EXECUTION_RE.search(message)
        if execution_match:
            current["execution_path"] = execution_match.group("path")

    if current is not None:
        _finish_attempt(current, current.get("last_event_at"))
        attempts.append(_compact_attempt(current))

    for attempt in attempts:
        attempt.pop("last_event_at", None)
        if attempt["database"] is None:
            attempt["database"] = Path(log_path).parent.name if log_path else None
        if attempt["status"] not in STATUSES:
            attempt["status"] = "partial"
    return attempts


def parse_log_tree(log_root: Path) -> list[dict[str, Any]]:
    """Parse every ``global.log`` below a captured MARITA log tree."""

    attempts: list[dict[str, Any]] = []
    for path in sorted(log_root.glob("**/global.log")):
        attempts.extend(parse_log_attempts(path.read_text(encoding="utf-8", errors="replace"), path.as_posix()))
    return attempts


def safe_load_json_text(text: str) -> tuple[Any, str | None]:
    """Load JSON while distinguishing empty and malformed evidence."""

    if not text.strip():
        return None, "empty_artifact"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"malformed_json: {exc.msg}"


def load_json_artifact(path: Path) -> tuple[Any, str | None]:
    try:
        return safe_load_json_text(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "missing_artifact"
    except OSError as exc:
        return None, f"read_error: {exc}"


def parse_constraint_graph(value: Any) -> dict[str, Any]:
    """Extract graph totals from the string serialized by MARITA."""

    if not isinstance(value, str):
        return {"parse_status": "invalid_type", "nodes": None, "edges": None, "listed_edge_lines": 0}
    match = GRAPH_RE.search(value)
    listed_edges = sum(1 for line in value.splitlines() if line.startswith("JIA("))
    if match is None:
        return {"parse_status": "unrecognized", "nodes": None, "edges": None, "listed_edge_lines": listed_edges}
    return {
        "parse_status": "ok",
        "nodes": int(match.group("nodes")),
        "edges": int(match.group("edges")),
        "listed_edge_lines": listed_edges,
    }


def parse_compatibility(value: Any) -> dict[str, Any]:
    """Summarize the symmetric attribute compatibility mapping."""

    if not isinstance(value, dict):
        return {
            "parse_status": "invalid_type",
            "attribute_count": None,
            "directed_pair_count": None,
            "pair_count": None,
        }
    directed = 0
    for targets in value.values():
        if isinstance(targets, list):
            directed += len(targets)
    return {
        "parse_status": "ok",
        "attribute_count": len(value),
        "directed_pair_count": directed,
        "pair_count": directed // 2 if directed % 2 == 0 else None,
    }


def _rule_field(rule: dict[str, Any], field: str, default: Any = None) -> Any:
    value = rule.get(field, default)
    if isinstance(value, (list, dict)):
        return value
    return value


def normalize_rule(database: str, index: int, rule: dict[str, Any]) -> dict[str, Any]:
    """Create the stable, analysis-friendly representation of one rule."""

    normalized: dict[str, Any] = {
        "database": database,
        "rule_index": index,
        "type": rule.get("type", "unknown"),
        "body": _rule_field(rule, "body", []),
        "head": _rule_field(rule, "head", []),
        "display": rule.get("display"),
        "support": rule.get("support"),
        "confidence": rule.get("confidence"),
    }
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    normalized["rule_hash"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return normalized


def _artifact_entry(path: Path, root: Path, error: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"path": path.relative_to(root).as_posix(), "available": path.is_file()}
    if error is not None:
        entry["parse_status"] = error
    return entry


def _report_rule_count(path: Path) -> tuple[int | None, str | None]:
    try:
        match = REPORT_RULES_RE.search(path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        return None, f"read_error: {exc}"
    return (int(match.group("count")) if match else None), None


def _relative_rule_file(path: Path, results_root: Path) -> str:
    return path.relative_to(results_root).as_posix()


def parse_artifact_snapshot(results_root: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Index the current result snapshot and return normalized rule records."""

    snapshots: dict[str, dict[str, Any]] = {}
    normalized_rules: list[dict[str, Any]] = []

    def snapshot_for(database: str) -> dict[str, Any]:
        return snapshots.setdefault(database, {"database": database, "artifacts": {}, "parse_errors": []})

    for path in sorted(results_root.glob("MARITA_*/**/*.json")):
        parent = path.parent.name
        if not parent.startswith("MARITA_"):
            continue
        database = parent.removeprefix("MARITA_")
        snapshot = snapshot_for(database)
        name = path.name
        if name.startswith("execution_time_"):
            kind = "execution_time_json"
        elif name.endswith("_results.json"):
            kind = "rule_json"
        elif name.startswith("init_time_metrics_"):
            kind = "initialization_metrics_json"
        elif name.startswith("cg_metrics_"):
            kind = "constraint_graph_json"
        elif name.startswith("compatibility_"):
            kind = "compatibility_json"
        else:
            continue
        value, error = load_json_artifact(path)
        entry = _artifact_entry(path, results_root, error)
        if value is not None:
            if kind == "rule_json":
                if isinstance(value, list):
                    entry["rule_count"] = len(value)
                    for index, rule in enumerate(value, start=1):
                        if isinstance(rule, dict):
                            normalized_rules.append(normalize_rule(database, index, rule))
                        else:
                            snapshot["parse_errors"].append(f"{entry['path']} rule {index}: invalid_rule")
                else:
                    entry["parse_status"] = "invalid_rule_container"
            elif kind == "execution_time_json":
                if isinstance(value, dict):
                    entry["reported_status"] = value.get("status")
                    entry["reported_rules_count"] = value.get("rules_count")
                    entry["execution_time_seconds"] = value.get("execution_time_seconds")
            elif kind == "constraint_graph_json":
                entry.update(parse_constraint_graph(value))
            elif kind == "compatibility_json":
                entry.update(parse_compatibility(value))
            elif kind == "initialization_metrics_json" and isinstance(value, dict):
                entry["metrics"] = value
        if error is not None:
            snapshot["parse_errors"].append(f"{entry['path']}: {error}")
        snapshot["artifacts"][kind] = entry

    for path in sorted(results_root.glob("report_MARITA_*.md")):
        database = path.stem.removeprefix("report_MARITA_")
        snapshot = snapshot_for(database)
        count, error = _report_rule_count(path)
        entry = _artifact_entry(path, results_root, error)
        entry["reported_rules_count"] = count
        snapshot["artifacts"]["report_md"] = entry

    for database, snapshot in snapshots.items():
        artifacts = snapshot["artifacts"]
        has_rules = "rule_json" in artifacts
        has_execution = "execution_time_json" in artifacts
        if has_rules and has_execution:
            snapshot["state"] = "complete"
        elif artifacts:
            snapshot["state"] = "partial"
        else:
            snapshot["state"] = "missing"
        snapshot["artifact_count"] = len(artifacts)
        snapshot["normalized_rule_count"] = sum(1 for rule in normalized_rules if rule["database"] == database)
    return snapshots, normalized_rules


def reconcile_database(
    database: str,
    attempts: Sequence[dict[str, Any]],
    snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """Cross-check authoritative logs against the latest artifact snapshot."""

    artifacts = (snapshot or {}).get("artifacts", {})
    checks: list[str] = []
    result = artifacts.get("rule_json", {})
    execution = artifacts.get("execution_time_json", {})
    report = artifacts.get("report_md", {})
    result_count = result.get("rule_count")
    execution_count = execution.get("reported_rules_count")
    report_count = report.get("reported_rules_count")
    if result_count is not None and execution_count is not None and result_count != execution_count:
        checks.append("rule_json_vs_execution_rules_count")
    if result_count is not None and report_count is not None and result_count != report_count:
        checks.append("rule_json_vs_report_rules_count")
    if execution.get("reported_status") and attempts and execution["reported_status"] != attempts[-1].get("status"):
        checks.append("execution_status_vs_latest_log_attempt")
    return {
        "database": database,
        "log_attempt_count": len(attempts),
        "latest_log_status": attempts[-1].get("status") if attempts else "missing",
        "artifact_state": (snapshot or {}).get("state", "missing"),
        "report_available": bool(report.get("available")),
        "execution_json_available": bool(execution.get("available")),
        "rule_json_available": bool(result.get("available")),
        "rule_count": result_count,
        "execution_reported_rules_count": execution_count,
        "report_reported_rules_count": report_count,
        "disagreements": checks,
    }


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def collect_sqlite_metadata(database_path: Path) -> dict[str, Any]:
    """Hash a SQLite file and collect read-only schema/count statistics."""

    digest = hashlib.sha256()
    with database_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    result: dict[str, Any] = {
        "database": database_path.name,
        "source_path": str(database_path),
        "size_bytes": database_path.stat().st_size,
        "sha256": digest.hexdigest(),
        "sqlite": {
            "tables": [],
            "table_count": 0,
            "row_count": 0,
            "column_count": 0,
            "foreign_key_count": 0,
            "index_count": 0,
        },
    }
    try:
        connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        result["sqlite_error"] = str(exc)
        return result
    with connection:
        table_names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        for table_name in table_names:
            quoted = _quote_identifier(table_name)
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({quoted})")]
            foreign_keys = list(connection.execute(f"PRAGMA foreign_key_list({quoted})"))
            indexes = list(connection.execute(f"PRAGMA index_list({quoted})"))
            row_count = int(connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
            result["sqlite"]["tables"].append(
                {
                    "name": table_name,
                    "row_count": row_count,
                    "column_count": len(columns),
                    "columns": columns,
                    "foreign_key_count": len(foreign_keys),
                    "index_count": len(indexes),
                }
            )
        tables = result["sqlite"]["tables"]
        result["sqlite"].update(
            {
                "table_count": len(tables),
                "row_count": sum(table["row_count"] for table in tables),
                "column_count": sum(table["column_count"] for table in tables),
                "foreign_key_count": sum(table["foreign_key_count"] for table in tables),
                "index_count": sum(table["index_count"] for table in tables),
            }
        )
    connection.close()
    return result


REMOTE_METADATA_SCRIPT = r"""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

def quote(value):
    return '"' + value.replace('"', '""') + '"'

def inspect(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    record = {
        'database': path.name,
        'source_path': str(path),
        'size_bytes': path.stat().st_size,
        'sha256': digest.hexdigest(),
        'sqlite': {'tables': [], 'table_count': 0, 'row_count': 0, 'column_count': 0,
                   'foreign_key_count': 0, 'index_count': 0},
    }
    try:
        connection = sqlite3.connect('file:' + path.as_posix() + '?mode=ro', uri=True)
    except sqlite3.Error as exc:
        record['sqlite_error'] = str(exc)
        return record
    with connection:
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for name in names:
            q = quote(name)
            columns = [row[1] for row in connection.execute('PRAGMA table_info(' + q + ')')]
            foreign_keys = list(connection.execute('PRAGMA foreign_key_list(' + q + ')'))
            indexes = list(connection.execute('PRAGMA index_list(' + q + ')'))
            rows = int(connection.execute('SELECT COUNT(*) FROM ' + q).fetchone()[0])
            record['sqlite']['tables'].append({
                'name': name, 'row_count': rows, 'column_count': len(columns), 'columns': columns,
                'foreign_key_count': len(foreign_keys), 'index_count': len(indexes),
            })
        tables = record['sqlite']['tables']
        record['sqlite'].update({
            'table_count': len(tables),
            'row_count': sum(t['row_count'] for t in tables),
            'column_count': sum(t['column_count'] for t in tables),
            'foreign_key_count': sum(t['foreign_key_count'] for t in tables),
            'index_count': sum(t['index_count'] for t in tables),
        })
    connection.close()
    return record

root = Path(sys.argv[1])
print(json.dumps([inspect(path) for path in sorted(root.glob('*.db'))], sort_keys=True))
"""


def _run(command: Sequence[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, input=input_text, capture_output=True)


def run_ssh_text(host: str, *command: str) -> str:
    remote_command = " ".join(shlex.quote(argument) for argument in command)
    return _run(["ssh", host, remote_command]).stdout


def collect_remote_database_metadata(host: str, source_database_dir: str) -> list[dict[str, Any]]:
    output = run_ssh_text(host, "python3", "-c", REMOTE_METADATA_SCRIPT, source_database_dir)
    value = json.loads(output)
    if not isinstance(value, list):
        raise ValueError("remote database metadata was not a list")
    return value


def _remote_files(host: str, source: str, patterns: tuple[str, ...]) -> list[str]:
    path = Path(source)
    if not patterns and path.suffix:
        return [source]
    output = run_ssh_text(host, "find", source, "-type", "f")
    files = [line.strip() for line in output.splitlines() if line.strip()]
    if not patterns:
        return files
    return [file for file in files if any(fnmatch.fnmatch(Path(file).name, pattern) for pattern in patterns)]


def _source_relative(source: str, path: str) -> Path:
    source_path = Path(source)
    file_path = Path(path)
    if source_path.suffix and file_path == source_path:
        return Path(file_path.name)
    return file_path.relative_to(source_path)


def _rsync_copy(host: str, spec: CopySpec) -> list[RemoteFile]:
    files = _remote_files(host, spec.source, spec.patterns)
    if not files:
        raise FileNotFoundError(f"no files matched remote source {spec.source}")
    spec.destination.mkdir(parents=True, exist_ok=True)
    source_path = Path(spec.source)
    if source_path.suffix and len(files) == 1:
        _run(["rsync", "-a", "--checksum", f"{host}:{spec.source}", str(spec.destination / source_path.name)])
    else:
        command = ["rsync", "-a", "--checksum"]
        for pattern in spec.patterns:
            command.append(f"--include={pattern}")
        if spec.patterns:
            command.append("--exclude=*")
        command.extend([f"{host}:{spec.source}/", f"{spec.destination}/"])
        _run(command)
    return [RemoteFile(path, spec.destination / _source_relative(spec.source, path)) for path in files]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remote_sha256(host: str, paths: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for start in range(0, len(paths), 200):
        output = _run(["ssh", host, "sha256sum", *paths[start : start + 200]]).stdout
        for line in output.splitlines():
            match = re.match(r"^(?P<digest>[0-9a-f]{64})\s+[* ](?P<path>.+)$", line)
            if match:
                result[match.group("path")] = match.group("digest")
    return result


def verify_copied_checksums(host: str, files: Sequence[RemoteFile]) -> list[dict[str, Any]]:
    """Verify every selected remote file against its local archive copy."""

    remote_hashes = _remote_sha256(host, [file.source for file in files])
    checks: list[dict[str, Any]] = []
    for file in files:
        remote_digest = remote_hashes.get(file.source)
        local_digest = sha256_file(file.archive)
        checks.append(
            {
                "source_path": file.source,
                "archive_path": file.archive.as_posix(),
                "source_sha256": remote_digest,
                "archive_sha256": local_digest,
                "equal": remote_digest is not None and remote_digest == local_digest,
            }
        )
    mismatches = [check for check in checks if not check["equal"]]
    if mismatches:
        raise RuntimeError(f"checksum verification failed for {len(mismatches)} copied files")
    return checks


def _archive_relative(path: Path, archive_root: Path) -> str:
    return path.relative_to(archive_root).as_posix()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _write_checksums(archive_root: Path) -> Path:
    checksum_path = archive_root / "checksums.sha256"
    lines = []
    for path in sorted(archive_root.rglob("*")):
        if path.is_file() and path != checksum_path:
            lines.append(f"{sha256_file(path)}  {_archive_relative(path, archive_root)}")
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checksum_path


def _git_provenance(host: str, repo_path: str | None) -> dict[str, Any]:
    if repo_path is None:
        return {"available": False}
    commands = {
        "revision": ["git", "-C", repo_path, "rev-parse", "HEAD"],
        "branch": ["git", "-C", repo_path, "branch", "--show-current"],
        "status": ["git", "-C", repo_path, "status", "--short"],
        "commit": ["git", "-C", repo_path, "show", "-s", "--format=%H%n%aI%n%s", "HEAD"],
    }
    provenance: dict[str, Any] = {"available": True, "repo_path": repo_path}
    for key, command in commands.items():
        try:
            provenance[key] = run_ssh_text(host, *command).strip()
        except subprocess.CalledProcessError as exc:
            provenance[key] = {"error": exc.stderr.strip()}
    return provenance


def collect_provenance(host: str, repo_path: str | None) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, command in {
        "hostname": ("hostname", "-f"),
        "kernel": ("uname", "-a"),
        "python": ("python3", "--version"),
        "capture_clock": ("date", "+%Y-%m-%dT%H:%M:%S%z"),
    }.items():
        try:
            values[key] = run_ssh_text(host, *command).strip()
        except subprocess.CalledProcessError as exc:
            values[key] = {"error": exc.stderr.strip()}
    values["git"] = _git_provenance(host, repo_path)
    return values


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def build_manifest(
    *,
    archive_root: Path,
    manifest_path: Path,
    host: str,
    source_paths: dict[str, str],
    database_records: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    snapshots: dict[str, dict[str, Any]],
    normalized_rule_path: Path,
    copy_checks: list[dict[str, Any]],
    provenance: dict[str, Any],
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    attempts_by_db: dict[str, list[dict[str, Any]]] = {}
    for attempt in attempts:
        database = attempt.get("database")
        if database:
            attempts_by_db.setdefault(database, []).append(attempt)

    databases: list[dict[str, Any]] = []
    for record in sorted(database_records, key=lambda value: str(value.get("database", ""))):
        database = _database_stem(str(record["database"]))
        entry = dict(record)
        entry["database"] = database
        db_attempts = attempts_by_db.get(database, [])
        entry["attempts"] = db_attempts
        entry["latest_attempt"] = db_attempts[-1] if db_attempts else None
        entry["latest_artifacts"] = snapshots.get(database, {"database": database, "state": "missing", "artifacts": {}})
        entry["cross_checks"] = reconcile_database(database, db_attempts, snapshots.get(database))
        databases.append(entry)

    status_counts: dict[str, int] = {status: 0 for status in STATUSES}
    for attempt in attempts:
        status = attempt.get("status", "partial")
        if status in status_counts:
            status_counts[status] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "capture": {
            "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "host": host,
            "archive": _relative_or_absolute(archive_root, Path.cwd()),
            "manifest": _relative_or_absolute(manifest_path, Path.cwd()),
            "scope": "MARITA/MARITA evidence only; source databases are referenced by hash and not copied",
        },
        "source": source_paths,
        "configuration": config or {"available": False},
        "provenance": provenance,
        "summary": {
            "database_count": len(databases),
            "attempt_count": len(attempts),
            "attempt_status_counts": status_counts,
            "result_execution_pair_count": sum(
                1 for database in databases if database["latest_artifacts"].get("state") == "complete"
            ),
            "metric_only_partial_run_count": sum(
                1 for database in databases if database["latest_artifacts"].get("state") == "partial"
            ),
            "missing_artifact_database_count": sum(
                1 for database in databases if database["latest_artifacts"].get("state") == "missing"
            ),
            "initialization_metric_file_count": sum(
                "initialization_metrics_json" in database["latest_artifacts"].get("artifacts", {})
                for database in databases
            ),
            "constraint_graph_metric_file_count": sum(
                "constraint_graph_json" in database["latest_artifacts"].get("artifacts", {}) for database in databases
            ),
            "compatibility_metric_file_count": sum(
                "compatibility_json" in database["latest_artifacts"].get("artifacts", {}) for database in databases
            ),
            "copied_file_count": len(copy_checks),
            "checksum_mismatch_count": sum(not check["equal"] for check in copy_checks),
            "observed_memory_events_gb": sorted(
                {attempt["memory_event_gb"] for attempt in attempts if attempt.get("memory_event_gb") is not None}
            ),
        },
        "artifacts": {
            "normalized_rules_jsonl": _relative_or_absolute(normalized_rule_path, Path.cwd()),
            "raw_results_root": "raw/results/MARITA",
            "raw_logs_root": "raw/logs/MARITA",
        },
        "databases": databases,
        "copy_checksums": copy_checks,
    }


def _load_config(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return {"parse_status": f"unavailable: {exc}"}
    if not isinstance(value, dict):
        return {"parse_status": "invalid_config"}
    monitor = value.get("limits", {}) if isinstance(value.get("limits"), dict) else value.get("monitor", {})
    return {
        "parse_status": "ok",
        "source": path.as_posix(),
        "timeout_seconds": monitor.get("timeout_seconds", monitor.get("timeout")),
        "memory_gb": monitor.get("memory_gb"),
        "memory_limit_bytes": monitor.get("memory_threshold"),
        "algorithm": value.get("algorithm"),
        "raw_top_level_keys": sorted(value),
    }


def capture(args: argparse.Namespace) -> dict[str, Any]:
    archive_root = Path(args.output).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve()
    if archive_root.exists() and any(archive_root.iterdir()):
        raise FileExistsError(f"archive output is not empty: {archive_root}")
    archive_root.mkdir(parents=True, exist_ok=True)

    specs = [
        CopySpec(args.source_results, archive_root / "raw" / "results" / "MARITA"),
        CopySpec(args.source_logs, archive_root / "raw" / "logs" / "MARITA"),
        CopySpec(args.source_timings, archive_root / "raw" / "logs" / "timings", ("marita_*.stdout",)),
    ]
    if args.source_run_configs:
        specs.append(
            CopySpec(
                args.source_run_configs,
                archive_root / "provenance" / "configs",
                ("marita_*.yaml", "marita_*.yml"),
            )
        )
    if args.source_config:
        specs.append(CopySpec(args.source_config, archive_root / "provenance" / "config"))
    if args.source_pipeline_log:
        specs.append(CopySpec(args.source_pipeline_log, archive_root / "raw" / "logs" / "pipeline"))

    copied: list[RemoteFile] = []
    for spec in specs:
        copied.extend(_rsync_copy(args.host, spec))
    copy_checks = verify_copied_checksums(args.host, copied)
    for check in copy_checks:
        check["archive_path"] = _archive_relative(Path(check["archive_path"]), archive_root)

    provenance = collect_provenance(args.host, args.source_repo)
    _write_json(archive_root / "provenance" / "host_and_git.json", provenance)
    source_paths = {
        key: value
        for key, value in {
            "host": args.host,
            "database_dir": args.source_databases,
            "results": args.source_results,
            "logs": args.source_logs,
            "timings": args.source_timings,
            "run_configs": args.source_run_configs,
            "config": args.source_config,
            "pipeline_log": args.source_pipeline_log,
            "repo": args.source_repo,
        }.items()
        if value
    }
    _write_json(archive_root / "provenance" / "source_paths.json", source_paths)

    database_records = collect_remote_database_metadata(args.host, args.source_databases)
    attempts = parse_log_tree(archive_root / "raw" / "logs" / "MARITA")
    results_root = archive_root / "raw" / "results" / "MARITA"
    snapshots, normalized_rules = parse_artifact_snapshot(results_root)
    normalized_rule_path = archive_root / "derived" / "normalized_rules.jsonl"
    normalized_rule_path.parent.mkdir(parents=True, exist_ok=True)
    with normalized_rule_path.open("w", encoding="utf-8") as handle:
        for rule in normalized_rules:
            handle.write(json.dumps(rule, ensure_ascii=False, sort_keys=True) + "\n")

    config_path = archive_root / "provenance" / "config" / Path(args.source_config).name if args.source_config else None
    config = _load_config(config_path) if config_path and config_path.exists() else None
    manifest = build_manifest(
        archive_root=archive_root,
        manifest_path=manifest_path,
        host=args.host,
        source_paths=source_paths,
        database_records=database_records,
        attempts=attempts,
        snapshots=snapshots,
        normalized_rule_path=normalized_rule_path,
        copy_checks=copy_checks,
        provenance=provenance,
        config=config,
    )
    _write_json(manifest_path, manifest)
    _write_checksums(archive_root)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    today = dt.date.today().strftime("%Y%m%d")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="tipi01")
    parser.add_argument("--source-databases", required=True, help="Remote directory containing source .db files.")
    parser.add_argument("--source-results", required=True, help="Remote MARITA result directory.")
    parser.add_argument("--source-logs", required=True, help="Remote MARITA global-log directory.")
    parser.add_argument("--source-timings", required=True, help="Remote result timings directory.")
    parser.add_argument("--source-run-configs", help="Remote directory containing per-run configs.")
    parser.add_argument("--source-config", help="Remote benchmark profile config.")
    parser.add_argument("--source-pipeline-log", help="Remote pipeline log to preserve as provenance.")
    parser.add_argument("--source-repo", help="Remote repository path for Git provenance.")
    parser.add_argument("--output", default=f"results/marita_tipi01_{today}")
    parser.add_argument("--manifest", default=f"research/generated/marita_baseline_{today}.json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = capture(args)
    except (FileExistsError, FileNotFoundError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"capture failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
