from __future__ import annotations

import logging
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from colorama import Fore, Style
from sqlalchemy import MetaData, alias, and_, false, func, or_, select, true

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from mahilda.database.foreign_keys import ForeignKeyMap


class ColorFormatter(logging.Formatter):
    COLOR_MAP = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.MAGENTA,
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLOR_MAP.get(record.levelno, Fore.WHITE)
        record.msg = f"{color}{record.msg}{Style.RESET_ALL}"
        return super().format(record)


class QueryUtility:
    """Handle threshold and join-count SQL query construction/execution."""

    DEFAULT_QUERY_CACHE_LIMIT = 1024
    DEFAULT_COLUMN_VALUE_CACHE_LIMIT = 250_000
    DEFAULT_COLUMN_VALUE_CACHE_ENTRY_LIMIT = 4096

    def __init__(
        self,
        engine: Engine,
        metadata: MetaData,
        logger_query_time: logging.Logger,
        logger_query_results: logging.Logger,
        query_cache_limit: int = DEFAULT_QUERY_CACHE_LIMIT,
        column_value_cache_limit: int = DEFAULT_COLUMN_VALUE_CACHE_LIMIT,
    ) -> None:
        self.engine = engine
        self.metadata = metadata
        self.logger_query_time = logger_query_time
        self.logger_query_results = logger_query_results
        self.query_cache_limit = max(0, query_cache_limit)
        self.column_value_cache_limit = max(0, column_value_cache_limit)
        self.column_value_cache_entry_limit = self.DEFAULT_COLUMN_VALUE_CACHE_ENTRY_LIMIT
        self._query_result_cache: OrderedDict[tuple[Any, ...], int] = OrderedDict()
        self._column_value_set_cache: OrderedDict[tuple[str, str], frozenset[str]] = OrderedDict()
        self._column_value_cache_size = 0
        self.query_cache_hits = 0
        self.query_cache_misses = 0
        self.column_value_cache_hits = 0
        self.column_value_cache_misses = 0
        self._table_names_cache: tuple[str, ...] | None = None
        self._column_names_cache: dict[str, tuple[str, ...]] = {}
        self._column_name_sets_cache: dict[str, frozenset[str]] = {}
        self._primary_key_cache: dict[str, tuple[str, ...]] = {}
        self._foreign_keys_cache: ForeignKeyMap | None = None
        self._setup_logging_handlers()

    @staticmethod
    def _freeze(value: Any) -> Any:
        """Convert query arguments into a stable, hashable cache key."""
        if isinstance(value, dict):
            return tuple(sorted((QueryUtility._freeze(key), QueryUtility._freeze(item)) for key, item in value.items()))
        if isinstance(value, (list, tuple)):
            return tuple(QueryUtility._freeze(item) for item in value)
        if isinstance(value, (set, frozenset)):
            return tuple(sorted((QueryUtility._freeze(item) for item in value), key=repr))
        return value

    def _cached_query_result(self, key: tuple[Any, ...]) -> int | None:
        result = self._query_result_cache.get(key)
        if result is None:
            self.query_cache_misses += 1
            return None
        self._query_result_cache.move_to_end(key)
        self.query_cache_hits += 1
        return result

    def _store_query_result(self, key: tuple[Any, ...], result: int) -> None:
        if self.query_cache_limit <= 0:
            return
        self._query_result_cache[key] = result
        self._query_result_cache.move_to_end(key)
        while len(self._query_result_cache) > self.query_cache_limit:
            self._query_result_cache.popitem(last=False)

    def clear_caches(self) -> None:
        """Clear all lifecycle-scoped metadata, value, and query caches."""
        self._query_result_cache.clear()
        self._column_value_set_cache.clear()
        self._column_value_cache_size = 0
        self._table_names_cache = None
        self._column_names_cache.clear()
        self._column_name_sets_cache.clear()
        self._primary_key_cache.clear()
        self._foreign_keys_cache = None
        self.query_cache_hits = 0
        self.query_cache_misses = 0
        self.column_value_cache_hits = 0
        self.column_value_cache_misses = 0

    close = clear_caches

    def _setup_logging_handlers(self) -> None:
        formatter = ColorFormatter("%(asctime)s - %(levelname)s - %(message)s")
        if not self.logger_query_time.handlers:
            handler_time = logging.StreamHandler()
            handler_time.setFormatter(formatter)
            self.logger_query_time.addHandler(handler_time)
            self.logger_query_time.setLevel(logging.DEBUG)

        if not self.logger_query_results.handlers:
            handler_results = logging.StreamHandler()
            handler_results.setFormatter(formatter)
            self.logger_query_results.addHandler(handler_results)
            self.logger_query_results.setLevel(logging.DEBUG)

    def check_threshold(
        self,
        join_conditions: list[tuple[str, int, str, str, int, str]],
        disjoint_semantics: bool = False,
        distinct: bool = False,
        count_over: list[list[tuple[str, int, str]]] | None = None,
        threshold: int = 1,
        flag: str = "threshold",
    ) -> int:
        key = (
            "threshold",
            self._freeze(join_conditions),
            bool(disjoint_semantics),
            bool(distinct),
            self._freeze(count_over),
            threshold,
            flag,
        )
        cached = self._cached_query_result(key)
        if cached is not None:
            return cached
        query, _, _ = self._construct_threshold_query(
            join_conditions,
            disjoint_semantics,
            distinct,
            count_over,
            threshold,
        )
        if query is None:
            raise ValueError(f"Invalid join conditions or query construction failed for flag: {flag}")

        try:
            with self.engine.connect() as conn:
                result = conn.execute(query).scalar()
        except Exception as err:
            raise ValueError(f"Error executing threshold query for flag '{flag}'") from err
        value = int(bool(result))
        self._store_query_result(key, value)
        return value

    def get_join_row_count(
        self,
        join_conditions: list[tuple[str, int, str, str, int, str]],
        disjoint_semantics: bool = False,
        distinct: bool = False,
        count_over: list[list[tuple[str, int, str]]] | None = None,
        flag: str = "",
    ) -> int:
        key = (
            "join_count",
            self._freeze(join_conditions),
            bool(disjoint_semantics),
            bool(distinct),
            self._freeze(count_over),
            flag,
        )
        cached = self._cached_query_result(key)
        if cached is not None:
            return cached
        query, _, _ = self._construct_count_query(join_conditions, disjoint_semantics, distinct, count_over)
        if query is None:
            return 0

        try:
            with self.engine.connect() as conn:
                result_sqlite = conn.execute(query).scalar()
        except Exception as err:
            self.logger_query_time.error(f"Error executing count query for flag '{flag}': {err}")
            return 0
        value = int(result_sqlite) if result_sqlite is not None else 0
        self._store_query_result(key, value)
        return value

    def get_rule_count(
        self,
        relation_occurrences: list[tuple[str, int]],
        equality_constraints: list[tuple[str, int, str, str, int, str]],
        projected_classes: list[list[tuple[str, int, str]]],
        disjoint_semantics: bool = False,
    ) -> int:
        """Count distinct variable assignments for an explicit rule query.

        Unlike the legacy join-condition API, this accepts occurrences which have
        no local join (including a one-atom relation) and makes the projected
        variable classes explicit.
        """
        key = (
            "rule_count",
            self._freeze(relation_occurrences),
            self._freeze(equality_constraints),
            self._freeze(projected_classes),
            bool(disjoint_semantics),
        )
        cached = self._cached_query_result(key)
        if cached is not None:
            return cached

        aliases: dict[str, Any] = {}
        for table_name, occurrence in relation_occurrences:
            self._get_or_create_alias(aliases, table_name, occurrence)
        if not aliases:
            return 0

        alias_values = list(aliases.values())
        join_base = alias_values[0].selectable
        for current in alias_values[1:]:
            join_base = join_base.join(current, true())

        predicates: list[Any] = []
        for table1, occurrence1, column1, table2, occurrence2, column2 in equality_constraints:
            left = aliases[f"{table1}_{occurrence1}"].columns[column1]
            right = aliases[f"{table2}_{occurrence2}"].columns[column2]
            predicates.append(left == right)
        if disjoint_semantics:
            by_table: dict[str, list[int]] = {}
            for table_name, occurrence in relation_occurrences:
                by_table.setdefault(table_name, []).append(occurrence)
            for table_name, occurrences in by_table.items():
                keys = self._get_primary_key_names(table_name)
                for index, occurrence1 in enumerate(occurrences):
                    for occurrence2 in occurrences[index + 1 :]:
                        if not keys:
                            predicates.append(false())
                            continue
                        predicates.append(
                            or_(
                                *(
                                    aliases[f"{table_name}_{occurrence1}"].columns[key]
                                    != aliases[f"{table_name}_{occurrence2}"].columns[key]
                                    for key in keys
                                )
                            )
                        )

        projection_columns: list[Any] = []
        for variable_class in projected_classes:
            if not variable_class:
                continue
            table_name, occurrence, column_name = variable_class[0]
            projection_columns.append(aliases[f"{table_name}_{occurrence}"].columns[column_name])
        if projection_columns:
            query = select(*projection_columns).distinct().select_from(join_base)
        else:
            query = select(true()).select_from(join_base)
        if predicates:
            query = query.where(and_(*predicates))
        count_query = select(func.count()).select_from(query.subquery())
        try:
            with self.engine.connect() as conn:
                value = conn.execute(count_query).scalar()
        except Exception as err:
            self.logger_query_time.error("Error executing explicit rule count: %s", err)
            return 0
        result = int(value or 0)
        self._store_query_result(key, result)
        return result

    def _construct_threshold_query(
        self,
        join_conditions: list[tuple[str, int, str, str, int, str]],
        disjoint_semantics: bool,
        distinct: bool,
        count_over: list[list[tuple[str, int, str]]] | None,
        threshold: int,
    ) -> tuple[Any | None, list[Any] | None, Any | None]:
        query, primary_key_conditions, join_base = self._construct_query_base(
            join_conditions,
            disjoint_semantics,
            distinct,
            count_over,
        )
        if join_base is None:
            return None, None, None

        threshold_query = select((func.count() > threshold).label("count_exceeds_threshold")).select_from(join_base)
        if primary_key_conditions:
            threshold_query = threshold_query.where(and_(*primary_key_conditions))
        return threshold_query, primary_key_conditions, join_base

    def _construct_count_query(
        self,
        join_conditions: list[tuple[str, int, str, str, int, str]],
        disjoint_semantics: bool,
        distinct: bool,
        count_over: list[list[tuple[str, int, str]]] | None,
    ) -> tuple[Any | None, list[Any] | None, Any | None]:
        return self._construct_query_base(join_conditions, disjoint_semantics, distinct, count_over)

    def _construct_query_base(
        self,
        join_conditions: list[tuple[str, int, str, str, int, str]],
        disjoint_semantics: bool,
        distinct: bool,
        count_over: list[list[tuple[str, int, str]]] | None,
    ) -> tuple[Any | None, list[Any] | None, Any | None]:
        condition_groups = self._organize_join_conditions(join_conditions)
        try:
            join_bases, aliases, used_aliases, table_occurrences = self._process_join_conditions(
                condition_groups,
                disjoint_semantics,
            )
        except ValueError as err:
            self.logger_query_time.error(f"Error processing join conditions: {err}")
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

    def _organize_join_conditions(
        self,
        join_conditions: list[tuple[str, int, str, str, int, str]],
    ) -> dict[frozenset[tuple[str, int]], list[tuple[str, int, str, str, int, str]]]:
        condition_groups: dict[frozenset[tuple[str, int]], list[tuple[str, int, str, str, int, str]]] = {}
        for condition in join_conditions:
            table_name1, occurrence1, _, table_name2, occurrence2, _ = condition
            key = frozenset({(table_name1, occurrence1), (table_name2, occurrence2)})
            condition_groups.setdefault(key, []).append(condition)
        return condition_groups

    def _process_join_conditions(
        self,
        condition_groups: dict[frozenset[tuple[str, int]], list[tuple[str, int, str, str, int, str]]],
        disjoint_semantics: bool,
    ) -> tuple[list[tuple[str, str | None, Any]], dict[str, Any], set[str], dict[str, set[int]]]:
        used_aliases: set[str] = set()
        aliases: dict[str, Any] = {}
        join_bases: list[tuple[str, str | None, Any]] = []
        table_occurrences: dict[str, set[int]] = {}

        for key, group in condition_groups.items():
            sorted_key = sorted(key)
            if len(sorted_key) == 2:
                table_name1, occurrence1 = sorted_key[0]
                table_name2, occurrence2 = sorted_key[1]

                if table_name1 not in self.metadata.tables or table_name2 not in self.metadata.tables:
                    continue

                if disjoint_semantics:
                    table_occurrences.setdefault(table_name1, set()).add(occurrence1)
                    table_occurrences.setdefault(table_name2, set()).add(occurrence2)

                alias1 = self._get_or_create_alias(aliases, table_name1, occurrence1)
                alias2 = self._get_or_create_alias(aliases, table_name2, occurrence2)

                partial_join_conditions = []
                columns_alias1 = self._get_column_name_set(table_name1)
                columns_alias2 = self._get_column_name_set(table_name2)
                for _tn1, _o1, attr1, _tn2, _o2, attr2 in group:
                    if attr1 in columns_alias1 and attr2 in columns_alias2:
                        partial_join_conditions.append(alias1.columns[attr1] == alias2.columns[attr2])
                    elif attr2 in columns_alias1 and attr1 in columns_alias2:
                        partial_join_conditions.append(alias1.columns[attr2] == alias2.columns[attr1])

                if partial_join_conditions:
                    join_condition = and_(*partial_join_conditions)
                    join_bases.append((f"{table_name1}_{occurrence1}", f"{table_name2}_{occurrence2}", join_condition))
                    used_aliases.add(f"{table_name1}_{occurrence1}")
                    used_aliases.add(f"{table_name2}_{occurrence2}")

            elif len(sorted_key) == 1:
                table_name1, occurrence1 = sorted_key[0]
                if table_name1 not in self.metadata.tables:
                    self.logger_query_time.error(f"Table '{table_name1}' does not exist; skipping condition.")
                    continue

                alias1 = self._get_or_create_alias(aliases, table_name1, occurrence1)
                partial_join_conditions = []
                columns_alias1 = self._get_column_name_set(table_name1)
                for _tn1, _o1, attr1, _tn2, _o2, attr2 in group:
                    if attr1 in columns_alias1 and attr2 in columns_alias1:
                        partial_join_conditions.append(alias1.columns[attr1] == alias1.columns[attr2])

                if partial_join_conditions:
                    join_condition = and_(*partial_join_conditions)
                    join_bases.append((f"{table_name1}_{occurrence1}", None, join_condition))
                    used_aliases.add(f"{table_name1}_{occurrence1}")

        return join_bases, aliases, used_aliases, table_occurrences

    def _get_or_create_alias(self, aliases: dict[str, Any], table_name: str, occurrence: int) -> Any:
        alias_key = f"{table_name}_{occurrence}"
        if table_name not in self.metadata.tables:
            raise ValueError(f"Table {table_name} does not exist in the database")
        if alias_key not in aliases:
            aliases[alias_key] = alias(self.metadata.tables[table_name], name=alias_key)
        return aliases[alias_key]

    def _construct_join(
        self,
        join_bases: list[tuple[str, str | None, Any]],
        aliases: dict[str, Any],
        _used_aliases: set[str],
    ) -> tuple[Any | None, list[Any]]:
        where_constraints: list[Any] = []
        used_aliases_in_join: set[str] = set()
        if not join_bases:
            return None, where_constraints

        first_base_key = join_bases[0][0]
        used_aliases_in_join.add(first_base_key)
        join_base = aliases[first_base_key].selectable

        pending = list(join_bases)
        while pending:
            remaining: list[tuple[str, str | None, Any]] = []
            progressed = False
            for alias_key1, alias_key2, join_condition in pending:
                if alias_key2 is None:
                    if alias_key1 in used_aliases_in_join:
                        where_constraints.append(join_condition)
                        progressed = True
                    else:
                        remaining.append((alias_key1, alias_key2, join_condition))
                elif alias_key1 in used_aliases_in_join and alias_key2 not in used_aliases_in_join:
                    used_aliases_in_join.add(alias_key2)
                    join_base = join_base.join(aliases[alias_key2], join_condition)
                    progressed = True
                elif alias_key2 in used_aliases_in_join and alias_key1 not in used_aliases_in_join:
                    used_aliases_in_join.add(alias_key1)
                    join_base = join_base.join(aliases[alias_key1], join_condition)
                    progressed = True
                elif alias_key1 in used_aliases_in_join and alias_key2 in used_aliases_in_join:
                    where_constraints.append(join_condition)
                    progressed = True
                else:
                    remaining.append((alias_key1, alias_key2, join_condition))
            if not progressed:
                break
            pending = remaining

        return join_base, where_constraints

    def _construct_primary_key_conditions(
        self,
        table_occurrences: dict[str, set[int]],
        aliases: dict[str, Any],
        used_aliases: set[str],
    ) -> list[Any]:
        primary_key_conditions: list[Any] = []
        for table_name, occurrences in table_occurrences.items():
            if len(occurrences) <= 1:
                continue

            pks = self._get_primary_key_names(table_name)
            if not pks:
                primary_key_conditions.append(false())
                continue
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
                    inequalities = [alias1.columns[pk] != alias2.columns[pk] for pk in pks]
                    primary_key_conditions.append(or_(*inequalities))

        return primary_key_conditions

    def _construct_select_query(
        self,
        join_base: Any,
        distinct: bool,
        primary_key_conditions: list[Any] | None = None,
        count_over: list[list[tuple[str, int, str]]] | None = None,
        aliases: dict[str, Any] | None = None,
    ) -> Any:
        if count_over:
            if not aliases:
                raise ValueError("Aliases must be provided when count_over is specified.")
            count_over_clause = []
            for x_class in count_over:
                for table_name, occurrence, attribute_name in x_class:
                    alias_key = f"{table_name}_{occurrence}"
                    if alias_key not in aliases:
                        raise ValueError(f"Alias {alias_key} not found in aliases")
                    count_over_clause.append(aliases[alias_key].columns[attribute_name])
                    break

            inner_query = select(*count_over_clause).distinct().select_from(join_base)
            if primary_key_conditions:
                inner_query = inner_query.where(and_(*primary_key_conditions))
            return select(func.count()).select_from(inner_query.subquery())

        query = select(func.count())
        if distinct:
            query = query.distinct()
        query = query.select_from(join_base)
        if primary_key_conditions:
            query = query.where(and_(*primary_key_conditions))
        return query

    def _get_table_names(self) -> list[str]:
        if self._table_names_cache is None:
            self._table_names_cache = tuple(sorted(self.metadata.tables.keys()))
        return list(self._table_names_cache)

    def _get_attribute_names(self, table_name: str) -> list[str]:
        names = self._column_names_cache.get(table_name)
        if names is None:
            names = tuple(col.name for col in self.metadata.tables[table_name].columns)
            self._column_names_cache[table_name] = names
            self._column_name_sets_cache[table_name] = frozenset(names)
        return list(names)

    def _get_column_name_set(self, table_name: str) -> frozenset[str]:
        if table_name not in self._column_name_sets_cache:
            self._get_attribute_names(table_name)
        return self._column_name_sets_cache[table_name]

    def _get_primary_key_names(self, table_name: str) -> tuple[str, ...]:
        keys = self._primary_key_cache.get(table_name)
        if keys is None:
            keys = tuple(column.name for column in self.metadata.tables[table_name].primary_key.columns)
            self._primary_key_cache[table_name] = keys
        return keys

    def _get_attribute_domain(self, table_name: str, attribute_name: str) -> str | None:
        table = self.metadata.tables.get(table_name)
        if table is not None and hasattr(table, "columns"):
            column = table.columns.get(attribute_name)
            if column is not None:
                return str(column.type)
        return None

    def _get_attribute_is_key(self, table_name: str, attribute_name: str) -> bool:
        table = self.metadata.tables.get(table_name)
        if table is not None and hasattr(table, "columns"):
            column = table.columns.get(attribute_name)
            if column is not None:
                return bool(column.primary_key)
        return False

    def _get_foreign_keys(self) -> ForeignKeyMap:
        if self._foreign_keys_cache is not None:
            return self._foreign_keys_cache
        foreign_keys_info: ForeignKeyMap = {}
        for table_name, table in self.metadata.tables.items():
            for fk in table.foreign_keys:
                ref_table = fk.column.table.name
                local_column = fk.parent.name
                reference_column = fk.column.name

                if ref_table not in self.metadata.tables:
                    self.logger_query_time.error(
                        f"Referenced table '{ref_table}' does not exist for foreign key "
                        f"'{local_column}' in table '{table_name}'."
                    )
                    continue

                if reference_column not in self.metadata.tables[ref_table].columns:
                    self.logger_query_time.error(
                        f"Referenced column '{reference_column}' does not exist in table "
                        f"'{ref_table}' for foreign key '{local_column}' in table '{table_name}'."
                    )
                    continue

                targets = foreign_keys_info.setdefault(table_name, {}).setdefault(local_column, ())
                foreign_keys_info[table_name][local_column] = tuple(
                    sorted(set(targets) | {(ref_table, reference_column)})
                )
        self._foreign_keys_cache = foreign_keys_info
        return foreign_keys_info

    def get_column_value_set(self, table_name: str, attribute_name: str) -> frozenset[str]:
        """Return the filtered string value set used by full joinability."""
        key = (table_name, attribute_name)
        cached = self._column_value_set_cache.get(key)
        if cached is not None:
            self._column_value_set_cache.move_to_end(key)
            self.column_value_cache_hits += 1
            return cached

        self.column_value_cache_misses += 1
        table = self.metadata.tables.get(table_name)
        if table is None or attribute_name not in table.columns:
            return frozenset()
        column = table.columns[attribute_name]
        try:
            with self.engine.connect() as conn:
                values = [row[0] for row in conn.execute(select(column)).fetchall()]
        except Exception as err:
            self.logger_query_time.error("Error getting values for %s.%s: %s", table_name, attribute_name, err)
            return frozenset()

        value_set = frozenset(map(str, filter(None, values)))
        if len(value_set) <= self.column_value_cache_limit:
            while (
                self._column_value_cache_size + len(value_set) > self.column_value_cache_limit
                or len(self._column_value_set_cache) >= self.column_value_cache_entry_limit
            ) and self._column_value_set_cache:
                _, removed = self._column_value_set_cache.popitem(last=False)
                self._column_value_cache_size -= len(removed)
            if self.column_value_cache_limit > 0:
                self._column_value_set_cache[key] = value_set
                self._column_value_cache_size += len(value_set)
        return value_set
