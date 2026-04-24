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

#from mahilda.utils.log_setup import setup_loggers
import colorama   # Ajout de colorama
colorama.init(autoreset=True)
from colorama import Fore, Style

class ColorFormatter(logging.Formatter):
    COLOR_MAP = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.MAGENTA,
    }

    def format(self, record):
        color = self.COLOR_MAP.get(record.levelno, Fore.WHITE)
        record.msg = f"{color}{record.msg}{Style.RESET_ALL}"
        return super().format(record)



class QueryUtility:
    """
    Handles complex queries, including threshold checks and join row counts.
    """

    def __init__(self, engine, metadata: MetaData, logger_query_time, logger_query_results):
        self.engine = engine
        self.metadata = metadata
        self.logger_query_time = logger_query_time
        self.logger_query_results = logger_query_results

        self._setup_logging_handlers()  # Setup logging handlers
    def _setup_logging_handlers(self):
        formatter = ColorFormatter('%(asctime)s - %(levelname)s - %(message)s')

        # Formatter for logger_query_time
        handler_time = logging.StreamHandler()
        handler_time.setFormatter(formatter)
        self.logger_query_time.addHandler(handler_time)
        self.logger_query_time.setLevel(logging.DEBUG)

        # Formatter for logger_query_results
        handler_results = logging.StreamHandler()
        handler_results.setFormatter(formatter)
        self.logger_query_results.addHandler(handler_results)
        self.logger_query_results.setLevel(logging.DEBUG)
    def check_threshold(
        self,
        join_conditions: List[Tuple[str, int, str, str, int, str]],
        disjoint_semantics: bool = False,
        distinct: bool = False,
        count_over: List[List[Tuple[str, int, str]]] = None,
        threshold: int = 1,
        flag:str="threshold"
    ) -> int:
        """
        Check if the count of resulting rows from the given join exceeds a threshold.
        """
        query, primary_key_conditions, join_base = self._construct_threshold_query(
            join_conditions, disjoint_semantics, distinct, count_over, threshold
        )
        # print(query)
        if query is None :
            raise ValueError(f"Invalid join conditions or query construction failed for flag: {flag}")
            return 0

        start = time.time()
        try:
            with self.engine.connect() as conn:
                result = conn.execute(query).scalar()
                result = bool(result)
        except Exception as e:
            raise ValueError(f"Error executing threshold query: {e} for flag: {flag}")
            self.logger_query_time.error(f"Error executing threshold query: {e}")
            return 0
        end = time.time()

        execution_time = end - start
        # self.logger_query_time.info(
        #     f"Execution Time: {execution_time:.4f} seconds for Threshold Query: {str(query)}"
        # )
        # self.logger_query_results.info(
        #     f"Threshold Query: {str(query)}; Result: {result}; Execution Time: {execution_time:.4f}"
        # )
        # self.logger_query_time.info(
        #     f"{Fore.GREEN}Execution Time: {execution_time:.4f} seconds for Threshold Query: {str(query)}"
        # )
        # self.logger_query_results.info(
        #     f"{Fore.GREEN}Threshold Query: {str(query)}; Result: {result}; Execution Time: {execution_time:.4f}"
        # )
        return int(result) if result is not None else 0

    def get_join_row_count(
        self,
        join_conditions: List[Tuple[str, int, str, str, int, str]],
        disjoint_semantics: bool = False,
        distinct: bool = False,
        count_over: List[List[Tuple[str, int, str]]] = None,
    ) -> int:
        query, primary_key_conditions, join_base = self._construct_count_query(
            join_conditions, disjoint_semantics, distinct, count_over
        )

        if query is None:
            return 0

        start = time.time()
        try:
            with self.engine.connect() as conn:
                result_sqlite = conn.execute(query).scalar()
        except Exception as e:
            self.logger_query_time.error(f"Error executing query: {e}")
            return 0
        end = time.time()

        execution_time_sqlite = end - start
        # self.logger_query_time.info(
        #     f"Execution Time: {execution_time_sqlite:.4f} seconds for Query: {str(query)}"
        # )
        # self.logger_query_results.info(
        #     f"Query: {str(query)}; Result: {result_sqlite}"
        # )

        return result_sqlite if result_sqlite is not None else 0

    # Below methods are similar to the original code but reorganized for clarity.

    def _construct_threshold_query(
        self,
        join_conditions,
        disjoint_semantics,
        distinct,
        count_over,
        threshold
    ):
        # Construct the query and return it along with conditions
        query, primary_key_conditions, join_base = self._construct_query_base(
            join_conditions, disjoint_semantics, distinct, count_over
        )
        if join_base is None :
            return None, None, None
        query = select((func.count() > threshold).label("count_exceeds_threshold")).select_from(join_base)
        if primary_key_conditions:
            query = query.where(and_(*primary_key_conditions))
        return query, primary_key_conditions, join_base

    def _construct_count_query(
        self,
        join_conditions,
        disjoint_semantics,
        distinct,
        count_over
    ):
        # Construct the query and return it along with conditions
        query, primary_key_conditions, join_base = self._construct_query_base(
            join_conditions, disjoint_semantics, distinct, count_over
        )
        return query, primary_key_conditions, join_base

    def _construct_query_base(
        self,
        join_conditions,
        disjoint_semantics,
        distinct,
        count_over
    ):
        condition_groups = self._organize_join_conditions(join_conditions)
        try:
            join_bases, aliases, used_aliases, table_occurrences = (
                self._process_join_conditions(condition_groups, disjoint_semantics)
            )
        except Exception as e:
            self.logger_query_time.error(f"Error processing join conditions: {e}")
            return None, None, None

        if not join_bases:
            return None, None, None

        join_base, where_constraints = self._construct_join(join_bases, aliases, used_aliases)
        if disjoint_semantics:
            primary_key_conditions = self._construct_primary_key_conditions(table_occurrences, aliases, used_aliases)
            primary_key_conditions += where_constraints
        else:
            primary_key_conditions = where_constraints

        query = self._construct_select_query(join_base, distinct, primary_key_conditions, count_over, aliases)
        return query, primary_key_conditions, join_base

    def _organize_join_conditions(self, join_conditions: List[Tuple[str, int, str, str, int, str]]):
        condition_groups = {}
        for condition in join_conditions:
            table_name1, occurrence1, attribute_name1, table_name2, occurrence2, attribute_name2 = condition
            key = frozenset({(table_name1, occurrence1), (table_name2, occurrence2)})
            if key not in condition_groups:
                condition_groups[key] = []
            condition_groups[key].append(condition)
        return condition_groups

    def _process_join_conditions(
        self,
        condition_groups: Dict[frozenset, List[Tuple[str, int, str, str, int, str]]],
        disjoint_semantics: bool,
    ):
        used_aliases = set()
        aliases = {}
        join_bases = []
        table_occurrences = {}

        for key, group in condition_groups.items():
            sorted_key = sorted(list(key))
            if len(sorted_key) == 2:
                table_name1, occurrence1 = sorted_key[0]
                table_name2, occurrence2 = sorted_key[1]

                if table_name1 not in self.metadata.tables:
                    #self.logger_query_time.debug(f"Table '{table_name1}' does not exist; skipping join condition.")
                    continue
                if table_name2 not in self.metadata.tables:
                    #self.logger_query_time.debug(f"Table '{table_name2}' does not exist; skipping join condition.")
                    continue

                if disjoint_semantics:
                    table_occurrences.setdefault(table_name1, set()).add(occurrence1)
                    table_occurrences.setdefault(table_name2, set()).add(occurrence2)

                alias1 = self._get_or_create_alias(aliases, table_name1, occurrence1)
                alias2 = self._get_or_create_alias(aliases, table_name2, occurrence2)

                partial_join_conditions = []
                for (tn1, o1, attr1, tn2, o2, attr2) in group:
                    columns_alias1 = [str(el).split(".")[1] for el in alias1.columns._all_columns]
                    columns_alias2 = [str(el).split(".")[1] for el in alias2.columns._all_columns]

                    if attr1 in columns_alias1 and attr2 in columns_alias2:
                        partial_join_conditions.append(alias1.columns[attr1] == alias2.columns[attr2])
                    elif attr2 in columns_alias1 and attr1 in columns_alias2:
                        partial_join_conditions.append(alias1.columns[attr2] == alias2.columns[attr1])

                if partial_join_conditions:
                    join_condition = and_(*partial_join_conditions)
                    join_bases.append(
                        (f"{table_name1}_{occurrence1}", f"{table_name2}_{occurrence2}", join_condition)
                    )
                    used_aliases.add(f"{table_name1}_{occurrence1}")
                    used_aliases.add(f"{table_name2}_{occurrence2}")

            elif len(sorted_key) == 1:
                (table_name1, occurrence1) = sorted_key[0]
                if table_name1 not in self.metadata.tables:
                    self.logger_query_time.error(f"Table '{table_name1}' does not exist; skipping condition.")
                    continue
                alias1 = self._get_or_create_alias(aliases, table_name1, occurrence1)
                partial_join_conditions = []
                for (tn1, o1, attr1, tn2, o2, attr2) in group:
                    columns_alias1 = [str(el).split(".")[1] for el in alias1.columns._all_columns]
                    if attr1 in columns_alias1 and attr2 in columns_alias1:
                        partial_join_conditions.append(alias1.columns[attr1] == alias1.columns[attr2])
                if partial_join_conditions:
                    join_condition = and_(*partial_join_conditions)
                    join_bases.append((f"{table_name1}_{occurrence1}", None, join_condition))
                    used_aliases.add(f"{table_name1}_{occurrence1}")

        return join_bases, aliases, used_aliases, table_occurrences

    def _get_or_create_alias(self, aliases: Dict[str, Any], table_name: str, occurrence: int):
        alias_key = f"{table_name}_{occurrence}"
        if table_name not in self.metadata.tables:
            raise ValueError(f"Table {table_name} does not exist in the database")
        if alias_key not in aliases:
            aliases[alias_key] = alias(self.metadata.tables[table_name], name=alias_key)
        return aliases[alias_key]

    def _construct_join(self, join_bases, aliases: Dict[str, Any], used_aliases: set):
        where_constraints = []
        used_aliases_in_join = set()

        if not join_bases:
            return None, where_constraints

        first_base_key = join_bases[0][0]
        used_aliases_in_join.add(first_base_key)
        join_base = aliases[first_base_key].selectable
        for alias_key1, alias_key2, join_condition in join_bases:
            if alias_key2 is None:
                where_constraints.append(join_condition)
            elif alias_key1 in used_aliases_in_join and alias_key2 not in used_aliases_in_join:
                used_aliases_in_join.add(alias_key2)
                join_base = join_base.join(aliases[alias_key2], join_condition)
            elif alias_key2 in used_aliases_in_join and alias_key1 not in used_aliases_in_join:
                used_aliases_in_join.add(alias_key1)
                join_base = join_base.join(aliases[alias_key1], join_condition)
            elif alias_key1 in used_aliases_in_join and alias_key2 in used_aliases_in_join:
                where_constraints.append(join_condition)

        return join_base, where_constraints

    def _construct_primary_key_conditions(self, table_occurrences: Dict[str, set], aliases: Dict[str, Any], used_aliases: set):
        primary_key_conditions = []
        for table_name, occurrences in table_occurrences.items():
            if len(occurrences) <= 1:
                continue
            table_pk = self.metadata.tables[table_name].primary_key
            pks = [col.name for col in table_pk.columns] if table_pk else []
            for occurrence1 in occurrences:
                for occurrence2 in occurrences:
                    if occurrence1 >= occurrence2:
                        continue
                    alias_key1 = f"{table_name}_{occurrence1}"
                    alias_key2 = f"{table_name}_{occurrence2}"
                    if alias_key1 not in used_aliases or alias_key2 not in used_aliases:
                        continue
                    alias1 = aliases[alias_key1]
                    alias2 = aliases[alias_key2]
                    for pk in pks:
                        pk_condition = alias1.columns[pk] != alias2.columns[pk]
                        primary_key_conditions.append(pk_condition)
        return primary_key_conditions

    def _construct_select_query(
        self,
        join_base,
        distinct: bool,
        primary_key_conditions: List[Any] = None,
        count_over: List[List[Tuple[str, int, str]]] = None,
        aliases: Dict[str, Any] = None,
    ):
        if count_over and not aliases:
            raise ValueError("Aliases must be provided when count_over is specified.")

        if count_over and len(count_over) > 0:
            count_over_clause = []
            for x_class in count_over:
                for attribute in x_class:
                    table_name, occurrence, attribute_name = attribute
                    alias_key = f"{table_name}_{occurrence}"
                    if alias_key not in aliases:
                        raise ValueError(f"Alias {alias_key} not found in aliases")
                    count_over_clause.append(aliases[alias_key].columns[attribute_name])
                    break

            inner_query = select(*count_over_clause).distinct().select_from(join_base)
            if primary_key_conditions:
                inner_query = inner_query.where(and_(*primary_key_conditions))
            #query = select(func.count()).select_from(inner_query)
            query = select(func.count()).select_from(inner_query.subquery()) # for future version of sqlalchemy

        else:
            query = select(func.count()).distinct().select_from(join_base)
            if primary_key_conditions:
                query = query.where(and_(*primary_key_conditions))

        return query


    def _get_table_names(self) -> List[str]:
        return sorted(self.metadata.tables.keys())
    def _get_attribute_names(self, table_name: str) -> List[str]:
        return [col.name for col in self.metadata.tables[table_name].columns]
    def _get_attribute_domain(self, table_name: str, attribute_name: str) -> str:
        """
        Return the domain (data type) of a given attribute in a table.

        :param table_name: Name of the table.
        :param attribute_name: Name of the attribute (column).
        :return: The data type of the attribute as a string, or None if not found.
        """
        table = self.metadata.tables.get(table_name)
        if table is not None and hasattr(table, "columns"):
            column = table.columns.get(attribute_name)
            if column is not None:
                return str(column.type)
        return None
    def _get_attribute_is_key(self, table_name: str, attribute_name: str) -> bool:
        """
        Check if a given attribute (column) is part of the primary key in a table.

        :param table_name: Name of the table.
        :param attribute_name: Name of the attribute (column).
        :return: True if the attribute is part of the primary key, False otherwise.
        """
        table = self.metadata.tables.get(table_name)
        if table is not None and hasattr(table, "columns"):
            column = table.columns.get(attribute_name)
            if column is not None:
                return column.primary_key
        return False
    def _get_foreign_keys(self) -> Dict[str, Dict[str, Tuple[str, str]]]:
        """
        Retrieve all foreign key relationships in the database.
        Returns a dictionary where keys are table names and values are dicts with the key being an attribute
        and the values tuples containing (referenced_table, referenced_column).
        """
        foreign_keys_info = {}
        for table_name, table in self.metadata.tables.items():
            for fk in table.foreign_keys:
                ref_table = fk.column.table.name
                local_column = fk.parent.name
                reference_column = fk.column.name
                
                # Check if the referenced table exists
                if ref_table not in self.metadata.tables:
                    self.logger_query_time.error(f"Referenced table '{ref_table}' does not exist for foreign key '{local_column}' in table '{table_name}'.")
                    continue
                
                # Check if the referenced column exists
                if reference_column not in self.metadata.tables[ref_table].columns:
                    self.logger_query_time.error(f"Referenced column '{reference_column}' does not exist in table '{ref_table}' for foreign key '{local_column}' in table '{table_name}'.")
                    continue
                
                if table_name not in foreign_keys_info:
                    foreign_keys_info[table_name] = {}
                foreign_keys_info[table_name][local_column] = (ref_table, reference_column)
        return foreign_keys_info
