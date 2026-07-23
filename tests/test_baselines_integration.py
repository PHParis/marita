from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from marita.database.alchemy_utility import AlchemyUtility
from marita.evaluation.baselines import Amie3, Popper, Spider


def _should_run_integration() -> bool:
    return os.environ.get("MARITA_RUN_INTEGRATION") == "1"


def _ensure_test_db(db_path: Path) -> None:
    from marita.cli.test_data import create_test_database

    if not db_path.exists():
        create_test_database(str(db_path))


@pytest.mark.integration
@pytest.mark.java_required
def test_amie3_integration(tmp_path: Path) -> None:
    if not _should_run_integration():
        pytest.skip("Set MARITA_RUN_INTEGRATION=1 to run integration tests")
    if shutil.which("java") is None:
        pytest.skip("Java is required for AMIE3 integration")

    db_path = tmp_path / "test.db"
    _ensure_test_db(db_path)
    db_uri = f"sqlite:///{db_path}"

    with AlchemyUtility(
        db_uri, database_path=str(tmp_path), create_index=False, create_csv=True, create_tsv=True
    ) as db_util:
        result = Amie3(db_util).discover_rules(results_dir=str(tmp_path / "results"))

    assert isinstance(result, list)


@pytest.mark.integration
@pytest.mark.java_required
def test_spider_integration(tmp_path: Path) -> None:
    if not _should_run_integration():
        pytest.skip("Set MARITA_RUN_INTEGRATION=1 to run integration tests")
    if shutil.which("java") is None:
        pytest.skip("Java is required for SPIDER integration")

    db_path = tmp_path / "test.db"
    _ensure_test_db(db_path)
    db_uri = f"sqlite:///{db_path}"

    with AlchemyUtility(
        db_uri, database_path=str(tmp_path), create_index=False, create_csv=True, create_tsv=True
    ) as db_util:
        result = Spider(db_util).discover_rules(results_dir=str(tmp_path / "results"))

    assert isinstance(result, dict)


@pytest.mark.integration
@pytest.mark.prolog_required
def test_popper_integration(tmp_path: Path) -> None:
    if not _should_run_integration():
        pytest.skip("Set MARITA_RUN_INTEGRATION=1 to run integration tests")
    if shutil.which("run-popper") is None:
        pytest.skip("run-popper is required for Popper integration")

    db_path = tmp_path / "test.db"
    _ensure_test_db(db_path)
    db_uri = f"sqlite:///{db_path}"

    with AlchemyUtility(
        db_uri, database_path=str(tmp_path), create_index=False, create_csv=True, create_tsv=True
    ) as db_util:
        result = Popper(db_util).discover_rules(results_dir=str(tmp_path / "results"))

    assert isinstance(result, list)
