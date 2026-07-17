from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

QUEUE_STATES = ("pending", "running", "done", "failed")


@dataclass(frozen=True)
class QueueLease:
    job_id: str
    token: str
    host: str
    path: Path
    heartbeat_path: Path


class LeaseHeartbeat:
    def __init__(self, lease: QueueLease, interval_seconds: int) -> None:
        self.lease = lease
        self.interval_seconds = max(1, interval_seconds)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"audit-heartbeat-{lease.job_id}", daemon=True)

    def start(self) -> None:
        self._write()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1, self.interval_seconds))
        self.lease.heartbeat_path.unlink(missing_ok=True)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._write()

    def _write(self) -> None:
        write_json_atomic(
            self.lease.heartbeat_path,
            {
                "job_id": self.lease.job_id,
                "token": self.lease.token,
                "host": self.lease.host,
                "pid": os.getpid(),
                "updated_at": utc_now(),
            },
        )


def initialise_queue(
    queue_dir: Path,
    manifest: dict[str, Any],
    jobs: list[dict[str, Any]],
    *,
    retry_failed: bool = False,
    max_attempts: int = 3,
) -> None:
    queue_dir.mkdir(parents=True, exist_ok=True)
    for state in QUEUE_STATES:
        (queue_dir / state).mkdir(parents=True, exist_ok=True)

    manifest_path = queue_dir / "manifest.json"
    ready_path = queue_dir / "ready"
    if ready_path.exists():
        _validate_manifest(manifest_path, manifest)
        if not retry_failed:
            return

    lock = queue_dir / ".init.lock"
    if not try_acquire_lock(lock):
        for _ in range(120):
            if ready_path.exists():
                _validate_manifest(manifest_path, manifest)
                return
            time.sleep(0.5)
        raise SystemExit("Timed out waiting for another host to initialize the distributed audit queue.")

    try:
        if manifest_path.exists():
            _validate_manifest(manifest_path, manifest)
        else:
            write_json_atomic(manifest_path, manifest)

        for job in jobs:
            job_id = str(job["job_id"])
            if retry_failed:
                _retry_failed_job(queue_dir, job_id, max_attempts=max_attempts)
            if _find_job_path(queue_dir, job_id) is not None:
                continue
            payload = {**job, "status": "pending", "attempt": int(job.get("attempt", 1))}
            write_json_atomic(queue_dir / "pending" / job_filename(job_id), payload)
        write_json_atomic(ready_path, {"ready_at": utc_now()})
    finally:
        release_lock(lock)


def _validate_manifest(manifest_path: Path, manifest: dict[str, Any]) -> None:
    try:
        existing = read_json(manifest_path)
    except (FileNotFoundError, OSError, ValueError):
        raise SystemExit(
            "Distributed audit queue manifest is missing or invalid. Use --reset-state to restart."
        ) from None
    if existing.get("fingerprint") != manifest.get("fingerprint"):
        raise SystemExit("Distributed audit queue does not match current inputs. Use --reset-state to restart.")


def claim_job(queue_dir: Path, host: str) -> tuple[QueueLease, dict[str, Any]] | None:
    pending_dir = queue_dir / "pending"
    running_dir = queue_dir / "running"
    running_dir.mkdir(parents=True, exist_ok=True)
    for pending_path in sorted(pending_dir.glob("*.json")):
        token = uuid.uuid4().hex
        lease_path = running_dir / f"{pending_path.stem}__{_safe_name(host)}__{os.getpid()}__{token}.json"
        try:
            pending_path.rename(lease_path)
        except (FileNotFoundError, OSError):
            continue
        payload = read_json(lease_path)
        payload.update(
            {
                "status": "running",
                "claimed_by": host,
                "claimed_pid": os.getpid(),
                "claimed_at": utc_now(),
                "lease_token": token,
            }
        )
        write_json_atomic(lease_path, payload)
        lease = QueueLease(
            job_id=str(payload["job_id"]),
            token=token,
            host=host,
            path=lease_path,
            heartbeat_path=lease_path.with_suffix(".heartbeat.json"),
        )
        return lease, payload
    return None


def finish_job(lease: QueueLease, *, status: str, result: dict[str, Any] | None = None) -> bool:
    payload = read_json(lease.path) if lease.path.exists() else {}
    if payload.get("lease_token") != lease.token:
        return False
    if status not in {"done", "failed"}:
        raise ValueError(f"Unsupported queue terminal status: {status}")
    payload.update({"status": status, "finished_at": utc_now(), "result": result or {}})
    write_json_atomic(lease.path, payload)
    target = lease.path.parent.parent / status / job_filename(lease.job_id)
    lease.path.replace(target)
    lease.heartbeat_path.unlink(missing_ok=True)
    return True


def recover_stale_jobs(queue_dir: Path, stale_after_seconds: int, *, max_attempts: int = 3) -> list[str]:
    recovered: list[str] = []
    running_dir = queue_dir / "running"
    pending_dir = queue_dir / "pending"
    failed_dir = queue_dir / "failed"
    now = time.time()
    for running_path in sorted(running_dir.glob("*.json")):
        if running_path.name.endswith(".heartbeat.json"):
            continue
        recovery_lock = running_path.with_name(f".{running_path.name}.recover.lock")
        try:
            recovery_lock.mkdir()
        except FileExistsError:
            continue
        try:
            payload = read_json(running_path)
            marker = running_path.with_suffix(".heartbeat.json")
            marker_mtime = marker.stat().st_mtime if marker.exists() else running_path.stat().st_mtime
        except (FileNotFoundError, OSError, ValueError):
            recovery_lock.rmdir()
            continue
        try:
            if now - marker_mtime < stale_after_seconds:
                continue

            job_id = str(payload.get("job_id") or running_path.stem)
            attempt = int(payload.get("attempt", 1))
            payload.update(
                {
                    "status": "failed" if attempt >= max_attempts else "pending",
                    "attempt": attempt if attempt >= max_attempts else attempt + 1,
                    "error": "stale audit worker lease",
                    "recovered_at": utc_now(),
                }
            )
            target_dir = failed_dir if attempt >= max_attempts else pending_dir
            target = target_dir / job_filename(job_id)
            write_json_atomic(running_path, payload)
            running_path.replace(target)
            marker.unlink(missing_ok=True)
            recovered.append(job_id)
        finally:
            recovery_lock.rmdir()
    return recovered


def read_queue_jobs(queue_dir: Path, *, stale_after_seconds: int = 7200) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    if not queue_dir.exists():
        return jobs
    for state in QUEUE_STATES:
        for path in sorted((queue_dir / state).glob("*.json")):
            if path.name.endswith(".heartbeat.json"):
                continue
            try:
                payload = read_json(path)
            except (OSError, ValueError):
                continue
            if payload.get("status") == "running" and state == "running":
                payload["stale"] = _is_stale(path, stale_after_seconds=stale_after_seconds)
            payload["queue_state"] = state
            jobs.append(payload)
    return jobs


def queue_is_complete(queue_dir: Path) -> bool:
    jobs = read_queue_jobs(queue_dir)
    return all(job.get("queue_state") == "done" for job in jobs)


def queue_has_failures(queue_dir: Path) -> bool:
    return any(job.get("queue_state") == "failed" for job in read_queue_jobs(queue_dir))


def update_lease(lease: QueueLease, updates: dict[str, Any]) -> bool:
    if not lease.path.exists():
        return False
    payload = read_json(lease.path)
    if payload.get("lease_token") != lease.token:
        return False
    payload.update(updates)
    write_json_atomic(lease.path, payload)
    return True


def try_acquire_lock(path: Path) -> bool:
    try:
        path.mkdir(parents=True)
    except FileExistsError:
        return False
    write_json_atomic(
        path / "owner.json",
        {"host": local_host(), "pid": os.getpid(), "created_at": utc_now()},
    )
    return True


def release_lock(path: Path) -> None:
    (path / "owner.json").unlink(missing_ok=True)
    path.rmdir()


def job_filename(job_id: str) -> str:
    safe = _safe_name(job_id)
    digest = hashlib.sha1(job_id.encode("utf-8")).hexdigest()[:12]
    return f"{safe}__{digest}.json"


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _find_job_path(queue_dir: Path, job_id: str) -> Path | None:
    filename = job_filename(job_id)
    for state in QUEUE_STATES:
        direct = queue_dir / state / filename
        if direct.exists():
            return direct
        for path in (queue_dir / state).glob(f"{Path(filename).stem}__*.json"):
            if path.name.endswith(".heartbeat.json"):
                continue
            try:
                if read_json(path).get("job_id") == job_id:
                    return path
            except (OSError, ValueError):
                continue
    return None


def _retry_failed_job(queue_dir: Path, job_id: str, *, max_attempts: int) -> None:
    failed_path = queue_dir / "failed" / job_filename(job_id)
    if not failed_path.exists():
        return
    payload = read_json(failed_path)
    attempt = int(payload.get("attempt", 1)) + 1
    if attempt > max_attempts:
        return
    payload.update({"status": "pending", "attempt": attempt, "error": None, "requeued_at": utc_now()})
    target = queue_dir / "pending" / failed_path.name
    write_json_atomic(failed_path, payload)
    failed_path.replace(target)


def _is_stale(path: Path, *, stale_after_seconds: int) -> bool:
    marker = path.with_suffix(".heartbeat.json")
    try:
        timestamp = marker.stat().st_mtime if marker.exists() else path.stat().st_mtime
    except OSError:
        return False
    return time.time() - timestamp >= stale_after_seconds


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value) or "job"


def local_host() -> str:
    return socket.gethostname().split(".")[0]
