from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import socket
import time
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tqdm import tqdm

from mahilda.audit.distributed import (
    LeaseHeartbeat,
    QueueLease,
    claim_job,
    finish_job,
    initialise_queue,
    queue_has_failures,
    queue_is_complete,
    read_queue_jobs,
    recover_stale_jobs,
    release_lock,
    try_acquire_lock,
    update_lease,
)
from mahilda.audit.evaluator import AuditEvaluationError, SQLiteRuleEvaluator
from mahilda.audit.matching import covered_on_instance, subsumes
from mahilda.audit.models import (
    AuditClassification,
    AuditRecord,
    MatchStatus,
    ParsedRule,
    RelationalRule,
    ScopeStatus,
    SourceRule,
)
from mahilda.audit.parsing import load_source_rules, parse_formula, parse_source_rule

if TYPE_CHECKING:
    from collections.abc import Iterable


STATE_PENDING = "pending"
STATE_RUNNING = "running"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_INTERRUPTED = "interrupted"

STATE_DIRNAME = ".audit_state"
CACHE_DIRNAME = ".audit_cache"
SHARDS_DIRNAME = "shards"
STATE_VERSION = 1
CACHE_VERSION = 1
SUCCESS_RUN_STATUS = "success"
STATUS_DIRNAME = "progress"


@dataclass(frozen=True)
class AuditConfig:
    results_dir: Path
    database_dir: Path
    output_dir: Path
    status_dir: Path | None = None
    target: str = "MAHILDA"
    competitors: tuple[str, ...] = ("AMIE3", "MATILDA", "SPIDER", "POPPER")
    confidence_threshold: float = 1.0
    max_examples: int = 25
    strict: bool = False
    show_progress: bool = True
    walk_length: int = 3
    max_tables: int = 3
    max_variables: int = 3
    disjoint_semantics: bool = True
    joinability: str = "fk"
    coverage: str = "alpha"
    diagnose_unmatched: bool = True
    include_amie_rdf: bool = False
    workers: int = 1
    resume: bool = False
    reset_state: bool = False
    status_only: bool = False
    reuse_cache: bool = False
    trust_legacy_cache: bool = False
    hosts: tuple[str, ...] = ()
    host: str | None = None
    heartbeat_seconds: int = 30
    stale_after_seconds: int = 7200
    max_attempts: int = 3

    @property
    def distributed(self) -> bool:
        return bool(self.hosts or self.host)


@dataclass(frozen=True)
class TargetRuleIndex:
    rules: list[RelationalRule]
    rules_by_canonical_key: dict[str, RelationalRule]
    rules_by_head_key: dict[str, list[RelationalRule]]


@dataclass(frozen=True)
class BenchmarkRunStatus:
    status: str
    rules_count: int | None
    partial: bool
    source_path: Path

    @property
    def signature(self) -> str:
        return _file_signature(self.source_path)


@dataclass(frozen=True)
class AuditShard:
    algorithm: str
    database: str
    database_path: Path
    sources: tuple[SourceRule, ...]
    target_index: TargetRuleIndex
    source_signature: str
    database_signature: str
    target_signature: str
    target_run_status: str
    run_status_signature: str

    @property
    def key(self) -> str:
        return f"{self.algorithm}__{self.database}"


@dataclass
class AuditRuntime:
    target_indexes_by_db: dict[str, TargetRuleIndex]
    evaluator_cache: dict[str, SQLiteRuleEvaluator] = field(default_factory=dict)
    projected_head_rows_cache: dict[str, dict[str, set[tuple[object, ...]]]] = field(
        default_factory=lambda: defaultdict(dict)
    )

    def get_evaluator(self, database: str, database_path: Path, *, disjoint_semantics: bool) -> SQLiteRuleEvaluator:
        evaluator = self.evaluator_cache.get(database)
        if evaluator is None:
            evaluator = SQLiteRuleEvaluator(database_path, relation_disjoint=disjoint_semantics)
            self.evaluator_cache[database] = evaluator
        return evaluator

    def close(self) -> None:
        while self.evaluator_cache:
            _, evaluator = self.evaluator_cache.popitem()
            evaluator.close()


@dataclass(frozen=True)
class ShardPaths:
    base_dir: Path
    csv_path: Path
    summary_path: Path
    unmatched_path: Path
    diagnosis_path: Path
    claims_path: Path
    state_path: Path


@dataclass(frozen=True)
class ShardResult:
    key: str
    algorithm: str
    database: str
    status: str
    processed_rules: int
    total_rules: int
    error: str | None = None


class AuditLeaseLostError(RuntimeError):
    """Raised when a distributed worker no longer owns its database lease."""


def run_audit(config: AuditConfig) -> list[AuditRecord]:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    if config.reuse_cache:
        return _run_cached_audit(config)
    shards = build_audit_plan(config)
    if config.status_only:
        return []
    state_dir = _state_dir(config)
    existing_state = state_dir.exists()

    if config.reset_state and existing_state:
        if config.distributed and _distributed_jobs_running(config):
            raise SystemExit("Cannot reset active distributed audit state while jobs are running.")
        shutil.rmtree(state_dir)
    elif existing_state and not (config.resume or config.status_only or config.distributed):
        raise SystemExit("Existing audit state found. Use --resume, --status, or --reset-state.")

    manifest = _build_manifest(config, shards)
    _initialize_state(config, shards, manifest)
    if config.distributed:
        _initialize_distributed_queue(config, shards, manifest)

    try:
        if config.distributed:
            _execute_distributed(config, shards)
        else:
            _execute_shards(config, shards)
    except KeyboardInterrupt as exc:
        _refresh_run_summary(config, shards)
        raise SystemExit(130) from exc

    if config.distributed and not _distributed_run_complete(config):
        _refresh_run_summary(config, shards)
        raise SystemExit("Distributed audit is incomplete; inspect --status and retry with --resume.")

    records = _collect_records(config, shards)
    owns_finalization_lock = not config.distributed or _finalize_distributed(config)
    if owns_finalization_lock:
        try:
            _write_outputs(config, records)
            _write_evaluation_cache(config, shards, records)
            if config.distributed:
                _mark_distributed_finalized(config)
        except Exception:
            if config.distributed:
                _release_finalization_lock(config)
            raise
    _refresh_run_summary(config, shards)
    if config.strict and any(record.match_status == MatchStatus.UNMATCHED for record in records):
        raise SystemExit(2)
    return records


def _run_cached_audit(config: AuditConfig) -> list[AuditRecord]:
    """Rematch cached evaluations without re-parsing competitors or querying SQLite."""
    if config.coverage == "instance":
        raise ValueError("--reuse-cache does not support instance coverage; it requires fresh SQLite projections.")
    shards = build_audit_plan(config)
    records, missing_shards = _load_reusable_records(config, shards)
    if missing_shards:
        records.extend(_evaluate_missing_shards(config, missing_shards))
    target_indexes = {shard.database: shard.target_index for shard in shards}
    rematched: list[AuditRecord] = []
    for record in records:
        target_index = target_indexes.get(record.database, _empty_target_rule_index())
        rematched.append(_rematch_cached_record(config, record, target_index))

    _write_outputs(config, rematched)
    _write_evaluation_cache(config, shards, rematched)
    if config.strict and any(record.match_status == MatchStatus.UNMATCHED for record in rematched):
        raise SystemExit(2)
    return rematched


def _evaluate_missing_shards(config: AuditConfig, shards: list[AuditShard]) -> list[AuditRecord]:
    """Evaluate only shards absent from the reusable cache."""
    records: list[AuditRecord] = []
    for shard in shards:
        records.extend(_evaluate_shard_records(config, shard))
    return records


def _evaluate_shard_records(config: AuditConfig, shard: AuditShard) -> list[AuditRecord]:
    runtime = AuditRuntime(target_indexes_by_db={shard.database: shard.target_index})
    try:
        progress = tqdm(
            shard.sources,
            desc=f"Auditing {shard.algorithm}/{shard.database}",
            disable=not config.show_progress,
            unit="rule",
        )
        return [_audit_rule(config, runtime, parse_source_rule(source), shard.target_index) for source in progress]
    finally:
        runtime.close()


def _load_reusable_records(config: AuditConfig, shards: list[AuditShard]) -> tuple[list[AuditRecord], list[AuditShard]]:
    cache_dir = config.output_dir / CACHE_DIRNAME
    cache_manifest = cache_dir / "manifest.json"
    if cache_manifest.exists():
        manifest = json.loads(cache_manifest.read_text(encoding="utf-8"))
        _validate_cache_manifest(config, shards, manifest)
        records: list[AuditRecord] = []
        missing: list[AuditShard] = []
        for shard in shards:
            path = cache_dir / SHARDS_DIRNAME / shard.algorithm / shard.database / "audit_rules.csv"
            if not path.exists():
                missing.append(shard)
                continue
            cached = _load_records_from_csv(path)
            if not _cache_records_match_shard(cached, shard):
                missing.append(shard)
                continue
            records.extend(cached)
        return records, missing

    legacy_path = config.output_dir / "audit_rules.csv"
    if not config.trust_legacy_cache:
        raise SystemExit(
            f"No reusable audit cache found at {cache_dir}. Use --trust-legacy-cache to import {legacy_path}."
        )
    if not legacy_path.exists():
        raise SystemExit(f"Legacy audit cache not found: {legacy_path}")

    legacy_records = _load_records_from_csv(legacy_path)
    records_by_shard: dict[tuple[str, str], list[AuditRecord]] = defaultdict(list)
    for record in legacy_records:
        records_by_shard[(record.algorithm, record.database)].append(record)
    reusable: list[AuditRecord] = []
    missing = []
    for shard in shards:
        cached = records_by_shard.get((shard.algorithm, shard.database), [])
        if _cache_records_match_shard(cached, shard):
            reusable.extend(cached)
        else:
            missing.append(shard)
    return reusable, missing


def _cache_records_match_shard(records: list[AuditRecord], shard: AuditShard) -> bool:
    if len(records) != len(shard.sources):
        return False
    by_index = {record.rule_index: record for record in records}
    return all(
        source.index in by_index
        and by_index[source.index].algorithm == shard.algorithm
        and by_index[source.index].database == shard.database
        and by_index[source.index].display == source.display
        for source in shard.sources
    )


def _validate_cache_manifest(config: AuditConfig, shards: list[AuditShard], manifest: dict[str, Any]) -> None:
    if manifest.get("version") != CACHE_VERSION:
        raise SystemExit("Audit cache version is incompatible; rerun without --reuse-cache.")
    expected = _evaluation_manifest(config, shards)
    if manifest.get("fingerprint") != expected["fingerprint"]:
        raise SystemExit("Audit cache does not match current evaluation inputs; rerun without --reuse-cache.")


def _evaluation_manifest(config: AuditConfig, shards: list[AuditShard]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": CACHE_VERSION,
        "target": config.target,
        "walk_length": config.walk_length,
        "max_tables": config.max_tables,
        "max_variables": config.max_variables,
        "disjoint_semantics": config.disjoint_semantics,
        "joinability": config.joinability,
        "results_dir": str(config.results_dir),
        "database_dir": str(config.database_dir),
        "status_dir": str(config.status_dir or config.results_dir / STATUS_DIRNAME),
        "shards": [
            {
                "key": shard.key,
                "algorithm": shard.algorithm,
                "database": shard.database,
                "source_signature": shard.source_signature,
                "database_signature": shard.database_signature,
                "target_signature": shard.target_signature,
                "target_run_status": shard.target_run_status,
                "run_status_signature": shard.run_status_signature,
                "total_rules": len(shard.sources),
            }
            for shard in shards
        ],
    }
    payload["fingerprint"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return payload


def _rematch_cached_record(config: AuditConfig, record: AuditRecord, target_index: TargetRuleIndex) -> AuditRecord:
    if record.classification in {
        AuditClassification.PARSE_FAILED,
        AuditClassification.OUT_OF_SCOPE,
        AuditClassification.VACUOUS,
    }:
        return replace(record, match_status=MatchStatus.NOT_APPLICABLE, matched_rule="")
    if record.canonical_rule == "" or record.scope_status != ScopeStatus.IN_TARGET_CLASS:
        return replace(record, match_status=MatchStatus.NOT_APPLICABLE, matched_rule="")

    classification = record.classification
    if record.confidence is not None:
        classification = (
            AuditClassification.COMPARABLE_TRUE
            if record.confidence >= config.confidence_threshold
            else AuditClassification.APPROXIMATE
        )
    if classification != AuditClassification.COMPARABLE_TRUE:
        return replace(
            record,
            classification=classification,
            match_status=MatchStatus.NOT_APPLICABLE,
            coverage_alpha=False,
            coverage_subsumption=False,
            coverage_instance=False,
            claim_relevant=False,
            diagnosis=classification.value,
            reason="confidence_below_threshold",
            matched_rule="",
        )

    try:
        rule = parse_formula(record.canonical_rule)
    except ValueError:
        return replace(
            record,
            classification=AuditClassification.PARSE_FAILED,
            match_status=MatchStatus.NOT_APPLICABLE,
            claim_relevant=False,
            diagnosis=ScopeStatus.NOT_PARSED.value,
            reason="cached_canonical_rule_unparseable",
            matched_rule="",
        )
    match_status, matched_rule = _match_rule(
        evaluator=None,
        rule=rule,
        coverage=config.coverage,
        target_index=target_index,
        projected_head_rows_cache={},
    )
    return replace(
        record,
        classification=classification,
        match_status=match_status,
        coverage_alpha=match_status == MatchStatus.RECALLED_ALPHA,
        coverage_subsumption=match_status in {MatchStatus.RECALLED_ALPHA, MatchStatus.RECALLED_SUBSUMED},
        coverage_instance=match_status
        in {
            MatchStatus.RECALLED_ALPHA,
            MatchStatus.RECALLED_SUBSUMED,
            MatchStatus.COVERED_ON_INSTANCE,
        },
        diagnosis=_diagnosis_for_match(match_status),
        claim_relevant=True,
        reason="matched" if match_status != MatchStatus.UNMATCHED else "unmatched",
        matched_rule=matched_rule,
    )


def build_audit_plan(config: AuditConfig) -> list[AuditShard]:
    run_statuses, status_metadata_available = _load_benchmark_run_statuses(config)
    target_sources = load_source_rules(config.results_dir, config.target)
    target_sources_by_database: dict[str, list[SourceRule]] = defaultdict(list)
    for source in target_sources:
        target_sources_by_database[source.database].append(source)

    eligible_target_databases: set[str] = set()
    target_statuses: dict[str, BenchmarkRunStatus] = {}
    for database, sources in target_sources_by_database.items():
        status = run_statuses.get((config.target, database))
        if _target_run_is_auditable(
            status,
            result_rule_count=len(sources),
            status_metadata_available=status_metadata_available,
        ):
            eligible_target_databases.add(database)
            if status is not None:
                target_statuses[database] = status

    # A successful run that exported zero rules has no source rows, so status
    # metadata is also authoritative for databases absent from the target
    # result glob.
    if status_metadata_available:
        for (algorithm, database), status in run_statuses.items():
            if algorithm != config.target:
                continue
            if _target_run_is_auditable(
                status,
                result_rule_count=len(target_sources_by_database.get(database, [])),
                status_metadata_available=True,
            ):
                eligible_target_databases.add(database)
                target_statuses[database] = status

    target_indexes = _build_target_rule_indexes(
        target_sources_by_database,
        eligible_databases=eligible_target_databases if status_metadata_available else None,
    )
    shards: list[AuditShard] = []
    for algorithm in config.competitors:
        if algorithm == "AMIE3" and not config.include_amie_rdf:
            continue
        grouped: dict[str, list[SourceRule]] = defaultdict(list)
        for source in load_source_rules(config.results_dir, algorithm):
            if status_metadata_available and source.database not in eligible_target_databases:
                continue
            grouped[source.database].append(source)
        for database, sources in sorted(grouped.items()):
            database_path = config.database_dir / f"{database}.db"
            target_status = target_statuses.get(database)
            shards.append(
                AuditShard(
                    algorithm=algorithm,
                    database=database,
                    database_path=database_path,
                    sources=tuple(sources),
                    target_index=target_indexes.get(database, _empty_target_rule_index()),
                    source_signature=_file_signature(sources[0].source_path) if sources else "",
                    database_signature=_file_signature(database_path),
                    target_signature=_target_signature(config, database),
                    target_run_status=target_status.status if target_status is not None else "untracked",
                    run_status_signature=target_status.signature if target_status is not None else "untracked",
                )
            )
    return shards


def get_audit_status(config: AuditConfig) -> dict[str, object]:
    shards = build_audit_plan(config)
    if config.distributed:
        state_dir = _state_dir(config)
        if not (state_dir / "queue" / "manifest.json").exists():
            return _planned_distributed_status(config, shards)
        return _distributed_status(config, shards)
    _initialize_state(config, shards, _build_manifest(config, shards))
    return _refresh_run_summary(config, shards)


def _execute_shards(config: AuditConfig, shards: list[AuditShard]) -> None:
    pending = {
        shard.key: shard
        for shard in shards
        if _shard_state(_shard_paths(config, shard)).get("status") != STATE_COMPLETED
    }
    if not pending:
        return
    if config.workers <= 1:
        for shard in pending.values():
            _run_audit_shard(config, shard)
            _refresh_run_summary(config, shards)
        return

    max_workers = max(1, config.workers)
    in_flight: dict[Future[ShardResult], AuditShard] = {}
    active_databases: set[str] = set()
    remaining = list(pending.values())
    progress = tqdm(
        total=sum(len(shard.sources) for shard in shards),
        initial=sum(
            _int_from_state(_shard_state(_shard_paths(config, shard)).get("processed_rules")) for shard in shards
        ),
        desc="Auditing",
        disable=not config.show_progress,
        unit="rule",
    )
    displayed_rules = progress.n if config.show_progress else 0
    try:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            while remaining or in_flight:
                while remaining and len(in_flight) < max_workers:
                    shard = next(
                        (candidate for candidate in remaining if candidate.database not in active_databases), None
                    )
                    if shard is None:
                        break
                    remaining.remove(shard)
                    future = executor.submit(_run_audit_shard, config, shard)
                    in_flight[future] = shard
                    active_databases.add(shard.database)
                if not in_flight:
                    continue
                done, _ = wait(in_flight.keys(), timeout=0.2, return_when=FIRST_COMPLETED)
                current_rules = sum(
                    _int_from_state(_shard_state(_shard_paths(config, shard)).get("processed_rules"))
                    for shard in shards
                )
                progress.update(current_rules - displayed_rules)
                displayed_rules = current_rules
                for future in done:
                    shard = in_flight.pop(future)
                    active_databases.discard(shard.database)
                    future.result()
                    _refresh_run_summary(config, shards)
    finally:
        progress.close()


def _initialize_distributed_queue(
    config: AuditConfig,
    shards: list[AuditShard],
    manifest: dict[str, object],
) -> None:
    jobs = [
        {
            "job_id": database,
            "database": database,
            "shards": [shard.key for shard in shards if shard.database == database],
            "total_rules": sum(len(shard.sources) for shard in shards if shard.database == database),
        }
        for database in sorted({shard.database for shard in shards})
    ]
    queue_manifest = {
        "version": 1,
        "audit_fingerprint": manifest["fingerprint"],
        "jobs": jobs,
        "stale_after_seconds": config.stale_after_seconds,
        "fingerprint": hashlib.sha256(
            json.dumps({"audit_fingerprint": manifest["fingerprint"], "jobs": jobs}, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }
    initialise_queue(
        _queue_dir(config),
        queue_manifest,
        jobs,
        retry_failed=config.resume,
        max_attempts=config.max_attempts,
    )


def _execute_distributed(config: AuditConfig, shards: list[AuditShard]) -> None:
    queue_dir = _queue_dir(config)
    host = config.host or "local"
    by_database: dict[str, list[AuditShard]] = defaultdict(list)
    for shard in shards:
        by_database[shard.database].append(shard)
    workers = max(1, config.workers)
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(_distributed_worker_loop, config, by_database, queue_dir, host) for _ in range(workers)
        ]
        for future in futures:
            future.result()


def _distributed_worker_loop(
    config: AuditConfig,
    shards_by_database: dict[str, list[AuditShard]],
    queue_dir: Path,
    host: str,
) -> None:
    while True:
        recover_stale_jobs(queue_dir, config.stale_after_seconds, max_attempts=config.max_attempts)
        claimed = claim_job(queue_dir, host)
        if claimed is None:
            jobs = read_queue_jobs(queue_dir, stale_after_seconds=config.stale_after_seconds)
            if any(job.get("queue_state") in {"pending", "running"} for job in jobs):
                time.sleep(1)
                continue
            return
        lease, payload = claimed
        heartbeat = LeaseHeartbeat(lease, config.heartbeat_seconds)
        heartbeat.start()
        database = str(payload["database"])
        try:
            database_shards = shards_by_database.get(database, [])
            runtime = AuditRuntime(
                target_indexes_by_db={database: database_shards[0].target_index} if database_shards else {}
            )
            try:
                for shard in database_shards:
                    _assert_lease_owned(lease)
                    _run_audit_shard(config, shard, runtime=runtime)
                _assert_lease_owned(lease)
            finally:
                runtime.close()
                update_lease(
                    lease,
                    {
                        "processed_shards": sum(
                            _shard_state(_shard_paths(config, candidate)).get("status") == STATE_COMPLETED
                            for candidate in database_shards
                        ),
                        "processed_rules": sum(
                            _int_from_state(_shard_state(_shard_paths(config, candidate)).get("processed_rules"))
                            for candidate in database_shards
                        ),
                    },
                )
            finish_job(
                lease,
                status="done",
                result={
                    "database": database,
                    "shards": [shard.key for shard in shards_by_database.get(database, [])],
                },
            )
        except KeyboardInterrupt:
            finish_job(lease, status="failed", result={"error": "interrupted", "status": "interrupted"})
            raise
        except Exception as exc:
            finish_job(lease, status="failed", result={"error": str(exc), "status": "error"})
        finally:
            heartbeat.stop()


def _distributed_run_complete(config: AuditConfig) -> bool:
    queue_dir = _queue_dir(config)
    if queue_has_failures(queue_dir):
        return False
    return queue_is_complete(queue_dir)


def _finalize_distributed(config: AuditConfig) -> bool:
    marker = _state_dir(config) / "finalized.json"
    if marker.exists():
        return False
    lock = _state_dir(config) / "finalize.lock"
    if not try_acquire_lock(lock):
        for _ in range(max(1, config.heartbeat_seconds * 2)):
            if marker.exists():
                return False
            time.sleep(0.5)
        return False
    return True


def _mark_distributed_finalized(config: AuditConfig) -> None:
    marker = _state_dir(config) / "finalized.json"
    try:
        marker.write_text(json.dumps({"completed_at": _utc_now()}, indent=2), encoding="utf-8")
    finally:
        _release_finalization_lock(config)


def _release_finalization_lock(config: AuditConfig) -> None:
    lock = _state_dir(config) / "finalize.lock"
    with suppress(FileNotFoundError):
        release_lock(lock)


def _distributed_jobs_running(config: AuditConfig) -> bool:
    return bool(list((_queue_dir(config) / "running").glob("*.json")))


def _queue_dir(config: AuditConfig) -> Path:
    return _state_dir(config) / "queue"


def _distributed_status(config: AuditConfig, shards: list[AuditShard]) -> dict[str, object]:
    jobs = read_queue_jobs(_queue_dir(config), stale_after_seconds=config.stale_after_seconds)
    shards_by_key = {shard.key: shard for shard in shards}
    total_rules = sum(len(shard.sources) for shard in shards)
    processed_rules = 0
    for job in jobs:
        job_shards = [shards_by_key[key] for key in job.get("shards", []) if key in shards_by_key]
        job["total_rules"] = sum(len(shard.sources) for shard in job_shards)
        job["processed_rules"] = sum(
            _int_from_state(_shard_state(_shard_paths(config, shard)).get("processed_rules")) for shard in job_shards
        )
        job["processed_shards"] = sum(
            _shard_state(_shard_paths(config, shard)).get("status") == STATE_COMPLETED for shard in job_shards
        )
        processed_rules += int(job["processed_rules"])
    totals = {
        "shards": len(shards),
        "pending": sum(job.get("queue_state") == "pending" for job in jobs),
        "running": sum(job.get("queue_state") == "running" and not job.get("stale") for job in jobs),
        "completed": sum(job.get("queue_state") == "done" for job in jobs),
        "failed": sum(job.get("queue_state") == "failed" for job in jobs),
        "interrupted": 0,
        "stale": sum(bool(job.get("stale")) for job in jobs),
        "processed_rules": processed_rules,
        "total_rules": total_rules,
    }
    return {
        "updated_at": _utc_now(),
        "totals": totals,
        "shards": jobs,
        "finalized": (_state_dir(config) / "finalized.json").exists(),
    }


def _planned_distributed_status(config: AuditConfig, shards: list[AuditShard]) -> dict[str, object]:
    databases = sorted({shard.database for shard in shards})
    return {
        "updated_at": _utc_now(),
        "totals": {
            "shards": len(shards),
            "pending": len(databases),
            "running": 0,
            "completed": 0,
            "failed": 0,
            "interrupted": 0,
            "stale": 0,
            "processed_rules": 0,
            "total_rules": sum(len(shard.sources) for shard in shards),
        },
        "shards": [
            {
                "job_id": database,
                "database": database,
                "status": "pending",
                "queue_state": "pending",
            }
            for database in databases
        ],
        "finalized": False,
    }


def _assert_lease_owned(lease: QueueLease) -> None:
    try:
        payload = json.loads(lease.path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        raise AuditLeaseLostError(f"Audit lease disappeared for {lease.job_id}") from None
    if payload.get("lease_token") != lease.token:
        raise AuditLeaseLostError(f"Audit lease was replaced for {lease.job_id}")


def _run_audit_shard(
    config: AuditConfig,
    shard: AuditShard,
    *,
    runtime: AuditRuntime | None = None,
) -> ShardResult:
    paths = _shard_paths(config, shard)
    state = _shard_state(paths)
    start_index = _int_from_state(state.get("processed_rules")) if config.resume else 0
    if state.get("status") == STATE_COMPLETED:
        return ShardResult(
            key=shard.key,
            algorithm=shard.algorithm,
            database=shard.database,
            status=STATE_COMPLETED,
            processed_rules=len(shard.sources),
            total_rules=len(shard.sources),
        )

    owns_runtime = runtime is None
    if runtime is None:
        runtime = AuditRuntime(target_indexes_by_db={shard.database: shard.target_index})
    iterator: Iterable[SourceRule]
    iterator = shard.sources[start_index:]
    progress = tqdm(
        iterator,
        desc=f"Auditing {shard.algorithm}/{shard.database}",
        disable=not config.show_progress or config.workers > 1,
        unit="rule",
        total=len(shard.sources),
        initial=start_index,
    )
    processed_rules = start_index
    try:
        _write_shard_state(
            paths,
            shard,
            status=STATE_RUNNING,
            processed_rules=processed_rules,
            total_rules=len(shard.sources),
            last_rule_index=shard.sources[processed_rules - 1].index if processed_rules else None,
            error=None,
        )
        if start_index == 0:
            _initialize_rules_csv(paths.csv_path)
        for source in progress:
            parsed = parse_source_rule(source)
            record = _audit_rule(config, runtime, parsed, shard.target_index)
            _append_record(paths.csv_path, record)
            processed_rules += 1
            _write_shard_state(
                paths,
                shard,
                status=STATE_RUNNING,
                processed_rules=processed_rules,
                total_rules=len(shard.sources),
                last_rule_index=source.index,
                error=None,
            )
        records = _load_records_from_csv(paths.csv_path)
        _write_outputs_in_dir(
            paths.base_dir,
            records,
            config,
            summary_path=paths.summary_path,
            unmatched_path=paths.unmatched_path,
            diagnosis_path=paths.diagnosis_path,
            claims_path=paths.claims_path,
        )
        _write_shard_state(
            paths,
            shard,
            status=STATE_COMPLETED,
            processed_rules=processed_rules,
            total_rules=len(shard.sources),
            last_rule_index=shard.sources[-1].index if shard.sources else None,
            error=None,
        )
        return ShardResult(
            key=shard.key,
            algorithm=shard.algorithm,
            database=shard.database,
            status=STATE_COMPLETED,
            processed_rules=processed_rules,
            total_rules=len(shard.sources),
        )
    except KeyboardInterrupt:
        _write_shard_state(
            paths,
            shard,
            status=STATE_INTERRUPTED,
            processed_rules=processed_rules,
            total_rules=len(shard.sources),
            last_rule_index=shard.sources[processed_rules - 1].index if processed_rules else None,
            error="interrupted",
        )
        raise
    except Exception as exc:
        _write_shard_state(
            paths,
            shard,
            status=STATE_FAILED,
            processed_rules=processed_rules,
            total_rules=len(shard.sources),
            last_rule_index=shard.sources[processed_rules - 1].index if processed_rules else None,
            error=str(exc),
        )
        raise
    finally:
        if owns_runtime:
            runtime.close()


def _state_dir(config: AuditConfig) -> Path:
    return config.output_dir / STATE_DIRNAME


def _manifest_path(config: AuditConfig) -> Path:
    return _state_dir(config) / "manifest.json"


def _run_summary_path(config: AuditConfig) -> Path:
    return _state_dir(config) / "run_summary.json"


def _shard_paths(config: AuditConfig, shard: AuditShard) -> ShardPaths:
    base_dir = config.output_dir / SHARDS_DIRNAME / shard.algorithm / shard.database
    return ShardPaths(
        base_dir=base_dir,
        csv_path=base_dir / "audit_rules.csv",
        summary_path=base_dir / "summary.json",
        unmatched_path=base_dir / "unmatched.md",
        diagnosis_path=base_dir / "diagnosis.md",
        claims_path=base_dir / "claims.md",
        state_path=_state_dir(config) / "shards" / f"{shard.key}.json",
    )


def _initialize_state(config: AuditConfig, shards: list[AuditShard], manifest: dict[str, object]) -> None:
    state_dir = _state_dir(config)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "shards").mkdir(parents=True, exist_ok=True)
    manifest_path = _manifest_path(config)
    if manifest_path.exists():
        current = json.loads(manifest_path.read_text(encoding="utf-8"))
        if current.get("fingerprint") != manifest["fingerprint"] and not config.reset_state:
            raise SystemExit("Audit state does not match current inputs. Use --reset-state to recompute.")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for shard in shards:
        paths = _shard_paths(config, shard)
        paths.base_dir.mkdir(parents=True, exist_ok=True)
        if not paths.state_path.exists():
            _write_shard_state(
                paths,
                shard,
                status=STATE_PENDING,
                processed_rules=0,
                total_rules=len(shard.sources),
                last_rule_index=None,
                error=None,
            )


def _refresh_run_summary(config: AuditConfig, shards: list[AuditShard]) -> dict[str, object]:
    states = [_shard_state(_shard_paths(config, shard)) for shard in shards]
    summary = {
        "updated_at": _utc_now(),
        "totals": {
            "shards": len(states),
            "pending": sum(state.get("status") == STATE_PENDING for state in states),
            "running": sum(state.get("status") == STATE_RUNNING for state in states),
            "completed": sum(state.get("status") == STATE_COMPLETED for state in states),
            "failed": sum(state.get("status") == STATE_FAILED for state in states),
            "interrupted": sum(state.get("status") == STATE_INTERRUPTED for state in states),
            "processed_rules": sum(_int_from_state(state.get("processed_rules")) for state in states),
            "total_rules": sum(_int_from_state(state.get("total_rules")) for state in states),
        },
        "shards": states,
    }
    _atomic_write_text(_run_summary_path(config), json.dumps(summary, indent=2))
    return summary


def _build_manifest(config: AuditConfig, shards: list[AuditShard]) -> dict[str, object]:
    payload = {
        "version": STATE_VERSION,
        "target": config.target,
        "competitors": list(config.competitors),
        "coverage": config.coverage,
        "confidence_threshold": config.confidence_threshold,
        "walk_length": config.walk_length,
        "max_tables": config.max_tables,
        "max_variables": config.max_variables,
        "disjoint_semantics": config.disjoint_semantics,
        "joinability": config.joinability,
        "diagnose_unmatched": config.diagnose_unmatched,
        "include_amie_rdf": config.include_amie_rdf,
        "results_dir": str(config.results_dir),
        "database_dir": str(config.database_dir),
        "status_dir": str(config.status_dir or config.results_dir / STATUS_DIRNAME),
        "shards": [
            {
                "key": shard.key,
                "algorithm": shard.algorithm,
                "database": shard.database,
                "source_signature": shard.source_signature,
                "target_signature": shard.target_signature,
                "target_run_status": shard.target_run_status,
                "run_status_signature": shard.run_status_signature,
                "total_rules": len(shard.sources),
            }
            for shard in shards
        ],
    }
    payload["fingerprint"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return payload


def _write_shard_state(
    paths: ShardPaths,
    shard: AuditShard,
    *,
    status: str,
    processed_rules: int,
    total_rules: int,
    last_rule_index: int | None,
    error: str | None,
) -> None:
    now = _utc_now()
    previous = _shard_state(paths)
    payload = {
        "key": shard.key,
        "algorithm": shard.algorithm,
        "database": shard.database,
        "status": status,
        "processed_rules": processed_rules,
        "total_rules": total_rules,
        "last_rule_index": last_rule_index,
        "error": error,
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "updated_at": now,
        "started_at": previous.get("started_at") or now,
        "completed_at": now if status == STATE_COMPLETED else None,
        "csv_path": str(paths.csv_path),
        "summary_path": str(paths.summary_path),
        "source_signature": shard.source_signature,
        "target_signature": shard.target_signature,
        "target_run_status": shard.target_run_status,
        "run_status_signature": shard.run_status_signature,
    }
    _atomic_write_text(paths.state_path, json.dumps(payload, indent=2))


def _atomic_write_text(path: Path, content: str) -> None:
    """Replace a checkpoint file without exposing a partially-written JSON file."""
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary_path.write_text(content, encoding="utf-8")
    os.replace(temporary_path, path)


def _shard_state(paths: ShardPaths) -> dict[str, Any]:
    if not paths.state_path.exists():
        return {}
    return json.loads(paths.state_path.read_text(encoding="utf-8"))


def _collect_records(config: AuditConfig, shards: list[AuditShard]) -> list[AuditRecord]:
    records: list[AuditRecord] = []
    for shard in sorted(shards, key=lambda item: (item.algorithm, item.database)):
        paths = _shard_paths(config, shard)
        if not paths.csv_path.exists():
            continue
        records.extend(_load_records_from_csv(paths.csv_path))
    return sorted(records, key=lambda record: (record.algorithm, record.database, record.rule_index))


def _load_records_from_csv(path: Path) -> list[AuditRecord]:
    if not path.exists():
        return []
    records: list[AuditRecord] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            records.append(
                AuditRecord(
                    algorithm=row["algorithm"],
                    database=row["database"],
                    source_path=row["source_path"],
                    rule_index=int(row["rule_index"]),
                    classification=AuditClassification(row["classification"]),
                    match_status=MatchStatus(row["match_status"]),
                    scope_status=ScopeStatus(row["scope_status"]),
                    scope_reason=row["scope_reason"],
                    target_class_member=row["target_class_member"] == "True",
                    coverage_alpha=row["coverage_alpha"] == "True",
                    coverage_subsumption=row["coverage_subsumption"] == "True",
                    coverage_instance=row["coverage_instance"] == "True",
                    diagnosis=row["diagnosis"],
                    claim_relevant=row["claim_relevant"] == "True",
                    reason=row["reason"],
                    support=_optional_int(row["support"]),
                    predictions=_optional_int(row["predictions"]),
                    confidence=_optional_float(row["confidence"]),
                    canonical_rule=row["canonical_rule"],
                    matched_rule=row["matched_rule"],
                    display=row["display"],
                )
            )
    return records


def _initialize_rules_csv(path: Path) -> None:
    fieldnames = list(AuditRecord.__dataclass_fields__.keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.DictWriter(handle, fieldnames=fieldnames).writeheader()


def _append_record(path: Path, record: AuditRecord) -> None:
    fieldnames = list(AuditRecord.__dataclass_fields__.keys())
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writerow({field: getattr(record, field) for field in fieldnames})


def _empty_target_rule_index() -> TargetRuleIndex:
    return TargetRuleIndex(rules=[], rules_by_canonical_key={}, rules_by_head_key={})


def _build_target_rule_indexes(
    sources_by_database: dict[str, list[SourceRule]],
    *,
    eligible_databases: set[str] | None,
) -> dict[str, TargetRuleIndex]:
    by_database: dict[str, list[RelationalRule]] = defaultdict(list)
    for database, sources in sources_by_database.items():
        if eligible_databases is not None and database not in eligible_databases:
            continue
        for source in sources:
            parsed = parse_source_rule(source)
            if parsed.rule is not None:
                by_database[database].append(parsed.rule)

    indexes: dict[str, TargetRuleIndex] = {}
    for database, rules in by_database.items():
        rules_by_canonical_key: dict[str, RelationalRule] = {}
        rules_by_head_key: dict[str, list[RelationalRule]] = defaultdict(list)
        for rule in rules:
            canonical_key = rule.canonical_key()
            rules_by_canonical_key.setdefault(canonical_key, rule)
            rules_by_head_key[rule.canonical_head_key()].append(rule)
        indexes[database] = TargetRuleIndex(
            rules=rules,
            rules_by_canonical_key=rules_by_canonical_key,
            rules_by_head_key=dict(rules_by_head_key),
        )
    return indexes


def _load_benchmark_run_statuses(
    config: AuditConfig,
) -> tuple[dict[tuple[str, str], BenchmarkRunStatus], bool]:
    """Load paper-benchmark status records when the results root has them.

    The per-run progress files are the preferred source. Aggregate summaries
    are read as a fallback because older distributed runs may have persisted a
    summary without retaining the progress directory. If neither artifact is
    present, the audit keeps its legacy result-glob behavior.
    """
    status_dir = config.status_dir or config.results_dir / STATUS_DIRNAME
    candidates: list[tuple[tuple[float, int, str], Path, dict[str, Any]]] = []
    if status_dir.exists():
        for path in sorted(status_dir.glob("*.json")):
            payload = _read_status_payload(path)
            if payload is None or "runs" in payload:
                continue
            candidates.append((_status_sort_key(payload, path, priority=1), path, payload))

    for path in sorted(config.results_dir.glob("summary*.json")):
        payload = _read_status_payload(path)
        if payload is None:
            continue
        runs = payload.get("runs")
        if not isinstance(runs, list):
            continue
        for run in runs:
            if isinstance(run, dict):
                candidates.append((_status_sort_key(run, path, priority=0), path, run))

    statuses: dict[tuple[str, str], BenchmarkRunStatus] = {}
    for _, path, payload in sorted(candidates, key=lambda item: item[0]):
        algorithm = str(payload.get("algorithm") or "").strip().upper()
        database = _normalise_database_name(payload.get("database"))
        if not algorithm or not database:
            continue
        statuses[(algorithm, database)] = BenchmarkRunStatus(
            status=str(payload.get("status") or "missing").strip().lower(),
            rules_count=_optional_status_int(payload.get("rules_count")),
            partial=bool(payload.get("partial")) or str(payload.get("status") or "").lower() == "partial",
            source_path=path,
        )

    metadata_available = status_dir.exists() or any(config.results_dir.glob("summary*.json"))
    return statuses, metadata_available


def _read_status_payload(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _status_sort_key(payload: dict[str, Any], path: Path, *, priority: int) -> tuple[float, int, str]:
    for key in ("updated_at", "ended_at", "completed_at", "started_at", "created_at"):
        value = payload.get(key)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp(), priority, str(path)
            except ValueError:
                pass
    try:
        timestamp = path.stat().st_mtime
    except OSError:
        timestamp = 0.0
    return timestamp, priority, str(path)


def _normalise_database_name(value: object) -> str:
    if value is None:
        return ""
    name = Path(str(value)).name
    return name.removesuffix(".db")


def _optional_status_int(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _target_run_is_auditable(
    status: BenchmarkRunStatus | None,
    *,
    result_rule_count: int,
    status_metadata_available: bool,
) -> bool:
    if not status_metadata_available:
        return True
    if status is None or status.status != SUCCESS_RUN_STATUS or status.partial:
        return False
    return status.rules_count is None or status.rules_count == result_rule_count


def _load_target_rule_indexes(config: AuditConfig) -> dict[str, TargetRuleIndex]:
    statuses, status_metadata_available = _load_benchmark_run_statuses(config)
    sources_by_database: dict[str, list[SourceRule]] = defaultdict(list)
    for source in load_source_rules(config.results_dir, config.target):
        sources_by_database[source.database].append(source)
    eligible_databases = {
        database
        for database, sources in sources_by_database.items()
        if _target_run_is_auditable(
            statuses.get((config.target, database)),
            result_rule_count=len(sources),
            status_metadata_available=status_metadata_available,
        )
    }
    return _build_target_rule_indexes(
        sources_by_database,
        eligible_databases=eligible_databases if status_metadata_available else None,
    )


def _audit_rule(
    config: AuditConfig,
    runtime: AuditRuntime,
    parsed: ParsedRule,
    target_index: TargetRuleIndex,
) -> AuditRecord:
    source = parsed.source
    rule = parsed.rule
    if rule is None:
        unsupported = bool(parsed.unsupported_reason and parsed.unsupported_reason.endswith("_rule"))
        classification = AuditClassification.OUT_OF_SCOPE if unsupported else AuditClassification.PARSE_FAILED
        scope_status = ScopeStatus.UNSUPPORTED_REPRESENTATION if unsupported else ScopeStatus.NOT_PARSED
        reason = parsed.unsupported_reason or "parse_failed"
        return _record(
            parsed=parsed,
            classification=classification,
            match_status=MatchStatus.NOT_APPLICABLE,
            scope_status=scope_status,
            scope_reason=reason,
            diagnosis=scope_status.value,
            reason=reason,
        )

    scope_status, scope_reason = _static_scope_status(config, rule)
    if scope_status != ScopeStatus.IN_TARGET_CLASS:
        return _record(
            parsed=parsed,
            classification=AuditClassification.OUT_OF_SCOPE,
            match_status=MatchStatus.NOT_APPLICABLE,
            scope_status=scope_status,
            scope_reason=scope_reason,
            diagnosis=scope_status.value,
            reason=scope_reason,
        )

    database_path = config.database_dir / f"{source.database}.db"
    if not database_path.exists():
        reason = f"missing_database:{database_path}"
        return _record(
            parsed=parsed,
            classification=AuditClassification.PARSE_FAILED,
            match_status=MatchStatus.NOT_APPLICABLE,
            scope_status=ScopeStatus.MISSING_DATABASE,
            scope_reason=reason,
            diagnosis=ScopeStatus.MISSING_DATABASE.value,
            reason=reason,
        )

    evaluator = runtime.get_evaluator(
        source.database,
        database_path,
        disjoint_semantics=config.disjoint_semantics,
    )
    try:
        if config.joinability == "fk" and not evaluator.is_fk_joinable(rule):
            return _record(
                parsed=parsed,
                classification=AuditClassification.OUT_OF_SCOPE,
                match_status=MatchStatus.NOT_APPLICABLE,
                scope_status=ScopeStatus.OUTSIDE_FK_JOINABILITY,
                scope_reason="not_foreign_key_joinable",
                diagnosis=ScopeStatus.OUTSIDE_FK_JOINABILITY.value,
                reason="not_foreign_key_joinable",
            )
        if _has_head_atom_in_body(rule) or (config.disjoint_semantics and evaluator.is_relation_disjoint_vacuous(rule)):
            return _record(
                parsed=parsed,
                classification=AuditClassification.VACUOUS,
                match_status=MatchStatus.NOT_APPLICABLE,
                scope_status=ScopeStatus.OUTSIDE_RELATION_DISJOINTNESS,
                scope_reason="vacuous_under_relation_disjoint_semantics",
                diagnosis=ScopeStatus.OUTSIDE_RELATION_DISJOINTNESS.value,
                reason="vacuous_under_relation_disjoint_semantics",
            )
        evaluation = evaluator.evaluate(rule)
    except AuditEvaluationError as exc:
        reason = str(exc)
        scope_status = (
            ScopeStatus.MISSING_PK if reason.startswith("missing_primary_key") else ScopeStatus.EVALUATOR_ERROR
        )
        return _record(
            parsed=parsed,
            classification=AuditClassification.OUT_OF_SCOPE,
            match_status=MatchStatus.NOT_APPLICABLE,
            scope_status=scope_status,
            scope_reason=reason,
            diagnosis=scope_status.value,
            reason=reason,
        )

    if evaluation.confidence < config.confidence_threshold:
        return _record(
            parsed=parsed,
            classification=AuditClassification.APPROXIMATE,
            match_status=MatchStatus.NOT_APPLICABLE,
            scope_status=ScopeStatus.IN_TARGET_CLASS,
            scope_reason=ScopeStatus.IN_TARGET_CLASS.value,
            diagnosis=AuditClassification.APPROXIMATE.value,
            reason="confidence_below_threshold",
            support=evaluation.support,
            predictions=evaluation.predictions,
            confidence=evaluation.confidence,
        )

    match_status, matched_rule = _match_rule(
        evaluator=evaluator,
        rule=rule,
        coverage=config.coverage,
        target_index=target_index,
        projected_head_rows_cache=runtime.projected_head_rows_cache[source.database],
    )
    return _record(
        parsed=parsed,
        classification=AuditClassification.COMPARABLE_TRUE,
        match_status=match_status,
        scope_status=ScopeStatus.IN_TARGET_CLASS,
        scope_reason=ScopeStatus.IN_TARGET_CLASS.value,
        diagnosis=_diagnosis_for_match(match_status),
        reason="matched" if match_status != MatchStatus.UNMATCHED else "unmatched",
        support=evaluation.support,
        predictions=evaluation.predictions,
        confidence=evaluation.confidence,
        matched_rule=matched_rule,
    )


def _static_scope_status(config: AuditConfig, rule: RelationalRule) -> tuple[ScopeStatus, str]:
    if not rule.body:
        return ScopeStatus.EMPTY_BODY, ScopeStatus.EMPTY_BODY.value
    if rule.head_variables() - rule.body_variables():
        return ScopeStatus.HEAD_ONLY_VARIABLE, "existential_or_head_only_variable"

    total_atoms = len(rule.body) + 1
    if total_atoms > config.walk_length:
        return ScopeStatus.OUTSIDE_BOUNDS, f"outside_walk_length:{total_atoms}>{config.walk_length}"

    tables = {atom.table for atom in rule.all_atoms()}
    if len(tables) > config.max_tables:
        return ScopeStatus.OUTSIDE_BOUNDS, f"outside_max_tables:{len(tables)}>{config.max_tables}"

    variables = rule.body_variables() | rule.head_variables()
    if len(variables) > config.max_variables:
        return ScopeStatus.OUTSIDE_BOUNDS, f"outside_max_variables:{len(variables)}>{config.max_variables}"

    return ScopeStatus.IN_TARGET_CLASS, ScopeStatus.IN_TARGET_CLASS.value


def _has_head_atom_in_body(rule: RelationalRule) -> bool:
    head = rule.head.without_occurrence()
    return any(atom.without_occurrence() == head for atom in rule.body)


def _match_rule(
    *,
    evaluator: SQLiteRuleEvaluator | None,
    rule: RelationalRule,
    coverage: str,
    target_index: TargetRuleIndex,
    projected_head_rows_cache: dict[str, set[tuple[object, ...]]],
) -> tuple[MatchStatus, str]:
    canonical_key = rule.canonical_key()
    alpha_match = target_index.rules_by_canonical_key.get(canonical_key)
    if alpha_match is not None:
        return MatchStatus.RECALLED_ALPHA, alpha_match.canonical_key()
    if coverage == "alpha":
        return MatchStatus.UNMATCHED, ""

    head_candidates = target_index.rules_by_head_key.get(rule.canonical_head_key(), [])
    for target_rule in head_candidates:
        if subsumes(target_rule, rule):
            return MatchStatus.RECALLED_SUBSUMED, target_rule.canonical_key()
    if coverage != "instance":
        return MatchStatus.UNMATCHED, ""

    if evaluator is None:
        raise ValueError("Instance coverage requires a live SQLite evaluator.")
    if covered_on_instance(
        evaluator,
        rule,
        head_candidates,
        projected_head_rows_cache=projected_head_rows_cache,
    ):
        return MatchStatus.COVERED_ON_INSTANCE, "finite_instance_coverage"
    return MatchStatus.UNMATCHED, ""


def _diagnosis_for_match(match_status: MatchStatus) -> str:
    if match_status == MatchStatus.RECALLED_ALPHA:
        return "covered_by_alpha_equivalence"
    if match_status == MatchStatus.RECALLED_SUBSUMED:
        return "covered_by_logical_subsumption"
    if match_status == MatchStatus.COVERED_ON_INSTANCE:
        return "covered_on_finite_instance"
    return "claim_relevant_uncovered"


def _record(
    *,
    parsed: ParsedRule,
    classification: AuditClassification,
    match_status: MatchStatus,
    scope_status: ScopeStatus,
    scope_reason: str,
    diagnosis: str,
    reason: str,
    support: int | None = None,
    predictions: int | None = None,
    confidence: float | None = None,
    matched_rule: str = "",
) -> AuditRecord:
    rule = parsed.rule
    coverage_alpha = match_status == MatchStatus.RECALLED_ALPHA
    coverage_subsumption = match_status in {MatchStatus.RECALLED_ALPHA, MatchStatus.RECALLED_SUBSUMED}
    coverage_instance = match_status in {
        MatchStatus.RECALLED_ALPHA,
        MatchStatus.RECALLED_SUBSUMED,
        MatchStatus.COVERED_ON_INSTANCE,
    }
    target_class_member = scope_status == ScopeStatus.IN_TARGET_CLASS
    claim_relevant = classification == AuditClassification.COMPARABLE_TRUE
    return AuditRecord(
        algorithm=parsed.source.algorithm,
        database=parsed.source.database,
        source_path=str(parsed.source.source_path),
        rule_index=parsed.source.index,
        classification=classification,
        match_status=match_status,
        scope_status=scope_status,
        scope_reason=scope_reason,
        target_class_member=target_class_member,
        coverage_alpha=coverage_alpha,
        coverage_subsumption=coverage_subsumption,
        coverage_instance=coverage_instance,
        diagnosis=diagnosis,
        claim_relevant=claim_relevant,
        reason=reason,
        support=support,
        predictions=predictions,
        confidence=confidence,
        canonical_rule=rule.canonical_key() if rule is not None else "",
        matched_rule=matched_rule,
        display=parsed.source.display,
    )


def _write_outputs(config: AuditConfig, records: list[AuditRecord]) -> None:
    _write_outputs_in_dir(
        config.output_dir,
        records,
        config,
        summary_path=config.output_dir / "audit_summary.json",
        unmatched_path=config.output_dir / "audit_unmatched.md",
        diagnosis_path=config.output_dir / "audit_diagnosis.md",
        claims_path=config.output_dir / "audit_claims.md",
    )


def _write_outputs_in_dir(
    output_dir: Path,
    records: list[AuditRecord],
    config: AuditConfig,
    *,
    summary_path: Path,
    unmatched_path: Path,
    diagnosis_path: Path,
    claims_path: Path,
) -> None:
    _write_rules_csv(output_dir / "audit_rules.csv", records)
    _write_funnel_csv(output_dir / "audit_funnel.csv", records)
    _write_summary_json(summary_path, records, config)
    _write_unmatched_markdown(unmatched_path, records, config.max_examples)
    _write_diagnosis_markdown(
        diagnosis_path,
        records,
        config.max_examples,
        diagnose_unmatched=config.diagnose_unmatched,
    )
    _write_claims_markdown(claims_path, records, config)


def _write_evaluation_cache(config: AuditConfig, shards: list[AuditShard], records: list[AuditRecord]) -> None:
    cache_dir = config.output_dir / CACHE_DIRNAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    records_by_shard: dict[tuple[str, str], list[AuditRecord]] = defaultdict(list)
    for record in records:
        records_by_shard[(record.algorithm, record.database)].append(record)
    for shard in shards:
        path = cache_dir / SHARDS_DIRNAME / shard.algorithm / shard.database / "audit_rules.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_rules_csv(path, records_by_shard.get((shard.algorithm, shard.database), []))
    manifest = _evaluation_manifest(config, shards)
    (cache_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _write_rules_csv(path: Path, records: list[AuditRecord]) -> None:
    fieldnames = list(AuditRecord.__dataclass_fields__.keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow({field: getattr(record, field) for field in fieldnames})


def _write_funnel_csv(path: Path, records: list[AuditRecord]) -> None:
    fieldnames = [
        "algorithm",
        "total",
        "parseable",
        "within_scope",
        "non_vacuous",
        "above_threshold",
        "exactly_recovered",
        "covered_by_more_general_rule",
        "unmatched_above_threshold",
        "parse_failed",
        "out_of_scope",
        "vacuous",
        "below_threshold",
    ]
    funnels = _funnel_by_algorithm(records)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for algorithm, funnel in funnels.items():
            writer.writerow({"algorithm": algorithm, **funnel})


def _write_summary_json(path: Path, records: list[AuditRecord], config: AuditConfig) -> None:
    by_algorithm: dict[str, Counter[str]] = defaultdict(Counter)
    by_database: dict[str, Counter[str]] = defaultdict(Counter)
    by_scope_status = Counter(record.scope_status.value for record in records)
    by_diagnosis = Counter(record.diagnosis for record in records)
    for record in records:
        by_algorithm[record.algorithm][record.classification.value] += 1
        by_algorithm[record.algorithm][record.match_status.value] += 1
        by_algorithm[record.algorithm][record.scope_status.value] += 1
        by_algorithm[record.algorithm][record.diagnosis] += 1
        by_database[record.database][record.classification.value] += 1
        by_database[record.database][record.diagnosis] += 1

    payload = {
        "coverage_mode": config.coverage,
        "totals": _totals(records),
        "funnel_by_algorithm": _funnel_by_algorithm(records),
        "by_scope_status": dict(sorted(by_scope_status.items())),
        "by_diagnosis": dict(sorted(by_diagnosis.items())),
        "by_algorithm": {algorithm: dict(counter) for algorithm, counter in sorted(by_algorithm.items())},
        "by_database": {database: dict(counter) for database, counter in sorted(by_database.items())},
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _totals(records: list[AuditRecord]) -> dict[str, int | float]:
    classification_counts = Counter(record.classification for record in records)
    comparable = classification_counts[AuditClassification.COMPARABLE_TRUE]
    alpha = sum(record.match_status == MatchStatus.RECALLED_ALPHA for record in records)
    alpha_or_subsumed = sum(
        record.match_status in {MatchStatus.RECALLED_ALPHA, MatchStatus.RECALLED_SUBSUMED} for record in records
    )
    alpha_subsumed_or_instance = sum(
        record.match_status
        in {MatchStatus.RECALLED_ALPHA, MatchStatus.RECALLED_SUBSUMED, MatchStatus.COVERED_ON_INSTANCE}
        for record in records
    )
    unmatched = sum(record.match_status == MatchStatus.UNMATCHED for record in records)
    alpha_uncovered = comparable - alpha
    subsumption_uncovered = comparable - alpha_or_subsumed
    instance_uncovered = comparable - alpha_subsumed_or_instance
    return {
        "audited_rules": len(records),
        "comparable_true": comparable,
        "alpha_claim_denominator": comparable,
        "subsumption_claim_denominator": comparable,
        "instance_claim_denominator": comparable,
        "recalled_alpha": alpha,
        "recalled_alpha_or_subsumed": alpha_or_subsumed,
        "recalled_alpha_subsumed_or_instance": alpha_subsumed_or_instance,
        "unmatched": unmatched,
        "claim_relevant_rules": comparable,
        "claim_relevant_uncovered": alpha_uncovered,
        "subsumption_uncovered": subsumption_uncovered,
        "instance_uncovered": instance_uncovered,
        "alpha_recall": alpha / comparable if comparable else 0.0,
        "alpha_or_subsumed_recall": alpha_or_subsumed / comparable if comparable else 0.0,
        "alpha_subsumed_or_instance_recall": alpha_subsumed_or_instance / comparable if comparable else 0.0,
    }


def _funnel_by_algorithm(records: list[AuditRecord]) -> dict[str, dict[str, int]]:
    grouped: dict[str, list[AuditRecord]] = defaultdict(list)
    for record in records:
        grouped[record.algorithm].append(record)

    result: dict[str, dict[str, int]] = {}
    for algorithm, rules in sorted(grouped.items()):
        parseable = [record for record in rules if record.canonical_rule != ""]
        within_scope = [
            record
            for record in parseable
            if record.scope_status == ScopeStatus.IN_TARGET_CLASS
            or record.classification == AuditClassification.VACUOUS
        ]
        non_vacuous = [record for record in within_scope if record.classification != AuditClassification.VACUOUS]
        above_threshold = [
            record for record in non_vacuous if record.classification == AuditClassification.COMPARABLE_TRUE
        ]
        exact = [record for record in above_threshold if record.match_status == MatchStatus.RECALLED_ALPHA]
        general = [record for record in above_threshold if record.match_status == MatchStatus.RECALLED_SUBSUMED]
        result[algorithm] = {
            "total": len(rules),
            "parseable": len(parseable),
            "within_scope": len(within_scope),
            "non_vacuous": len(non_vacuous),
            "above_threshold": len(above_threshold),
            "exactly_recovered": len(exact),
            "covered_by_more_general_rule": len(general),
            "unmatched_above_threshold": sum(
                record.match_status == MatchStatus.UNMATCHED for record in above_threshold
            ),
            "parse_failed": sum(record.classification == AuditClassification.PARSE_FAILED for record in rules),
            "out_of_scope": sum(record.classification == AuditClassification.OUT_OF_SCOPE for record in rules),
            "vacuous": sum(record.classification == AuditClassification.VACUOUS for record in rules),
            "below_threshold": sum(record.classification == AuditClassification.APPROXIMATE for record in rules),
        }
    return result


def _write_unmatched_markdown(path: Path, records: list[AuditRecord], max_examples: int) -> None:
    unmatched = [record for record in records if record.match_status == MatchStatus.UNMATCHED]
    lines = ["# Unmatched Comparable True Rules", ""]
    if not unmatched:
        lines.append("No unmatched comparable true rules found.")
    else:
        lines.append(f"Showing up to {max_examples} unmatched rules.")
        lines.append("")
        for record in unmatched[:max_examples]:
            lines.extend(
                [
                    f"## {record.algorithm} / {record.database} / rule {record.rule_index}",
                    "",
                    f"- Support: {record.support}",
                    f"- Predictions: {record.predictions}",
                    f"- Confidence: {record.confidence}",
                    f"- Canonical: `{record.canonical_rule}`",
                    "",
                    "```text",
                    record.display,
                    "```",
                    "",
                ]
            )
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_diagnosis_markdown(
    path: Path,
    records: list[AuditRecord],
    max_examples: int,
    *,
    diagnose_unmatched: bool,
) -> None:
    diagnosis_counts = Counter(record.diagnosis for record in records)
    scope_counts = Counter(record.scope_status.value for record in records)
    alpha_uncovered = [record for record in records if record.claim_relevant and not record.coverage_alpha]
    lines = [
        "# Audit Diagnosis",
        "",
        "This report explains why rules are or are not usable in the MAHILDA coverage claim.",
        "",
        "## Scope Status Counts",
        "",
    ]
    for status, count in scope_counts.most_common():
        lines.append(f"- {status}: {count}")
    lines.extend(["", "## Diagnosis Counts", ""])
    for diagnosis, count in diagnosis_counts.most_common():
        lines.append(f"- {diagnosis}: {count}")
    lines.extend(["", "## Claim-Relevant Uncovered Examples", ""])
    if not diagnose_unmatched:
        lines.append("Unmatched-rule diagnosis examples disabled with `--no-diagnose-unmatched`.")
    elif not alpha_uncovered:
        lines.append("No claim-relevant uncovered rules found under alpha-equivalence.")
    else:
        lines.append(f"Showing up to {max_examples} rules that block an alpha-equivalence 100% coverage claim.")
        lines.append("")
        for record in alpha_uncovered[:max_examples]:
            lines.extend(
                [
                    f"### {record.algorithm} / {record.database} / rule {record.rule_index}",
                    "",
                    f"- Diagnosis: {record.diagnosis}",
                    f"- Scope: {record.scope_status.value}",
                    f"- Support: {record.support}",
                    f"- Predictions: {record.predictions}",
                    f"- Confidence: {record.confidence}",
                    f"- Canonical: `{record.canonical_rule}`",
                    "",
                    "```text",
                    record.display,
                    "```",
                    "",
                ]
            )
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_claims_markdown(path: Path, records: list[AuditRecord], config: AuditConfig) -> None:
    totals = _totals(records)
    comparable = int(totals["comparable_true"])
    alpha = int(totals["recalled_alpha"])
    alpha_or_subsumed = int(totals["recalled_alpha_or_subsumed"])
    alpha_subsumed_or_instance = int(totals["recalled_alpha_subsumed_or_instance"])
    claim_relevant_uncovered = int(totals["claim_relevant_uncovered"])
    if config.coverage == "subsumption":
        selected_uncovered = int(totals["subsumption_uncovered"])
        selected_label = "alpha-equivalence or logical subsumption"
    elif config.coverage == "instance":
        selected_uncovered = int(totals["instance_uncovered"])
        selected_label = "alpha-equivalence, logical subsumption, finite-instance coverage"
    else:
        selected_uncovered = claim_relevant_uncovered
        selected_label = "alpha-equivalence"

    lines = [
        "# Audit Claims",
        "",
        "This audit supports only formal, closed-world, instance-level claims.",
        "",
        "## Computed Counts",
        "",
        f"- Audited competitor rules: {totals['audited_rules']}",
        f"- Comparable true rules: {comparable}",
        f"- Recalled by alpha-equivalence: {alpha}",
        f"- Recalled by alpha-equivalence or subsumption: {alpha_or_subsumed}",
        f"- Recalled by alpha-equivalence, subsumption, finite-instance coverage: {alpha_subsumed_or_instance}",
        f"- Unmatched comparable true rules under {selected_label}: {selected_uncovered}",
        "",
        "## Supported Claim Shape",
        "",
    ]
    funnel_lines = [
        "## Per-System Funnel",
        "",
        "| System | Total | Parseable | In scope | Non-vacuous | Above threshold | Exact | More general | Unmatched |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for algorithm, funnel in _funnel_by_algorithm(records).items():
        funnel_lines.append(
            "| {algorithm} | {total} | {parseable} | {within_scope} | {non_vacuous} | "
            "{above_threshold} | {exactly_recovered} | {covered_by_more_general_rule} | "
            "{unmatched_above_threshold} |".format(algorithm=algorithm, **funnel)
        )
    funnel_lines.extend(
        [
            "",
            "Excluded counts (parse_failed, out_of_scope, vacuous, below_threshold) are in "
            "audit_summary.json under funnel_by_algorithm.",
            "",
        ]
    )
    lines[lines.index("## Supported Claim Shape") - 1 : lines.index("## Supported Claim Shape") - 1] = funnel_lines

    if comparable == 0:
        lines.append("- No comparable true competitor rules were found, so no empirical coverage claim is supported.")
    elif selected_uncovered == 0:
        lines.append(
            f"- Under the configured audit scope, MAHILDA achieves 100% recall of comparable true rules by {selected_label}."
        )
    else:
        lines.append(f"- Under the configured audit scope, MAHILDA does not achieve 100% recall by {selected_label}.")
    lines.extend(
        [
            "",
            "## Not Supported",
            "",
            "- The audit does not prove that MAHILDA finds all true rules in the database.",
            "- The audit does not judge whether a true rule is useful or interesting.",
            "- The audit does not support calling competitor rules incorrect without formal category counts.",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _target_signature(config: AuditConfig, database: str) -> str:
    target_path = (
        config.results_dir / config.target / f"{config.target}_{database}" / f"{config.target}_{database}_results.json"
    )
    return _file_signature(target_path)


def _file_signature(path: Path) -> str:
    if not path.exists():
        return "missing"
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int_from_state(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    return 0


def _optional_int(value: str) -> int | None:
    return int(value) if value not in {"", "None"} else None


def _optional_float(value: str) -> float | None:
    return float(value) if value not in {"", "None"} else None
