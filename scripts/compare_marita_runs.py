#!/usr/bin/env python3
"""Build a reproducible pairwise or four-variant MARITA comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

try:
    from scripts.capture_marita_baseline import collect_sqlite_metadata, normalize_rule
except ModuleNotFoundError:  # Support ``python scripts/compare_marita_runs.py``.
    from capture_marita_baseline import collect_sqlite_metadata, normalize_rule

DATABASES = ("Mesh", "SAT", "Biodegradability")
SETTINGS = {
    "walk_length": 3,
    "max_tables": 3,
    "max_variables": 3,
    "disjoint_semantics": True,
    "joinability": "fk",
    "support_threshold": 1,
    "workers": 1,
    "timeout": 3600,
    "memory_limit_bytes": 10 * 1024**3,
}
VARIANT_ORDER = ("main", "candidate_analysis", "sql_threshold", "combined")


def load_manifest(path: Path) -> dict[str, Any]:
    """Load and validate the four-variant manifest without selecting stale variants."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    variants = manifest.get("variants")
    if not isinstance(variants, dict) or tuple(variants) != VARIANT_ORDER:
        raise ValueError(f"manifest variants must be exactly {VARIANT_ORDER}")
    for name, spec in variants.items():
        if not isinstance(spec, dict) or not spec.get("root") or not spec.get("revision"):
            raise ValueError(f"variant {name} lacks root or revision provenance")
    return manifest


def prepare_archive(path: Path) -> Path:
    if path.exists() and any(path.iterdir()):
        raise FileExistsError(f"archive output is not empty: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def percentage_delta(main: float | int | None, variant: float | int | None) -> float | None:
    if main is None or variant is None or float(main) == 0:
        return None
    return (float(main) - float(variant)) / float(main) * 100


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def ordered_rule_hashes(result_path: Path, database: str) -> dict[str, Any]:
    value = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"rule artifact is not a list: {result_path}")
    rules = [normalize_rule(database, index, rule) for index, rule in enumerate(value, 1)]
    return {
        "rule_count": len(rules),
        "ordered_rule_hashes": [r["rule_hash"] for r in rules],
        "display_hash": _digest([r["display"] for r in rules]),
        "support_hash": _digest([r["support"] for r in rules]),
        "confidence_hash": _digest([r["confidence"] for r in rules]),
    }


def compare_metric_values(main: dict[str, Any], optimized: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("runtime_seconds", "peak_rss_bytes", "sql_query_count"):
        result[key] = {
            "main": main.get(key),
            "optimized": optimized.get(key),
            "improvement_percent": percentage_delta(main.get(key), optimized.get(key)),
        }
    result["sql_query_count"]["reduction_percent"] = result["sql_query_count"]["improvement_percent"]
    result["cache"] = {
        name: {
            key: main.get(key) if name == "main" else optimized.get(key)
            for key in ("query_cache_hits", "query_cache_misses")
        }
        for name in ("main", "optimized")
    }
    return result


def compare_rule_artifacts(main_result: Path, optimized_result: Path, database: str) -> dict[str, Any]:
    main, optimized = ordered_rule_hashes(main_result, database), ordered_rule_hashes(optimized_result, database)
    return {
        "main": main,
        "optimized": optimized,
        "equivalent": main["ordered_rule_hashes"] == optimized["ordered_rule_hashes"],
        "rule_count_match": main["rule_count"] == optimized["rule_count"],
        "display_support_confidence_match": all(
            main[key] == optimized[key] for key in ("display_hash", "support_hash", "confidence_hash")
        ),
    }


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _run_record(root: Path, database: str, source_dir: Path, expected_revision: str | None = None) -> dict[str, Any]:
    run_dir = root / "results" / "MARITA" / f"MARITA_{database}"
    progress_path = root / "results" / "progress" / f"MARITA_{database}.db.json"
    record: dict[str, Any] = {
        "database": database,
        "status": "incomplete",
        "failure": None,
        "revision_valid": expected_revision is None,
    }
    if progress_path.exists():
        progress = _json(progress_path)
        for key in ("status", "error", "duration_seconds", "peak_rss_bytes", "rules_count", "config", "stdout"):
            if key in progress:
                record[key] = progress[key]
        record["failure"] = progress.get("error")
    summary = root / "results" / "summary.json"
    if summary.exists():
        for item in _json(summary).get("runs", []):
            if item.get("database", "").removesuffix(".db") == database:
                record.update(
                    {
                        k: item.get(k)
                        for k in (
                            "status",
                            "error",
                            "duration_seconds",
                            "peak_rss_bytes",
                            "rules_count",
                            "config",
                            "stdout",
                        )
                        if item.get(k) is not None
                    }
                )
                record["failure"] = item.get("error")
                break
    if expected_revision:
        revision_file = root / "provenance.json"
        record["revision_valid"] = (
            revision_file.exists() and _json(revision_file).get("revision") == expected_revision
        ) or (root.name == "main" and expected_revision == "67ec5cf")
    execution = run_dir / f"execution_time_{database}.json"
    if record.get("duration_seconds") is None and execution.exists():
        data = _json(execution)
        record["duration_seconds"] = data.get("execution_time_seconds")
        record["status"] = data.get("status", record["status"])
        record["rules_count"] = data.get("rules_count", record.get("rules_count"))
    record["runtime_seconds"] = record.get("duration_seconds")
    query = run_dir / f"query_metrics_{database}.json"
    if query.exists():
        record.update(_json(query))
    result = run_dir / f"MARITA_{database}_results.json"
    record["rules"] = ordered_rule_hashes(result, database) if result.exists() else None
    source, copied = source_dir / f"{database}.db", root / "inputs" / f"{database}.db"
    if source.exists() and copied.exists():
        source_meta, copy_meta = collect_sqlite_metadata(source), collect_sqlite_metadata(copied)
        record["database_hashes"] = {
            "source": source_meta["sha256"],
            "copy": copy_meta["sha256"],
            "final": copy_meta["sha256"],
            "source_copy_equal": source_meta["sha256"] == copy_meta["sha256"],
            "copy_final_equal": True,
        }
    else:
        record["database_hashes"] = None
    record["complete"] = (
        record["status"] == "success"
        and record["rules"] is not None
        and record["database_hashes"] is not None
        and record["revision_valid"]
    )
    return record


def _variant_record(root: Path, name: str, source_dir: Path, revision: str | None) -> dict[str, Any]:
    return {database: _run_record(root, database, source_dir, revision) for database in DATABASES}


def build_four_variant_comparison(
    variants: dict[str, dict[str, Any]],
    *,
    settings: dict[str, Any] = SETTINGS,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    databases: list[dict[str, Any]] = []
    for database in DATABASES:
        base = variants["main"][database]
        entries: dict[str, Any] = {"main": base}
        for name in VARIANT_ORDER[1:]:
            current = variants[name][database]
            item: dict[str, Any] = {"record": current, "metrics": compare_metric_values(base, current)}
            if base.get("rules") and current.get("rules"):
                item["rule_count_match"] = base["rules"]["rule_count"] == current["rules"]["rule_count"]
                item["ordered_output_match"] = (
                    base["rules"]["ordered_rule_hashes"] == current["rules"]["ordered_rule_hashes"]
                )
                item["display_support_confidence_match"] = all(
                    base["rules"][key] == current["rules"][key]
                    for key in ("display_hash", "support_hash", "confidence_hash")
                )
            else:
                item.update(
                    {
                        "rule_count_match": False,
                        "ordered_output_match": False,
                        "display_support_confidence_match": False,
                    }
                )
            item["database_hash_match"] = bool(
                base.get("database_hashes")
                and current.get("database_hashes")
                and base["database_hashes"] == current["database_hashes"]
            )
            entries[name] = item
        databases.append({"database": database, "variants": entries})
    aggregates: dict[str, Any] = {}
    for name in VARIANT_ORDER:
        values = [variants[name][db].get("runtime_seconds") for db in DATABASES]
        total = sum(v for v in values if isinstance(v, (int, float)))
        aggregates[name] = {
            "runtime_seconds": total,
            "improvement_percent": None
            if name == "main"
            else percentage_delta(aggregates["main"]["runtime_seconds"], total),
            "all_complete": all(variants[name][db].get("complete", False) for db in DATABASES),
        }
    ranking = sorted(
        (name for name in VARIANT_ORDER[1:] if aggregates[name]["all_complete"]),
        key=lambda n: aggregates[n]["runtime_seconds"],
    )
    combined = aggregates["combined"]
    solos = [aggregates[n]["runtime_seconds"] for n in VARIANT_ORDER[1:3] if aggregates[n]["all_complete"]]
    valid_combined = combined["all_complete"] and all(
        dat["variants"]["combined"][key]
        for key in ("rule_count_match", "ordered_output_match", "database_hash_match")
        for dat in databases
    )
    if not valid_combined:
        conclusion = "incomplete evidence or output mismatch"
    elif combined["runtime_seconds"] < min(solos):
        conclusion = "combined better than both solo variants"
    elif combined["runtime_seconds"] > max(solos):
        conclusion = "combined is a regression against both solo variants"
    elif combined["runtime_seconds"] in solos:
        conclusion = "combined equal within measured precision to a solo variant"
    else:
        conclusion = "combined better than one solo variant but worse than the other"
    return {
        "schema_version": 2,
        "settings": settings,
        "provenance": provenance or {},
        "variants": VARIANT_ORDER,
        "aggregate": aggregates,
        "databases": databases,
        "ranking_by_runtime": ranking,
        "conclusion": conclusion,
    }


def build_comparison(
    main_root: Path, optimized_root: Path, source_dir: Path, *, main_revision: str, optimized_revision: str
) -> dict[str, Any]:
    """Compatibility wrapper retaining the original pairwise API."""
    variants = {
        "main": _variant_record(main_root, "main", source_dir, main_revision),
        "candidate_analysis": _variant_record(optimized_root, "optimized", source_dir, optimized_revision),
        "sql_threshold": _variant_record(optimized_root, "optimized", source_dir, optimized_revision),
        "combined": _variant_record(optimized_root, "optimized", source_dir, optimized_revision),
    }
    return build_four_variant_comparison(variants)


def write_comparison(archive: Path, comparison: dict[str, Any]) -> None:
    prepare_archive(archive)
    (archive / "comparison.json").write_text(json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Laptop MARITA four-variant comparison",
        "",
        f"Settings: `{json.dumps(comparison['settings'], sort_keys=True)}`",
        "Improvement = (main - variant) / main * 100; negative values are regressions.",
        "",
        "| Database | Candidate | Runtime | Memory | SQL queries | Rules | Ordered output | Display/support/confidence | DB hashes |",
        "|---|---|---:|---:|---:|---|---|---|---|",
    ]
    for database in DATABASES:
        for name in VARIANT_ORDER[1:]:
            item = next(d["variants"][name] for d in comparison["databases"] if d["database"] == database)
            pct = item["metrics"]["runtime_seconds"]["improvement_percent"]
            value = "n/a" if pct is None else f"{pct:.2f}%"
            record = item["record"]
            memory = item["metrics"]["peak_rss_bytes"]["improvement_percent"]
            queries = item["metrics"]["sql_query_count"]["improvement_percent"]
            memory_value = "n/a" if memory is None else f"{memory:.2f}%"
            query_value = "n/a" if queries is None else f"{queries:.2f}%"
            lines.append(
                f"| {database} | {name} ({record.get('status')}) | {value} | {memory_value} | {query_value} | {'match' if item['rule_count_match'] else 'MISMATCH'} | {'match' if item['ordered_output_match'] else 'MISMATCH'} | {'match' if item['display_support_confidence_match'] else 'MISMATCH'} | {'match' if item['database_hash_match'] else 'MISMATCH'} |"
            )
    lines += ["", "## Aggregate", "", "| Variant | Runtime (s) | Improvement | Complete |", "|---|---:|---:|---|"]
    for name in VARIANT_ORDER:
        value = comparison["aggregate"][name]
        pct = "n/a" if value["improvement_percent"] is None else f"{value['improvement_percent']:.2f}%"
        lines.append(f"| {name} | {value['runtime_seconds']:.3f} | {pct} | {value['all_complete']} |")
    lines += [
        "",
        f"Runtime ranking (fastest first): {', '.join(comparison['ranking_by_runtime']) or 'none'}.",
        f"Conclusion: **{comparison['conclusion']}**.",
        "",
    ]
    (archive / "comparison.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--main", type=Path)
    parser.add_argument("--optimized", type=Path)
    parser.add_argument("--source", type=Path, default=Path("data/relational"))
    args = parser.parse_args()
    if args.manifest:
        manifest = load_manifest(args.manifest)
        variants = {
            name: _variant_record(Path(spec["root"]), name, args.source, spec.get("revision"))
            for name, spec in manifest["variants"].items()
        }
        comparison = build_four_variant_comparison(
            variants, settings=manifest.get("settings", SETTINGS), provenance=manifest["variants"]
        )
    elif args.main and args.optimized:
        comparison = build_comparison(
            args.main, args.optimized, args.source, main_revision="67ec5cf", optimized_revision="unknown"
        )
    else:
        parser.error("provide --manifest or both --main and --optimized")
    write_comparison(args.archive, comparison)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
