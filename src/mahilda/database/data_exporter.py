import csv
import logging
import os

from sqlalchemy import MetaData, select
from sqlalchemy.engine import Engine


class DataExporter:
    """
    Handles exporting database tables to CSV and the entire database to TSV (triples).
    """

    def __init__(
        self,
        db_path: str,
        base_name: str,
        engine: Engine,
        metadata: MetaData,
        logger_query_time: logging.Logger,
        logger_query_results: logging.Logger,
    ) -> None:
        self.database_path = db_path
        self.base_name = base_name
        self.engine = engine
        self.metadata = metadata
        self.logger_query_time = logger_query_time
        self.logger_query_results = logger_query_results

    def export_tables_to_csv(self) -> None:
        """Export all tables to CSV files."""
        base_csv_dir = os.path.join(self.database_path, self.base_name, "csv")
        os.makedirs(base_csv_dir, exist_ok=True)

        for table_name in sorted(self.metadata.tables.keys()):
            table = self.metadata.tables.get(table_name)
            if table is None:
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

    def export_triples_to_tsv(self, triples: list[tuple[str, str, str]]):
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

