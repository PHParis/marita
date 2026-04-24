import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from mahilda.cli import benchmark as benchmark_cli
from mahilda.cli import run as run_cli


class _FakeAlchemyUtility:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb


def test_run_processor_wires_should_stop_predicate(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeMahilda:
        def __init__(self, db_util, config=None) -> None:
            del db_util, config

        def discover_rules(self, *, results_dir: str, should_stop):
            captured["results_dir"] = results_dir
            captured["should_stop_value"] = should_stop()
            return iter([])

    monkeypatch.setattr(run_cli, "MAHILDA", FakeMahilda)
    monkeypatch.setattr(run_cli, "AlchemyUtility", _FakeAlchemyUtility)
    monkeypatch.setattr(run_cli.RuleIO, "save_rules_to_json", lambda rules, path: 0)
    monkeypatch.setattr(run_cli.DatabaseProcessor, "generate_report", lambda *args, **kwargs: None)

    processor = run_cli.DatabaseProcessor(
        algorithm_name="MAHILDA",
        database_name=Path("demo.db"),
        database_path=tmp_path,
        results_dir=tmp_path / "results",
        logger=logging.getLogger("test_run_processor_wiring"),
        should_stop=lambda: True,
    )

    with pytest.raises(RuntimeError, match="resource limits"):
        processor.discover_rules()

    assert captured["should_stop_value"] is True
    assert str(tmp_path / "results" / "MAHILDA_demo") in str(captured["results_dir"])


def test_run_processor_uses_expected_output_paths(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    class FakeMahilda:
        def __init__(self, db_util, config=None) -> None:
            del db_util, config

        def discover_rules(self, *, results_dir: str, should_stop):
            del should_stop
            captured["results_dir"] = results_dir
            rule = SimpleNamespace(display="P(x)->Q(x)", accuracy=0.9, confidence=0.8)
            return iter([rule])

    def fake_save_rules(rules, path: str) -> int:
        captured["json_path"] = path
        return len(rules)

    monkeypatch.setattr(run_cli, "MAHILDA", FakeMahilda)
    monkeypatch.setattr(run_cli, "AlchemyUtility", _FakeAlchemyUtility)
    monkeypatch.setattr(run_cli.RuleIO, "save_rules_to_json", fake_save_rules)
    monkeypatch.setattr(run_cli.DatabaseProcessor, "generate_report", lambda *args, **kwargs: None)

    processor = run_cli.DatabaseProcessor(
        algorithm_name="MAHILDA",
        database_name=Path("demo.db"),
        database_path=tmp_path,
        results_dir=tmp_path / "results",
        logger=logging.getLogger("test_run_processor_output"),
    )

    count = processor.discover_rules()

    assert count == 1
    assert captured["results_dir"].endswith("MAHILDA_demo")
    assert captured["json_path"].endswith("MAHILDA_demo_results.json")


def test_benchmark_processor_uses_expected_output_paths(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    class FakeSpider:
        def __init__(self, db_util) -> None:
            del db_util

        def discover_rules(self, *, results_dir: str):
            captured["results_dir"] = results_dir
            rule = SimpleNamespace(display="A(x)->B(x)", accuracy=0.7, confidence=0.6)
            return [rule]

    def fake_save_rules(rules, path: str) -> int:
        captured["json_path"] = path
        return len(rules)

    monkeypatch.setattr(benchmark_cli, "Spider", FakeSpider)
    monkeypatch.setattr(benchmark_cli, "AlchemyUtility", _FakeAlchemyUtility)
    monkeypatch.setattr(benchmark_cli.RuleIO, "save_rules_to_json", fake_save_rules)
    monkeypatch.setattr(benchmark_cli.BaselineProcessor, "generate_report", lambda *args, **kwargs: None)

    processor = benchmark_cli.BaselineProcessor(
        baseline_name="SPIDER",
        database_name=Path("demo.db"),
        database_path=tmp_path,
        results_dir=tmp_path / "results",
        logger=logging.getLogger("test_benchmark_processor_output"),
    )

    count = processor.discover_rules()

    assert count == 1
    assert captured["results_dir"].endswith("SPIDER_demo")
    assert captured["json_path"].endswith("SPIDER_demo_results.json")


def test_run_report_path_generation(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    processor = run_cli.DatabaseProcessor(
        algorithm_name="MAHILDA",
        database_name=Path("demo.db"),
        database_path=tmp_path,
        results_dir=results_dir,
        logger=logging.getLogger("test_run_report_path"),
    )

    processor.generate_report(number_of_rules=0, result_path=results_dir / "foo.json", top_rules=[])

    assert (results_dir / "report_MAHILDA_demo.md").exists()


def test_benchmark_report_path_generation(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    processor = benchmark_cli.BaselineProcessor(
        baseline_name="SPIDER",
        database_name=Path("demo.db"),
        database_path=tmp_path,
        results_dir=results_dir,
        logger=logging.getLogger("test_benchmark_report_path"),
    )

    processor.generate_report(number_of_rules=0, result_path=results_dir / "foo.json", top_rules=[])

    assert (results_dir / "report_SPIDER_demo.md").exists()
