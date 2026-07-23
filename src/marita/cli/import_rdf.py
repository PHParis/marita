import argparse
import logging
from pathlib import Path

from marita.database.rdf_importer import import_rdf_benchmark


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import an RDF/Turtle KG into MARITA benchmark artifacts.")
    parser.add_argument("--input", required=True, help="Input RDF/Turtle file path")
    parser.add_argument("--output-dir", required=True, help="Directory where artifacts will be written")
    parser.add_argument(
        "--variants",
        default="core,ontology-lite",
        help="Comma-separated variants to materialize (default: core,ontology-lite)",
    )
    parser.add_argument(
        "--dataset-name",
        default=None,
        help="Output dataset name prefix (default: sanitized input filename stem)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    logger = logging.getLogger(__name__)
    variants = [variant.strip() for variant in args.variants.split(",") if variant.strip()]

    try:
        result = import_rdf_benchmark(
            input_path=Path(args.input),
            output_dir=Path(args.output_dir),
            variants=variants,
            dataset_name=args.dataset_name,
            progress=True,
        )
    except Exception as exc:
        logger.error("RDF import failed: %s", exc, exc_info=True)
        return 1

    artifacts = result.artifacts
    print(f"Full archive DB: {artifacts.full_db}")
    for variant, db_path in artifacts.variant_dbs.items():
        print(f"{variant} SQLite DB: {db_path}")
        print(f"{variant} AMIE3 TSV: {artifacts.variant_tsvs[variant]}")
    print(f"Manifest: {artifacts.manifest}")
    print(f"Report: {artifacts.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
