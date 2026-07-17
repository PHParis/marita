"""Measure bounded MAHILDA enumeration with the old and indexed graph paths.

The legacy path is intentionally kept here as a benchmark oracle, not as a
runtime option. It provides a reproducible before/after measurement while
the conformance tests independently verify that both paths enumerate the same
canonical rule set.
"""

from __future__ import annotations

import argparse
import contextlib
import cProfile
import io
import json
import os
import sqlite3
import time
import tracemalloc
from pathlib import Path
from types import MethodType

from mahilda.algorithms.mahilda_core.constraint_graph import ConstraintGraph, JoinableIndexedAttributes
from mahilda.algorithms.mahilda_core.tgd_discovery import (
    dfs,
    init,
    instantiate_tgd,
    path_pruning,
)
from mahilda.audit.parsing import parse_formula
from mahilda.database.alchemy_utility import AlchemyUtility


def _write_synthetic_fk_chain(path: Path, table_count: int = 8) -> Path:
    path.unlink(missing_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        for index in range(table_count):
            table = f"synthetic_{index:02d}"
            columns = "id INTEGER PRIMARY KEY, payload INTEGER NOT NULL"
            if index:
                previous = f"synthetic_{index - 1:02d}"
                columns = f"id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL REFERENCES {previous}(id), payload INTEGER NOT NULL"
            connection.execute(f"CREATE TABLE {table} ({columns})")
            if index:
                connection.executemany(
                    f"INSERT INTO {table}(id, parent_id, payload) VALUES (?, ?, ?)",
                    [(index * 100 + row, index * 100 + row - 100, row) for row in range(1, 4)],
                )
            else:
                connection.executemany(
                    f"INSERT INTO {table}(id, payload) VALUES (?, ?)",
                    [(row, row) for row in range(1, 4)],
                )
        connection.commit()
    finally:
        connection.close()
    return path


def _pairwise_graph(jia_list: list[JoinableIndexedAttributes]) -> ConstraintGraph:
    graph = ConstraintGraph()
    nodes = sorted(set(jia_list))
    for node in nodes:
        graph.add_node(node)
    for index, source in enumerate(nodes):
        for target in nodes[index + 1 :]:
            if source.is_connected(target):
                graph.add_edge(source, target)
    return graph


def _legacy_all_neighbors(self: ConstraintGraph, node: JoinableIndexedAttributes):
    neighbors = set(self.edges.get(node, set()))
    neighbors.update(source for source, targets in self.edges.items() if node in targets)
    return sorted(neighbors)


def _enumerate(
    graph: ConstraintGraph,
    database: AlchemyUtility,
    mapper,
    *,
    max_table: int,
    max_vars: int,
) -> tuple[float, tuple[tuple[object, float, float], ...]]:
    started = time.perf_counter()
    fingerprints = []
    for candidate, (body, head), _metrics in dfs(
        graph,
        None,
        path_pruning,
        database,
        mapper,
        max_table=max_table,
        max_vars=max_vars,
        support_threshold=1,
    ):
        parsed = parse_formula(instantiate_tgd(candidate, (body, head), mapper))
        fingerprints.append((parsed.canonical_key(), float(_metrics[0]), float(_metrics[1])))
    return time.perf_counter() - started, tuple(fingerprints)


def _timed_graph(factory, jia_list):
    tracemalloc.start()
    started = time.perf_counter()
    graph = factory(jia_list)
    elapsed = time.perf_counter() - started
    _current, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return elapsed, peak_bytes, graph


def benchmark(
    database_path: Path,
    *,
    max_occurrence: int = 3,
    max_table: int = 3,
    max_vars: int = 3,
    compare_enumeration: bool = False,
) -> dict[str, object]:
    output_dir = Path("/tmp") / f"mahilda-runtime-{database_path.stem}"
    output_dir.mkdir(parents=True, exist_ok=True)
    database = AlchemyUtility(
        f"sqlite:///{os.path.abspath(database_path)}",
        create_index=False,
        create_csv=False,
        create_tsv=False,
        get_data=False,
    )
    try:
        # The output is intentionally silenced so progress-bar rendering does
        # not distort the timing command's machine-readable result.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            import mahilda.algorithms.mahilda_core.tgd_discovery as discovery

            discovery.APPLY_DISJOINT = True
            discovery.APPLY_FULL_JOINABILITY = False
            discovery.SUPPORT_THRESHOLD = 1
            optimized, mapper, jia_list = init(
                database,
                max_nb_occurrence=max_occurrence,
                results_path=str(output_dir),
            )
            legacy_graph_seconds, legacy_graph_peak_bytes, legacy_graph = _timed_graph(_pairwise_graph, jia_list)
            indexed_graph_seconds, indexed_graph_peak_bytes, indexed_graph = _timed_graph(
                ConstraintGraph.from_jia_list,
                jia_list,
            )

            enumeration: dict[str, object] = {"enabled": compare_enumeration}
            if compare_enumeration:
                legacy_graph.all_neighbors = MethodType(_legacy_all_neighbors, legacy_graph)
                legacy_seconds, legacy_rules = _enumerate(
                    legacy_graph,
                    database,
                    mapper,
                    max_table=max_table,
                    max_vars=max_vars,
                )
                indexed_seconds, indexed_rules = _enumerate(
                    optimized,
                    database,
                    mapper,
                    max_table=max_table,
                    max_vars=max_vars,
                )
                enumeration.update(
                    {
                        "legacy_scan_neighbors_seconds": legacy_seconds,
                        "indexed_neighbors_seconds": indexed_seconds,
                        "legacy_rules": len(legacy_rules),
                        "indexed_rules": len(indexed_rules),
                        "same_canonical_rules": tuple(item[0] for item in legacy_rules)
                        == tuple(item[0] for item in indexed_rules),
                        "same_ordered_rule_fingerprints": legacy_rules == indexed_rules,
                    }
                )

        return {
            "database": database_path.stem,
            "settings": {
                "walk_length": max_occurrence,
                "max_tables": max_table,
                "max_variables": max_vars,
                "disjoint_semantics": True,
            },
            "graph": {
                "jia_nodes": len(jia_list),
                "edges": sum(len(targets) for targets in optimized.edges.values()),
                "legacy_pairwise_seconds": legacy_graph_seconds,
                "indexed_seconds": indexed_graph_seconds,
                "legacy_peak_allocated_bytes": legacy_graph_peak_bytes,
                "indexed_peak_allocated_bytes": indexed_graph_peak_bytes,
            },
            "enumeration": {
                **enumeration,
            },
        }
    finally:
        database.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        action="append",
        type=Path,
        default=None,
        help="SQLite workload; repeat the option for multiple databases.",
    )
    parser.add_argument(
        "--synthetic-fk-chain",
        action="store_true",
        help="Also benchmark a deterministic eight-table FK chain in /tmp.",
    )
    parser.add_argument("--max-occurrence", type=int, default=3)
    parser.add_argument("--max-table", type=int, default=3)
    parser.add_argument("--max-vars", type=int, default=3)
    parser.add_argument(
        "--compare-enumeration",
        action="store_true",
        help="Also compare the legacy and indexed DFS neighbor paths; use small bounds for this mode.",
    )
    parser.add_argument(
        "--profile-output",
        type=Path,
        help="Write a cProfile stats file for each benchmark invocation.",
    )
    args = parser.parse_args()
    database_paths = list(args.database or [])
    if args.synthetic_fk_chain:
        database_paths.insert(0, _write_synthetic_fk_chain(Path("/tmp/mahilda-synthetic-fk-chain.db")))
    if not database_paths:
        database_paths = [Path("data/relational/Carcinogenesis.db")]
    for database_path in database_paths:
        profiler = cProfile.Profile() if args.profile_output else None
        if profiler is not None:
            profiler.enable()
        result = benchmark(
            database_path,
            max_occurrence=args.max_occurrence,
            max_table=args.max_table,
            max_vars=args.max_vars,
            compare_enumeration=args.compare_enumeration,
        )
        if profiler is not None:
            profiler.disable()
            profile_path = args.profile_output
            if len(database_paths) > 1:
                profile_path = profile_path.with_name(f"{profile_path.stem}_{database_path.stem}{profile_path.suffix}")
            profiler.dump_stats(profile_path)
            result["profile"] = str(profile_path)
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
