from __future__ import annotations

from types import SimpleNamespace

import pytest

from marita.evaluation.baselines.matilda import Matilda
from marita.utils.rules import Predicate, TGDRule


def test_resolve_matilda_path_accepts_repo_with_src(tmp_path):
    repo = tmp_path / "MATILDA"
    (repo / "src").mkdir(parents=True)

    assert Matilda._resolve_matilda_path(repo) == repo.resolve()


def test_resolve_matilda_path_rejects_missing_src(tmp_path):
    with pytest.raises(RuntimeError, match="MATILDA sibling repository not found"):
        Matilda._resolve_matilda_path(tmp_path / "missing")


def test_convert_rule_translates_external_tgd_shape():
    raw = SimpleNamespace(
        body=[SimpleNamespace(variable1="x", relation="table__col", variable2="y")],
        head=[SimpleNamespace(variable1="y", relation="other__col", variable2="z")],
        display="table(x, y) -> other(y, z)",
        accuracy=2,
        confidence="0.5",
        correct=True,
        compatible=None,
    )

    converted = Matilda._convert_rule(raw)

    assert converted == TGDRule(
        body=(Predicate("x", "table__col", "y"),),
        head=(Predicate("y", "other__col", "z"),),
        display="table(x, y) -> other(y, z)",
        accuracy=2.0,
        confidence=0.5,
        correct=True,
        compatible=None,
    )


def test_discover_rules_forwards_timeout_to_external_matilda(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class FakeExternalMatilda:
        def __init__(self, database, settings=None):
            captured["database"] = database
            captured["settings"] = settings

        def discover_rules(self, **kwargs):
            captured.update(kwargs)
            return []

    monkeypatch.setattr(Matilda, "_resolve_matilda_path", staticmethod(lambda configured_path: tmp_path))
    monkeypatch.setattr(Matilda, "_load_external_matilda", staticmethod(lambda matilda_path: FakeExternalMatilda))

    Matilda(database=object()).discover_rules(results_dir=str(tmp_path / "results"), timeout=7)

    assert captured["results_dir"] == str(tmp_path / "results")
    assert captured["timeout"] == 7
