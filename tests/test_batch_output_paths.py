import json
from pathlib import Path

from mahilda.cli import batch


def test_run_database_writes_execution_time_file(monkeypatch, tmp_path: Path) -> None:
    class FakeProcessor:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def discover_rules(self) -> int:
            return 3

    monkeypatch.setattr(batch, "DatabaseProcessor", FakeProcessor)

    db_path = tmp_path / "demo.db"
    db_path.write_text("", encoding="utf-8")
    results_base = tmp_path / "results"

    result = batch.run_database(db_path, "demo", results_base, timeout=10)

    metrics_path = results_base / "MAHILDA_demo" / "execution_time_demo.json"
    assert result["status"] == "success"
    assert result["rules_count"] == 3
    assert metrics_path.exists()

    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert payload["database"] == "demo"
    assert payload["rules_count"] == 3
