from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import MetaData, select

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from marita.database.foreign_keys import ForeignKeyMap


class TripleConverter:
    """
    Converts database tables into RDF-like triples.
    """

    def __init__(self, engine: Engine, metadata: MetaData, logger: logging.Logger) -> None:
        self.engine = engine
        self.metadata = metadata
        self.logger = logger

    def convert_to_triples(self) -> list[tuple[str, str, str]]:
        triples: list[tuple[str, str, str]] = []
        foreign_keys = self._get_foreign_keys()
        primary_keys = {table: self._get_primary_keys(table) for table in self._get_table_names()}

        for table_name in self._get_table_names():
            attributes = self._get_attribute_names(table_name)
            pk_columns = primary_keys.get(table_name, [])
            fk_columns = foreign_keys.get(table_name, {})

            if len(pk_columns) == 0 or len(attributes) == 1:
                self.logger.warning(f"Table {table_name} has no PK or only one column. Skipping.")
                continue

            rows = self._select_query(table_name, attributes)
            for row in rows:
                row_dict = dict(zip(attributes, row, strict=False))
                # Skip row if a primary key is missing
                if any(pk not in row_dict or row_dict[pk] is None for pk in pk_columns):
                    self.logger.error(f"Missing primary keys in table {table_name} for row {row_dict}.")
                    continue

                subject = self._generate_rdf_id(table_name, pk_columns, row_dict)

                for attribute, value in row_dict.items():
                    if value is None or attribute is None:
                        continue

                    predicate = f"{self._sanitize_identifier(table_name)}.{self._sanitize_identifier(attribute)}"
                    if attribute in fk_columns:
                        # Foreign key triple
                        try:
                            for ref_table, _ref_column in fk_columns[attribute]:
                                # A local column may reference more than one target.
                                ref_pk_columns = primary_keys.get(ref_table, [])
                                if not ref_pk_columns:
                                    self.logger.warning(f"Referenced table {ref_table} has no PK. Skipping.")
                                    continue

                                row_dict_fk: dict[str, Any] = {}
                                missing_pk_columns = []
                                if len(ref_pk_columns) == 1:
                                    reference_value = row_dict[attribute]
                                    if reference_value is None:
                                        continue
                                    row_dict_fk[ref_pk_columns[0]] = reference_value
                                else:
                                    for pk_col in ref_pk_columns:
                                        if pk_col in row_dict and row_dict[pk_col] is not None:
                                            row_dict_fk[pk_col] = row_dict[pk_col]
                                        else:
                                            missing_pk_columns.append(pk_col)
                                if missing_pk_columns:
                                    if self.logger.isEnabledFor(logging.DEBUG):
                                        self.logger.debug(
                                            f"Skipping FK triple for {table_name}.{attribute}: "
                                            f"missing columns {missing_pk_columns} needed for {ref_table} PK"
                                        )
                                    continue

                                ref_subject = self._generate_rdf_id(ref_table, ref_pk_columns, row_dict_fk)
                                triples.append((subject, predicate, ref_subject))
                        except Exception as e:
                            self.logger.error(f"Error processing foreign key for table {table_name}: {e}")
                    elif attribute not in pk_columns:
                        # Literal triple
                        safe_value = str(value).replace('"', '\\"')
                        triples.append((subject, predicate, f'"{safe_value}"'))

        return triples

    def _get_table_names(self) -> list[str]:
        return sorted(self.metadata.tables.keys())

    def _get_foreign_keys(self) -> ForeignKeyMap:
        foreign_keys_info: ForeignKeyMap = {}
        for table_name, table in self.metadata.tables.items():
            for fk in table.foreign_keys:
                ref_table = fk.column.table.name
                local_column = fk.parent.name
                reference_column = fk.column.name
                targets = foreign_keys_info.setdefault(table_name, {}).setdefault(local_column, ())
                foreign_keys_info[table_name][local_column] = tuple(
                    sorted(set(targets) | {(ref_table, reference_column)})
                )
        return foreign_keys_info

    def _get_primary_keys(self, table_name: str) -> list[str]:
        table = self.metadata.tables.get(table_name)
        if table is not None and table.primary_key:
            return [key.name for key in table.primary_key.columns]
        return []

    def _get_attribute_names(self, table_name: str) -> list[str]:
        table = self.metadata.tables.get(table_name)
        if table is not None and hasattr(table, "columns"):
            return [column.name for column in table.columns]
        return []

    def _select_query(self, table_name: str, attributes: list[str]) -> list[tuple[Any, ...]]:
        table_obj = self.metadata.tables.get(table_name)
        if table_obj is None:
            return []
        columns = [table_obj.columns[attr] for attr in attributes if attr in table_obj.columns]
        if not columns:
            return []
        query = select(*columns)
        try:
            with self.engine.connect() as conn:
                return [tuple(row) for row in conn.execute(query).fetchall()]
        except Exception as e:
            self.logger.error(f"Error executing select query on '{table_name}': {e}")
            return []

    def _generate_rdf_id(self, table: str, primary_keys: list[str], row_dict: dict[str, Any]) -> str:
        try:
            pk_values = "_".join(self._sanitize_identifier(str(row_dict[pk])) for pk in primary_keys)
        except KeyError as e:
            # Only log at debug level - this is expected for partial FK references
            if self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug(f"Missing key {e} in table {table} for row {row_dict}.")
            return self._sanitize_identifier("unknown_id")
        return f"{table}_{pk_values}"

    @staticmethod
    def _sanitize_identifier(identifier: str) -> str:
        return "".join(e if e.isalnum() else "_" for e in identifier)
