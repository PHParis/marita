import argparse

from mahilda.cli import (
    audit,
    batch,
    benchmark,
    download_databases,
    import_rdf,
    mlflow_start,
    mlflow_ui,
    paper_benchmark,
    paper_pipeline,
    run,
    smoke,
    test_data,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mahilda", description="MAHILDA command line interface")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run rule discovery for one database")
    run_parser.add_argument(
        "-c",
        "--config",
        default="configs/config.example.yaml",
        help="Config path (default: configs/config.example.yaml)",
    )

    benchmark_parser = subparsers.add_parser("benchmark", help="Run competitor baseline on one database")
    benchmark_parser.add_argument(
        "-c",
        "--config",
        default="configs/config.example.yaml",
        help="Config path (default: configs/config.example.yaml)",
    )
    benchmark_parser.add_argument(
        "--baseline",
        default=None,
        help="Benchmark baseline to run (AMIE3, SPIDER, POPPER, MATILDA)",
    )
    benchmark_parser.add_argument(
        "--input-tsv",
        default=None,
        help="Prebuilt TSV input for AMIE3; skips relational triple export when provided",
    )

    audit_parser = subparsers.add_parser("audit", help="Audit competitor rule coverage by MAHILDA")
    audit_parser.add_argument("--results-dir", default="results/paper_table2")
    audit_parser.add_argument("--database-dir", default="data/relational")
    audit_parser.add_argument("--output-dir", default=None)
    audit_parser.add_argument("--status-dir", default=None)
    audit_parser.add_argument("--target", default="MAHILDA")
    audit_parser.add_argument("--competitors", default="AMIE3,MATILDA,SPIDER,POPPER")
    audit_parser.add_argument("--settings", default=None)
    audit_parser.add_argument("--coverage", choices=("alpha", "subsumption", "instance"), default="alpha")
    audit_parser.add_argument("--walk-length", type=int, default=None)
    audit_parser.add_argument("--max-tables", type=int, default=None)
    audit_parser.add_argument("--max-variables", type=int, default=None)
    audit_parser.add_argument("--joinability", choices=("fk", "full"), default=None)
    audit_parser.add_argument("--no-disjoint-semantics", action="store_true")
    audit_parser.add_argument("--no-diagnose-unmatched", action="store_true")
    audit_parser.add_argument("--include-amie-rdf", action="store_true")
    audit_parser.add_argument("--confidence-threshold", type=float, default=1.0)
    audit_parser.add_argument("--max-examples", type=int, default=25)
    audit_parser.add_argument("--strict", action="store_true")
    audit_parser.add_argument("--no-progress", action="store_true")
    audit_parser.add_argument("--workers", type=int, default=None)
    audit_parser.add_argument("--hosts", default=None)
    audit_parser.add_argument("--host", default=None)
    audit_parser.add_argument("--heartbeat-seconds", type=int, default=None)
    audit_parser.add_argument("--stale-after-seconds", type=int, default=None)
    audit_parser.add_argument("--max-attempts", type=int, default=None)
    audit_parser.add_argument("--resume", action="store_true")
    audit_parser.add_argument("--reset-state", action="store_true")
    audit_parser.add_argument("--status", action="store_true")
    audit_parser.add_argument("--reuse-cache", action="store_true")
    audit_parser.add_argument("--trust-legacy-cache", action="store_true")

    import_rdf_parser = subparsers.add_parser("import-rdf", help="Import RDF/Turtle into benchmark artifacts")
    import_rdf_parser.add_argument("--input", required=True)
    import_rdf_parser.add_argument("--output-dir", required=True)
    import_rdf_parser.add_argument("--variants", default="core,ontology-lite")
    import_rdf_parser.add_argument("--dataset-name", default=None)

    download_parser = subparsers.add_parser(
        "download-databases",
        help="Download and convert relational benchmark databases",
    )
    download_parser.add_argument("-o", "--output", default="data/relational")
    download_parser.add_argument("--database", action="append", default=None)
    download_parser.add_argument("--max-databases", type=int, default=None)
    download_parser.add_argument("--timeout", type=int, default=300)
    download_parser.add_argument("--list", action="store_true")
    download_parser.add_argument("--dump-only", action="store_true")
    download_parser.add_argument("--convert-only", action="store_true")
    download_parser.add_argument("--quiet", action="store_true")

    batch_parser = subparsers.add_parser("batch", help="Run batch processing across many databases")
    batch_parser.add_argument(
        "-c",
        "--config",
        default="configs/config.example.yaml",
        help="Config path (default: configs/config.example.yaml)",
    )
    batch_parser.add_argument("-d", "--directory", default=None)
    batch_parser.add_argument("-o", "--output", default="results/batch")
    batch_parser.add_argument("-t", "--timeout", type=int, default=None)
    batch_parser.add_argument("--start-from", type=int, default=0)
    batch_parser.add_argument("--max-databases", type=int, default=None)
    batch_parser.add_argument("-w", "--workers", type=int, default=None)

    paper_parser = subparsers.add_parser("paper-benchmark", help="Run ISWC 2026 paper benchmark protocol")
    paper_parser.add_argument("--database-dir", default=None)
    paper_parser.add_argument("--output", default=None)
    paper_parser.add_argument("--logs", default=None)
    paper_parser.add_argument("--algorithms", default=None)
    paper_parser.add_argument("--databases", default=None)
    paper_parser.add_argument("--timeout", type=int, default=None)
    paper_parser.add_argument("--memory-gb", type=float, default=None)
    paper_parser.add_argument("--java-heap-gb", type=int, default=None)
    paper_parser.add_argument("--settings", default=None)
    paper_parser.add_argument("--host", default=None)
    paper_parser.add_argument("--hosts", default=None)
    paper_parser.add_argument("--dry-run", action="store_true")
    paper_parser.add_argument("--status", action="store_true")
    paper_parser.add_argument("--email-to", default=None)
    paper_parser.add_argument("--email-from", default=None)
    paper_parser.add_argument("--smtp-host", default=None)
    paper_parser.add_argument("--smtp-port", type=int, default=None)
    paper_parser.add_argument("--smtp-user", default=None)
    paper_parser.add_argument("--smtp-password-env", default="MAHILDA_SMTP_PASSWORD")
    paper_parser.add_argument("--smtp-starttls", action="store_true")

    pipeline_parser = subparsers.add_parser("paper-pipeline", help="Run the full paper experiment pipeline")
    pipeline_parser.add_argument("--settings", default="configs/paper/benchmark_83.yaml")
    pipeline_parser.add_argument("--host", default="auto")
    pipeline_parser.add_argument("--dry-run", action="store_true")
    pipeline_parser.add_argument("--status", action="store_true")
    pipeline_parser.add_argument("--reset", action="store_true")

    smoke_parser = subparsers.add_parser("smoke", help="Run fast local smoke test")
    smoke_parser.add_argument("-v", "--verbose", action="store_true")
    smoke_parser.add_argument("-q", "--quiet", action="store_true")
    smoke_parser.add_argument("-c", "--config", default="configs/config.test.yaml")

    test_db_parser = subparsers.add_parser("test-db", help="Create local test database")
    test_db_parser.add_argument("-o", "--output", default="test_data/test.db")

    mlflow_parser = subparsers.add_parser("mlflow", help="MLflow helper commands")
    mlflow_subparsers = mlflow_parser.add_subparsers(dest="mlflow_command", required=True)
    mlflow_subparsers.add_parser("start", help="Start local MLflow tracking server")
    mlflow_ui_parser = mlflow_subparsers.add_parser("ui", help="Launch MLflow UI")
    mlflow_ui_parser.add_argument("--port", type=int, default=5000, help="Port to bind MLflow UI")

    args = parser.parse_args(argv)

    if args.command == "run":
        return run.main(["--config", args.config])

    if args.command == "benchmark":
        benchmark_args: list[str] = ["--config", args.config]
        if args.baseline:
            benchmark_args.extend(["--baseline", args.baseline])
        if args.input_tsv:
            benchmark_args.extend(["--input-tsv", args.input_tsv])
        return benchmark.main(benchmark_args)

    if args.command == "audit":
        audit_args: list[str] = [
            "--results-dir",
            args.results_dir,
            "--database-dir",
            args.database_dir,
            "--target",
            args.target,
            "--competitors",
            args.competitors,
            "--coverage",
            args.coverage,
            "--confidence-threshold",
            str(args.confidence_threshold),
            "--max-examples",
            str(args.max_examples),
        ]
        if args.output_dir:
            audit_args.extend(["--output-dir", args.output_dir])
        if args.status_dir:
            audit_args.extend(["--status-dir", args.status_dir])
        if args.settings:
            audit_args.extend(["--settings", args.settings])
        if args.walk_length is not None:
            audit_args.extend(["--walk-length", str(args.walk_length)])
        if args.max_tables is not None:
            audit_args.extend(["--max-tables", str(args.max_tables)])
        if args.max_variables is not None:
            audit_args.extend(["--max-variables", str(args.max_variables)])
        if args.joinability is not None:
            audit_args.extend(["--joinability", args.joinability])
        if args.strict:
            audit_args.append("--strict")
        if args.no_progress:
            audit_args.append("--no-progress")
        if args.workers is not None:
            audit_args.extend(["--workers", str(args.workers)])
        if args.hosts:
            audit_args.extend(["--hosts", args.hosts])
        if args.host:
            audit_args.extend(["--host", args.host])
        if args.heartbeat_seconds is not None:
            audit_args.extend(["--heartbeat-seconds", str(args.heartbeat_seconds)])
        if args.stale_after_seconds is not None:
            audit_args.extend(["--stale-after-seconds", str(args.stale_after_seconds)])
        if args.max_attempts is not None:
            audit_args.extend(["--max-attempts", str(args.max_attempts)])
        if args.resume:
            audit_args.append("--resume")
        if args.reset_state:
            audit_args.append("--reset-state")
        if args.status:
            audit_args.append("--status")
        if args.no_disjoint_semantics:
            audit_args.append("--no-disjoint-semantics")
        if args.no_diagnose_unmatched:
            audit_args.append("--no-diagnose-unmatched")
        if args.include_amie_rdf:
            audit_args.append("--include-amie-rdf")
        if args.reuse_cache:
            audit_args.append("--reuse-cache")
        if args.trust_legacy_cache:
            audit_args.append("--trust-legacy-cache")
        return audit.main(audit_args)

    if args.command == "import-rdf":
        import_args = ["--input", args.input, "--output-dir", args.output_dir, "--variants", args.variants]
        if args.dataset_name:
            import_args.extend(["--dataset-name", args.dataset_name])
        return import_rdf.main(import_args)

    if args.command == "download-databases":
        download_args: list[str] = ["--output", args.output, "--timeout", str(args.timeout)]
        if args.database:
            for database in args.database:
                download_args.extend(["--database", database])
        if args.max_databases is not None:
            download_args.extend(["--max-databases", str(args.max_databases)])
        if args.list:
            download_args.append("--list")
        if args.dump_only:
            download_args.append("--dump-only")
        if args.convert_only:
            download_args.append("--convert-only")
        if args.quiet:
            download_args.append("--quiet")
        return download_databases.main(download_args)

    if args.command == "batch":
        batch_args: list[str] = [
            "--config",
            args.config,
            "--output",
            args.output,
        ]
        if args.timeout is not None:
            batch_args.extend(["--timeout", str(args.timeout)])
        batch_args.extend(["--start-from", str(args.start_from)])
        if args.workers is not None:
            batch_args.extend(["--workers", str(args.workers)])
        if args.directory:
            batch_args.extend(["--directory", args.directory])
        if args.max_databases is not None:
            batch_args.extend(["--max-databases", str(args.max_databases)])
        return batch.main(batch_args)

    if args.command == "paper-benchmark":
        paper_args: list[str] = []
        if args.database_dir is not None:
            paper_args.extend(["--database-dir", args.database_dir])
        if args.output is not None:
            paper_args.extend(["--output", args.output])
        if args.logs is not None:
            paper_args.extend(["--logs", args.logs])
        if args.algorithms is not None:
            paper_args.extend(["--algorithms", args.algorithms])
        if args.databases is not None:
            paper_args.extend(["--databases", args.databases])
        if args.timeout is not None:
            paper_args.extend(["--timeout", str(args.timeout)])
        if args.memory_gb is not None:
            paper_args.extend(["--memory-gb", str(args.memory_gb)])
        if args.java_heap_gb is not None:
            paper_args.extend(["--java-heap-gb", str(args.java_heap_gb)])
        if args.settings:
            paper_args.extend(["--settings", args.settings])
        if args.host:
            paper_args.extend(["--host", args.host])
        if args.hosts:
            paper_args.extend(["--hosts", args.hosts])
        if args.dry_run:
            paper_args.append("--dry-run")
        if args.status:
            paper_args.append("--status")
        if args.email_to:
            paper_args.extend(["--email-to", args.email_to])
        if args.email_from:
            paper_args.extend(["--email-from", args.email_from])
        if args.smtp_host:
            paper_args.extend(["--smtp-host", args.smtp_host])
        if args.smtp_port is not None:
            paper_args.extend(["--smtp-port", str(args.smtp_port)])
        if args.smtp_user:
            paper_args.extend(["--smtp-user", args.smtp_user])
        if args.smtp_password_env != "MAHILDA_SMTP_PASSWORD":
            paper_args.extend(["--smtp-password-env", args.smtp_password_env])
        if args.smtp_starttls:
            paper_args.append("--smtp-starttls")
        return paper_benchmark.main(paper_args)

    if args.command == "paper-pipeline":
        pipeline_args: list[str] = ["--settings", args.settings, "--host", args.host]
        if args.dry_run:
            pipeline_args.append("--dry-run")
        if args.status:
            pipeline_args.append("--status")
        if args.reset:
            pipeline_args.append("--reset")
        return paper_pipeline.main(pipeline_args)

    if args.command == "smoke":
        smoke_args: list[str] = ["--config", args.config]
        if args.verbose:
            smoke_args.append("--verbose")
        if args.quiet:
            smoke_args.append("--quiet")
        return smoke.main(smoke_args)

    if args.command == "test-db":
        return test_data.main(["--output", args.output])

    if args.command == "mlflow":
        if args.mlflow_command == "start":
            return mlflow_start.main([])
        if args.mlflow_command == "ui":
            return mlflow_ui.main(["--port", str(args.port)])

    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
