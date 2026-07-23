import logging
import signal
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from marita.cli import benchmark as benchmark_cli
from marita.cli import processors as processors_cli
from marita.cli import run as run_cli
from marita.cli.processors import BaselineProcessor, DatabaseProcessor
from marita.utils.rules import InclusionDependency


class _FakeAlchemyUtility:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb


def test_run_processor_wires_should_stop_predicate(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeMarita:
        def __init__(self, db_util, config=None) -> None:
            del db_util, config

        def discover_rules(self, *, results_dir: str, should_stop):
            captured["results_dir"] = results_dir
            captured["should_stop_value"] = should_stop()
            return iter([])

    monkeypatch.setattr(processors_cli, "MARITA", FakeMarita)
    monkeypatch.setattr(processors_cli, "AlchemyUtility", _FakeAlchemyUtility)
    monkeypatch.setattr(processors_cli.RuleIO, "save_rules_to_json", lambda rules, path: 0)
    monkeypatch.setattr(DatabaseProcessor, "generate_report", lambda *args, **kwargs: None)

    processor = DatabaseProcessor(
        algorithm_name="MARITA",
        database_name=Path("demo.db"),
        database_path=tmp_path,
        results_dir=tmp_path / "results",
        logger=logging.getLogger("test_run_processor_wiring"),
        should_stop=lambda: True,
    )

    with pytest.raises(RuntimeError, match="resource limits"):
        processor.discover_rules()

    assert captured["should_stop_value"] is True
    assert str(tmp_path / "results" / "MARITA_demo") in str(captured["results_dir"])


def test_run_processor_uses_expected_output_paths(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    class FakeMarita:
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

    monkeypatch.setattr(processors_cli, "MARITA", FakeMarita)
    monkeypatch.setattr(processors_cli, "AlchemyUtility", _FakeAlchemyUtility)
    monkeypatch.setattr(processors_cli.RuleIO, "save_rules_to_json", fake_save_rules)
    monkeypatch.setattr(DatabaseProcessor, "generate_report", lambda *args, **kwargs: None)

    processor = DatabaseProcessor(
        algorithm_name="MARITA",
        database_name=Path("demo.db"),
        database_path=tmp_path,
        results_dir=tmp_path / "results",
        logger=logging.getLogger("test_run_processor_output"),
    )

    count = processor.discover_rules()

    assert count == 1
    assert captured["results_dir"].endswith("MARITA_demo")
    assert captured["json_path"].endswith("MARITA_demo_results.json")


def test_benchmark_processor_uses_expected_output_paths(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    class FakeSpider:
        def __init__(self, db_util) -> None:
            del db_util

        def discover_rules(self, *, results_dir: str, **kwargs):
            del kwargs
            captured["results_dir"] = results_dir
            rule = SimpleNamespace(display="A(x)->B(x)", accuracy=0.7, confidence=0.6)
            return [rule]

    def fake_save_rules(rules, path: str) -> int:
        captured["json_path"] = path
        return len(rules)

    monkeypatch.setattr(processors_cli, "Spider", FakeSpider)
    monkeypatch.setattr(processors_cli, "AlchemyUtility", _FakeAlchemyUtility)
    monkeypatch.setattr(processors_cli.RuleIO, "save_rules_to_json", fake_save_rules)
    monkeypatch.setattr(BaselineProcessor, "generate_report", lambda *args, **kwargs: None)

    processor = BaselineProcessor(
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


def test_benchmark_processor_reports_unscored_spider_rules(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeSpider:
        def __init__(self, db_util) -> None:
            del db_util

        def discover_rules(self, **kwargs):
            del kwargs
            return [
                InclusionDependency(
                    table_dependant="child",
                    columns_dependant=("id",),
                    table_referenced="parent",
                    columns_referenced=("id",),
                )
            ]

    def fake_save_rules(rules, path: str) -> int:
        captured["rules"] = list(rules)
        captured["json_path"] = path
        return len(rules)

    def fake_generate_report(self, number_of_rules, result_path, top_rules, execution_time=None) -> None:
        del self, execution_time
        captured["report_number_of_rules"] = number_of_rules
        captured["report_result_path"] = result_path
        captured["report_top_rules"] = list(top_rules)

    monkeypatch.setattr(processors_cli, "Spider", FakeSpider)
    monkeypatch.setattr(processors_cli, "AlchemyUtility", _FakeAlchemyUtility)
    monkeypatch.setattr(processors_cli.RuleIO, "save_rules_to_json", fake_save_rules)
    monkeypatch.setattr(BaselineProcessor, "generate_report", fake_generate_report)

    processor = BaselineProcessor(
        baseline_name="SPIDER",
        database_name=Path("demo.db"),
        database_path=tmp_path,
        results_dir=tmp_path / "results",
        logger=logging.getLogger("test_benchmark_processor_unscored_spider"),
    )

    count = processor.discover_rules()

    assert count == 1
    assert captured["json_path"] == str(tmp_path / "results" / "SPIDER_demo" / "SPIDER_demo_results.json")
    assert captured["report_number_of_rules"] == 1
    assert captured["report_result_path"] == tmp_path / "results" / "SPIDER_demo" / "SPIDER_demo_results.json"
    assert captured["report_top_rules"] == captured["rules"]


def test_benchmark_processor_passes_timeout_to_amie3(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeAmie3:
        def __init__(self, db_util) -> None:
            del db_util

        def discover_rules(self, *, results_dir: str, timeout: int, **kwargs):
            del kwargs
            captured["results_dir"] = results_dir
            captured["timeout"] = timeout
            return []

    monkeypatch.setattr(processors_cli, "Amie3", FakeAmie3)
    monkeypatch.setattr(processors_cli, "AlchemyUtility", _FakeAlchemyUtility)
    monkeypatch.setattr(processors_cli.RuleIO, "save_rules_to_json", lambda rules, path: len(rules))
    monkeypatch.setattr(BaselineProcessor, "generate_report", lambda *args, **kwargs: None)

    processor = BaselineProcessor(
        baseline_name="AMIE3",
        database_name=Path("demo.db"),
        database_path=tmp_path,
        results_dir=tmp_path / "results",
        logger=logging.getLogger("test_benchmark_timeout"),
        timeout=1800,
    )

    count = processor.discover_rules()

    assert count == 0
    assert captured["timeout"] == 1800
    assert str(captured["results_dir"]).endswith("AMIE3_demo")


def test_benchmark_processor_passes_popper_command(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakePopper:
        def __init__(self, db_util) -> None:
            del db_util

        def discover_rules(self, **kwargs):
            captured.update(kwargs)
            return []

    monkeypatch.setattr(processors_cli, "Popper", FakePopper)
    monkeypatch.setattr(processors_cli, "AlchemyUtility", _FakeAlchemyUtility)
    monkeypatch.setattr(processors_cli.RuleIO, "save_rules_to_json", lambda rules, path: len(rules))
    monkeypatch.setattr(BaselineProcessor, "generate_report", lambda *args, **kwargs: None)

    processor = BaselineProcessor(
        baseline_name="POPPER",
        database_name=Path("demo.db"),
        database_path=tmp_path,
        results_dir=tmp_path / "results",
        logger=logging.getLogger("test_benchmark_popper_command"),
        popper_command="custom-popper",
    )

    assert processor.discover_rules() == 0
    assert captured["popper_command"] == "custom-popper"
    assert str(captured["runtime_dir"]).endswith("POPPER_demo/_runtime")


@pytest.mark.skipif(not hasattr(signal, "SIGALRM"), reason="SIGALRM is required for hard benchmark timeout")
def test_benchmark_processor_enforces_hard_timeout(monkeypatch, tmp_path: Path) -> None:
    class SlowMatilda:
        def __init__(self, db_util) -> None:
            del db_util

        def discover_rules(self, **kwargs):
            del kwargs
            time.sleep(5)
            return []

    monkeypatch.setattr(processors_cli, "Matilda", SlowMatilda)
    monkeypatch.setattr(processors_cli, "AlchemyUtility", _FakeAlchemyUtility)

    processor = BaselineProcessor(
        baseline_name="MATILDA",
        database_name=Path("demo.db"),
        database_path=tmp_path,
        results_dir=tmp_path / "results",
        logger=logging.getLogger("test_benchmark_hard_timeout"),
        timeout=1,
    )

    started = time.monotonic()
    with pytest.raises(processors_cli.BenchmarkTimeoutError):
        processor.discover_rules()

    assert time.monotonic() - started < 3
    assert (tmp_path / "results" / "MATILDA_demo" / "execution_time_demo.json").exists()


def test_run_report_path_generation(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    processor = DatabaseProcessor(
        algorithm_name="MARITA",
        database_name=Path("demo.db"),
        database_path=tmp_path,
        results_dir=results_dir,
        logger=logging.getLogger("test_run_report_path"),
    )

    processor.generate_report(number_of_rules=0, result_path=results_dir / "foo.json", top_rules=[])

    assert (results_dir / "report_MARITA_demo.md").exists()


def test_benchmark_report_path_generation(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    processor = BaselineProcessor(
        baseline_name="SPIDER",
        database_name=Path("demo.db"),
        database_path=tmp_path,
        results_dir=results_dir,
        logger=logging.getLogger("test_benchmark_report_path"),
    )

    processor.generate_report(number_of_rules=0, result_path=results_dir / "foo.json", top_rules=[])

    assert (results_dir / "report_SPIDER_demo.md").exists()


def test_run_processor_cleanup_only_removes_marita_temp_dir(tmp_path: Path) -> None:
    prolog_dir = tmp_path / "prolog_tmp"
    spider_dir = tmp_path / "SPIDER_temp"
    popper_dir = tmp_path / "popper"
    prolog_dir.mkdir()
    spider_dir.mkdir()
    popper_dir.mkdir()

    processor = DatabaseProcessor(
        algorithm_name="MARITA",
        database_name=Path("demo.db"),
        database_path=tmp_path,
        results_dir=tmp_path / "results",
        logger=logging.getLogger("test_run_cleanup"),
    )

    processor.clean_up()

    assert not prolog_dir.exists()
    assert spider_dir.exists()
    assert popper_dir.exists()


# Verify the public API is still reachable via the CLI modules for backward compatibility
def test_database_processor_importable_via_run_cli() -> None:
    assert run_cli.DatabaseProcessor is DatabaseProcessor


def test_baseline_processor_importable_via_benchmark_cli() -> None:
    assert benchmark_cli.BaselineProcessor is BaselineProcessor
