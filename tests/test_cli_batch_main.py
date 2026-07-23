from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from marita.cli import batch

if TYPE_CHECKING:
    from pathlib import Path


def _cfg(tmp_path: Path):
    return SimpleNamespace(
        database=SimpleNamespace(path=tmp_path),
        batch=SimpleNamespace(timeout=9, workers=1),
        logging=SimpleNamespace(log_dir=tmp_path / "logs"),
    )


def test_batch_main_config_error(monkeypatch) -> None:
    monkeypatch.setattr(batch, "load_typed_config", lambda _p: (_ for _ in ()).throw(ValueError("bad config")))
    assert batch.main(["--config", "cfg.yml"]) == 1


def test_batch_main_missing_directory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(batch, "load_typed_config", lambda _p: _cfg(tmp_path))
    assert batch.main(["--config", "cfg.yml", "--directory", str(tmp_path / "missing")]) == 1


def test_batch_main_no_db_files(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(batch, "load_typed_config", lambda _p: _cfg(tmp_path))
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert batch.main(["--config", "cfg.yml", "--directory", str(empty_dir)]) == 1


def test_batch_main_start_from_out_of_range(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(batch, "load_typed_config", lambda _p: _cfg(tmp_path))
    db_dir = tmp_path / "dbs"
    db_dir.mkdir()
    (db_dir / "a.db").write_text("", encoding="utf-8")
    assert batch.main(["--config", "cfg.yml", "--directory", str(db_dir), "--start-from", "2"]) == 1


def test_batch_main_happy_path_with_fake_executor(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(batch, "load_typed_config", lambda _p: _cfg(tmp_path))
    monkeypatch.setattr(batch, "initialize_directories", lambda *args, **kwargs: None)

    db_dir = tmp_path / "dbs"
    db_dir.mkdir()
    (db_dir / "a.db").write_text("", encoding="utf-8")
    (db_dir / "b.db").write_text("", encoding="utf-8")

    class FakeFuture:
        def __init__(self, value):
            self._value = value

        def result(self):
            return self._value

    class FakeExecutor:
        def __init__(self, max_workers=None):
            del max_workers
            self.futures = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            del exc_type, exc, tb

        def submit(self, fn, db_path, db_name, results_base_dir, timeout, log_root):
            value = fn(db_path, db_name, results_base_dir, timeout, log_root)
            fut = FakeFuture(value)
            self.futures.append(fut)
            return fut

    monkeypatch.setattr(batch, "ProcessPoolExecutor", FakeExecutor)
    monkeypatch.setattr(batch, "as_completed", lambda futures: list(futures))
    monkeypatch.setattr(
        batch,
        "run_database",
        lambda db_path, db_name, *_args, **_kwargs: {
            "database": db_name,
            "status": "success",
            "duration": 1.0,
            "rules_count": 2,
            "error": None,
            "start_time": None,
            "end_time": None,
        },
    )

    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    rc = batch.main(["--config", "cfg.yml", "--directory", str(db_dir), "--output", str(out_dir)])
    assert rc == 0
    assert (out_dir / "summary.txt").exists()
