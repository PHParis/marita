import argparse

from mahilda.cli import batch, benchmark, download_databases, import_rdf, mlflow_start, mlflow_ui, run, smoke, test_data


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
        help="Benchmark baseline to run (AMIE3, SPIDER, POPPER)",
    )
    benchmark_parser.add_argument(
        "--input-tsv",
        default=None,
        help="Prebuilt TSV input for AMIE3; skips relational triple export when provided",
    )

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
