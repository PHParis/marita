from __future__ import annotations

import logging
import os
from typing import Any

import psutil
from sqlalchemy import select, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import SQLAlchemyError

from mahilda.database.data_exporter import DataExporter
from mahilda.database.database_connection_manager import DatabaseConnectionManager
from mahilda.database.index_manager import IndexManager
from mahilda.database.query_utility import QueryUtility
from mahilda.database.triple_converter import TripleConverter
from mahilda.utils.log_setup import setup_loggers


class AlchemyUtility:
    """High-level database facade used by rule-discovery algorithms."""

    def __init__(
        self,
        db_url: str,
        database_path: str = "",
        create_index: bool = True,
        create_csv: bool = True,
        create_tsv: bool = True,
        get_data: bool = True,
    ) -> None:
        setup_loggers(log_dir=os.environ.get("MAHILDA_LOG_DIR", "logs"))
        self.logger_query_time = logging.getLogger("query_time")
        self.logger_query_results = logging.getLogger("query_results")

        self.db_url = db_url
        self.database_path = database_path

        url = make_url(db_url)
        if url.drivername == "sqlite" and url.database:
            self.base_name = os.path.splitext(os.path.basename(url.database))[0]
        else:
            self.base_name = str(url).split("//")[-1].split(":")[0]

        self.db_manager = DatabaseConnectionManager(db_url)
        if self.db_manager.conn is None or self.db_manager.engine is None:
            raise RuntimeError("Failed to initialize database connection manager")

        self.index_manager = IndexManager(self.db_manager.conn, self.db_manager.metadata)
        self.data_exporter = DataExporter(
            db_path=self.database_path,
            base_name=self.base_name,
            engine=self.db_manager.engine,
            metadata=self.db_manager.metadata,
            logger_query_time=self.logger_query_time,
            logger_query_results=self.logger_query_results,
        )
        self.triple_converter = TripleConverter(
            engine=self.db_manager.engine,
            metadata=self.db_manager.metadata,
            logger=self.logger_query_time,
        )
        self.query_utility = QueryUtility(
            engine=self.db_manager.engine,
            metadata=self.db_manager.metadata,
            logger_query_time=self.logger_query_time,
            logger_query_results=self.logger_query_results,
        )

        if create_csv:
            self.data_exporter.export_tables_to_csv()
            self.base_csv_dir = os.path.join(self.database_path, self.base_name, "csv")

        if url.drivername == "sqlite":
            self._setup_sqlite(create_index)

        if create_tsv:
            triples = self.triple_converter.convert_to_triples()
            self.data_exporter.export_triples_to_tsv(triples)
            self.database_path_tsv = os.path.join(self.database_path, self.base_name, "tsv")

        self.tables_data = self._extract_table_data() if get_data else {}

    def _setup_sqlite(self, create_index: bool) -> None:
        conn = self.db_manager.conn
        if conn is None:
            return

        conn.execute(text("PRAGMA temp_store = MEMORY;"))
        conn.execute(text(f"PRAGMA cache_size = {self.get_cache_size()};"))
        conn.execute(text("PRAGMA read_uncommitted = 1;"))
        conn.execute(text("PRAGMA synchronous = OFF;"))
        conn.commit()

        if create_index:
            try:
                self.index_manager.create_indexes()
                conn.commit()
            except SQLAlchemyError as err:
                self.logger_query_time.error(f"Error creating indexes: {err}")
                conn.rollback()

        conn.commit()

    @staticmethod
    def get_cache_size() -> int:
        """Return SQLite cache size in pages (about 10% of RAM)."""
        total_memory = psutil.virtual_memory().total
        cache_size_kb = (total_memory * 0.1) // 1024
        cache_size_pages = cache_size_kb // 1.024
        return int(cache_size_pages)

    def _extract_table_data(self) -> dict[str, dict[str, Any]]:
        data: dict[str, dict[str, Any]] = {}
        for table_name in sorted(self.db_manager.metadata.tables.keys()):
            table = self.db_manager.metadata.tables.get(table_name)
            if table is None:
                continue
            columns = [col.name for col in table.columns]
            rows = self._select_query(table_name, columns)
            data[table_name] = {"columns": columns, "rows": rows}
        return data

    def _select_query(self, table_name: str, attributes: list[str]) -> list[tuple[Any, ...]]:
        table_obj = self.db_manager.metadata.tables.get(table_name)
        if table_obj is None:
            return []

        columns = [table_obj.columns[attr] for attr in attributes if attr in table_obj.columns]
        if not columns:
            return []

        query = select(*columns)
        if self.db_manager.engine is None:
            return []

        try:
            with self.db_manager.engine.connect() as conn:
                return [tuple(row) for row in conn.execute(query).fetchall()]
        except SQLAlchemyError as err:
            self.logger_query_time.error(f"Error executing select query on '{table_name}': {err}")
            return []

    def create_composed_indexes(self, cols_list: list[tuple[str, str, str, str]]) -> None:
        self.index_manager.create_composed_indexes(cols_list)

    def check_threshold(
        self,
        join_conditions: list[tuple[str, int, str, str, int, str]],
        disjoint_semantics: bool = False,
        distinct: bool = False,
        count_over: list[list[tuple[str, int, str]]] | None = None,
        threshold: int = 1,
        flag: str = "",
    ) -> int:
        return self.query_utility.check_threshold(
            join_conditions,
            disjoint_semantics,
            distinct,
            count_over,
            threshold,
            flag,
        )

    def get_join_row_count(
        self,
        join_conditions: list[tuple[str, int, str, str, int, str]],
        disjoint_semantics: bool = False,
        distinct: bool = False,
        count_over: list[list[tuple[str, int, str]]] | None = None,
        flag: str = "",
    ) -> int:
        return self.query_utility.get_join_row_count(join_conditions, disjoint_semantics, distinct, count_over, flag)

    def get_rule_count(
        self,
        relation_occurrences: list[tuple[str, int]],
        equality_constraints: list[tuple[str, int, str, str, int, str]],
        projected_classes: list[list[tuple[str, int, str]]],
        disjoint_semantics: bool = False,
    ) -> int:
        return self.query_utility.get_rule_count(
            relation_occurrences,
            equality_constraints,
            projected_classes,
            disjoint_semantics,
        )

    def get_attribute_values(self, table_name: str, attribute_name: str) -> list[Any]:
        try:
            rows = self._select_query(table_name, [attribute_name])
            return [row[0] for row in rows if row and row[0] is not None]
        except SQLAlchemyError as err:
            self.logger_query_time.error(f"Error getting values for {table_name}.{attribute_name}: {err}")
            return []

    def get_attribute_value_set(self, table_name: str, attribute_name: str) -> frozenset[str]:
        """Return full-joinability values with the legacy string/filter semantics."""
        return self.query_utility.get_column_value_set(table_name, attribute_name)

    def get_table_names(self) -> list[str]:
        return self.query_utility._get_table_names()

    def get_attribute_names(self, table_name: str) -> list[str]:
        return self.query_utility._get_attribute_names(table_name)

    def get_attribute_domain(self, table_name: str, attribute_name: str) -> str | None:
        return self.query_utility._get_attribute_domain(table_name, attribute_name)

    def get_attribute_is_key(self, table_name: str, attribute_name: str) -> bool:
        return self.query_utility._get_attribute_is_key(table_name, attribute_name)

    def are_foreign_keys(
        self,
        table: str,
        column: str,
        other_table: str,
        other_column: str,
        log_errors: bool = False,
        log_level: str = "error",
    ) -> bool:
        foreign_keys = self.query_utility._get_foreign_keys()
        if table in foreign_keys and column in foreign_keys[table]:
            referenced_table, referenced_column = foreign_keys[table][column]
            if referenced_table == other_table and referenced_column == other_column:
                return True
            if log_errors:
                log_msg = (
                    f"Incorrect foreign key for table '{table}': '{column}' references "
                    f"'{referenced_table}.{referenced_column}' instead of '{other_table}.{other_column}'."
                )
                self._log_foreign_key_message(log_msg, log_level)
            return False

        if log_errors:
            self._log_foreign_key_message(
                f"Foreign key '{column}' does not exist in table '{table}'.",
                log_level,
            )
        return False

    def _log_foreign_key_message(self, message: str, log_level: str) -> None:
        if log_level == "warning":
            self.logger_query_time.warning(message)
        elif log_level == "debug":
            self.logger_query_time.debug(message)
        else:
            self.logger_query_time.error(message)

    def validate_foreign_key_constraint(
        self,
        table: str,
        column: str,
        expected_table: str,
        expected_column: str,
    ) -> bool:
        return self.are_foreign_keys(
            table,
            column,
            expected_table,
            expected_column,
            log_errors=True,
            log_level="error",
        )

    def check_foreign_key_silently(self, table: str, column: str, other_table: str, other_column: str) -> bool:
        return self.are_foreign_keys(table, column, other_table, other_column, log_errors=False)

    def get_table_arity(self, table_name: str) -> int:
        table = self.db_manager.metadata.tables.get(table_name)
        return len(table.columns) if table is not None else 0

    def close(self) -> None:
        self.query_utility.clear_caches()
        self.db_manager.close()

    def __enter__(self) -> AlchemyUtility:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)
    db_url = "sqlite:///tests/tpcc.db"
    with AlchemyUtility(db_url, database_path="tests") as alchemy_utility:
        logging.getLogger(__name__).info("Database exported in 'data/%s/'.", alchemy_utility.base_name)
