from __future__ import annotations

from types import SimpleNamespace

import pytest

from mahilda.evaluation.baselines.matilda import Matilda
from mahilda.utils.rules import Predicate, TGDRule


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
