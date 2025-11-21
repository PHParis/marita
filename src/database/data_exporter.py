import csv
import hashlib
import logging
import os
import time
from typing import Any, Dict, List, Tuple

import psutil
from sqlalchemy import (
    MetaData,
    alias,
    and_,
    create_engine,
    func,
    select,
    text
)


#from utils.log_setup import setup_loggers
import colorama   # Ajout de colorama
colorama.init(autoreset=True)


class DataExporter:
    """
    Handles exporting database tables to CSV and the entire database to TSV (triples).
    """

    def __init__(self, db_path: str, base_name: str, engine, metadata: MetaData, logger_query_time, logger_query_results):
        self.database_path = db_path
        self.base_name = base_name
        self.engine = engine
        self.metadata = metadata
        self.logger_query_time = logger_query_time
        self.logger_query_results = logger_query_results

    def export_tables_to_csv(self):
        """Export all tables to CSV files."""
        base_csv_dir = os.path.join(self.database_path, self.base_name, "csv")
        os.makedirs(base_csv_dir, exist_ok=True)

        for table_name in sorted(self.metadata.tables.keys()):
            table = self.metadata.tables.get(table_name)
            if  table is None:
                continue

            table_attributes = [col.name for col in table.columns]
            query = select(table)
            try:
                with self.engine.connect() as connection:
                    result = connection.execute(query)
                    rows = result.fetchall()
            except Exception as e:
                self.logger_query_time.error(f"Error fetching rows for table '{table_name}': {e}")
                continue

            csv_filename = os.path.join(base_csv_dir, f"{table_name}.csv")
            try:
                with open(csv_filename, "w", newline="") as csv_file:
                    csv_writer = csv.writer(csv_file)
                    csv_writer.writerow(table_attributes)
                    for row in rows:
                        csv_writer.writerow(row)
            except Exception as e:
                self.logger_query_time.error(f"Error writing CSV file '{csv_filename}': {e}")

    def export_triples_to_tsv(self, triples: List[Tuple[str, str, str]]):
        """Export the given triples to a TSV file."""
        base_dir = os.path.join(self.database_path, self.base_name)
        tsv_dir = os.path.join(base_dir, "tsv")
        os.makedirs(tsv_dir, exist_ok=True)
        tsv_filename = os.path.join(tsv_dir, f"{self.base_name}.tsv")

        try:
            with open(tsv_filename, "w") as file:
                for triple in triples:
                    file.write("\t".join(triple) + "\n")
        except Exception as e:
            self.logger_query_time.error(f"Error writing TSV file '{tsv_filename}': {e}")

    def export_triples_to_ttl(self, triples: List[Tuple[str, str, str]]):
        """Export the triples to a TTL file."""
        base_dir = os.path.join(self.database_path, self.base_name)
        ttl_dir = os.path.join(base_dir, "ttl")
        os.makedirs(ttl_dir, exist_ok=True)
        ttl_filename = os.path.join(ttl_dir, f"{self.base_name}.ttl")

        try:
            with open(ttl_filename, "w") as file:
                file.write("@prefix ex: <http://example.org/> .\n")
                file.write("@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n\n")

                for subject, predicate, obj in triples:
                    subject_iri = f"ex:{self._sanitize_identifier(subject)}"
                    predicate_iri = f"ex:{self._sanitize_identifier(predicate)}"
                    if obj.startswith('"') and obj.endswith('"'):
                        file.write(f"{subject_iri} {predicate_iri} {obj} .\n")
                    else:
                        object_iri = f"ex:{self._sanitize_identifier(obj)}"
                        file.write(f"{subject_iri} {predicate_iri} {object_iri} .\n")
        except Exception as e:
            self.logger_query_time.error(f"Error writing TTL file '{ttl_filename}': {e}")

    @staticmethod
    def _sanitize_identifier(identifier: str) -> str:
        """Sanitize an identifier to be used in RDF IRIs."""
        return str(identifier).replace(' ', '_').replace('"', '').replace("'", '')
