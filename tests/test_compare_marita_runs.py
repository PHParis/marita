from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from scripts.compare_marita_runs import (
    DATABASES,
    SETTINGS,
    build_four_variant_comparison,
    compare_metric_values,
    compare_rule_artifacts,
    load_manifest,
    percentage_delta,
    prepare_archive,
)

if TYPE_CHECKING:
    from pathlib import Path


def _rules(path: Path, support: int = 1, count: int = 1) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "type": "MARITARule",
                    "body": ["a(x)"],
                    "head": ["b(x)"],
                    "display": "a(x) => b(x)",
                    "support": support,
                    "confidence": 0.5,
                }
            ]
            * count
        ),
        encoding="utf-8",
    )


def test_laptop_selection_and_settings_are_fixed() -> None:
    assert DATABASES == ("Mesh", "SAT", "Biodegradability")
    assert SETTINGS["walk_length"] == 3 and SETTINGS["memory_limit_bytes"] == 10 * 1024**3


def test_prepare_archive_never_overwrites(tmp_path: Path) -> None:
    archive = prepare_archive(tmp_path / "archive")
    (archive / "sentinel").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        prepare_archive(archive)
    assert (archive / "sentinel").read_text(encoding="utf-8") == "keep"


def test_percentage_calculations_include_regressions_and_missing_values() -> None:
    assert percentage_delta(10, 8) == 20
    assert percentage_delta(10, 12) == -20
    assert percentage_delta(0, 1) is None
    assert percentage_delta(None, 1) is None
    metrics = compare_metric_values(
        {"runtime_seconds": 10, "peak_rss_bytes": 100, "sql_query_count": 20},
        {"runtime_seconds": 8, "peak_rss_bytes": 120, "sql_query_count": 15},
    )
    assert metrics["runtime_seconds"]["improvement_percent"] == 20
    assert metrics["peak_rss_bytes"]["improvement_percent"] == -20
    assert metrics["sql_query_count"]["reduction_percent"] == 25


def test_rule_hashes_and_counts_are_compared(tmp_path: Path) -> None:
    main = tmp_path / "main.json"
    optimized = tmp_path / "optimized.json"
    _rules(main)
    _rules(optimized)
    equal = compare_rule_artifacts(main, optimized, "Mesh")
    assert equal["equivalent"] and equal["rule_count_match"]
    _rules(optimized, support=2, count=2)
    mismatch = compare_rule_artifacts(main, optimized, "Mesh")
    assert not mismatch["equivalent"] and not mismatch["rule_count_match"]


def test_missing_optimized_artifact_is_not_silent(tmp_path: Path) -> None:
    main = tmp_path / "main.json"
    _rules(main)
    with pytest.raises(FileNotFoundError):
        compare_rule_artifacts(main, tmp_path / "missing.json", "Mesh")


def _variant(runtime: float, *, match: bool = True) -> dict[str, dict[str, object]]:
    rule = {
        "rule_count": 1,
        "ordered_rule_hashes": ["same" if match else "different"],
        "display_hash": "d",
        "support_hash": "s",
        "confidence_hash": "c",
    }
    return {
        database: {
            "status": "success",
            "complete": True,
            "runtime_seconds": runtime,
            "rules": rule,
            "database_hashes": {"source": "x", "copy": "x", "final": "x"},
        }
        for database in DATABASES
    }


def test_manifest_requires_all_variants_and_commit_provenance(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "variants": {
                    name: {"root": str(tmp_path), "revision": name}
                    for name in ("main", "candidate_analysis", "sql_threshold", "combined")
                }
            }
        ),
        encoding="utf-8",
    )
    assert load_manifest(manifest)["variants"]["combined"]["revision"] == "combined"
    manifest.write_text(json.dumps({"variants": {"main": {"root": "x"}}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_manifest(manifest)


def test_four_variant_ranking_and_negative_regression() -> None:
    comparison = build_four_variant_comparison(
        {
            "main": _variant(10),
            "candidate_analysis": _variant(8),
            "sql_threshold": _variant(9),
            "combined": _variant(12),
        }
    )
    assert comparison["ranking_by_runtime"] == ["candidate_analysis", "sql_threshold", "combined"]
    assert comparison["aggregate"]["combined"]["improvement_percent"] == -20
    assert "regression" in comparison["conclusion"]


def test_four_variant_incomplete_and_output_mismatch_are_not_valid() -> None:
    combined = _variant(7, match=False)
    combined["SAT"]["complete"] = False
    comparison = build_four_variant_comparison(
        {"main": _variant(10), "candidate_analysis": _variant(8), "sql_threshold": _variant(9), "combined": combined}
    )
    assert comparison["conclusion"] == "incomplete evidence or output mismatch"
    assert comparison["databases"][1]["variants"]["combined"]["rule_count_match"]
    assert not comparison["databases"][1]["variants"]["combined"]["ordered_output_match"]
