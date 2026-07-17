from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING

from mahilda.audit.distributed import (
    claim_job,
    finish_job,
    initialise_queue,
    read_queue_jobs,
    recover_stale_jobs,
)

if TYPE_CHECKING:
    from pathlib import Path

def _queue(tmp_path: Path) -> Path:
    queue_dir = tmp_path / "queue"
    initialise_queue(
        queue_dir,
        {"fingerprint": "test"},
        [{"job_id": "tiny", "database": "tiny", "total_rules": 2}],
    )
    return queue_dir


def test_claim_job_is_atomic_between_hosts(tmp_path: Path) -> None:
    queue_dir = _queue(tmp_path)
    claims: list[object] = []

    def claim(host: str) -> None:
        claims.append(claim_job(queue_dir, host))

    threads = [threading.Thread(target=claim, args=(host,)) for host in ("h0", "h1")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(item is not None for item in claims) == 1
    assert len(list((queue_dir / "running").glob("*.json"))) == 1


def test_finish_job_moves_only_matching_lease(tmp_path: Path) -> None:
    queue_dir = _queue(tmp_path)
    claimed = claim_job(queue_dir, "h0")
    assert claimed is not None
    lease, _ = claimed

    assert finish_job(lease, status="done", result={"ok": True}) is True
    assert not list((queue_dir / "running").glob("*.json"))
    done = list((queue_dir / "done").glob("*.json"))
    assert len(done) == 1
    assert json.loads(done[0].read_text(encoding="utf-8"))["status"] == "done"


def test_recover_stale_job_requeues_with_incremented_attempt(tmp_path: Path) -> None:
    queue_dir = _queue(tmp_path)
    claimed = claim_job(queue_dir, "h0")
    assert claimed is not None
    lease, _ = claimed
    lease.heartbeat_path.unlink(missing_ok=True)
    lease.path.touch()

    recovered = recover_stale_jobs(queue_dir, stale_after_seconds=0)

    assert recovered == ["tiny"]
    pending = list((queue_dir / "pending").glob("*.json"))
    assert len(pending) == 1
    assert json.loads(pending[0].read_text(encoding="utf-8"))["attempt"] == 2


def test_status_reads_all_queue_states(tmp_path: Path) -> None:
    queue_dir = _queue(tmp_path)
    claimed = claim_job(queue_dir, "h0")
    assert claimed is not None
    lease, payload = claimed
    payload["processed_rules"] = 1
    lease.path.write_text(json.dumps(payload), encoding="utf-8")

    jobs = read_queue_jobs(queue_dir)

    assert len(jobs) == 1
    assert jobs[0]["queue_state"] == "running"
    assert jobs[0]["claimed_by"] == "h0"
