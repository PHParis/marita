from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import TYPE_CHECKING

from mahilda.audit.models import Atom, Evaluation, RelationalRule

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


class AuditEvaluationError(ValueError):
    """Raised when a rule cannot be evaluated against the SQLite database."""


class SQLiteRuleEvaluator:
    def __init__(self, database_path: Path, *, relation_disjoint: bool = True) -> None:
        self.database_path = database_path
        self.relation_disjoint = relation_disjoint
        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row
        self._tables = self._load_tables()
        self._primary_keys = self._load_primary_keys()
        self._foreign_keys = self._load_foreign_keys()

    def close(self) -> None:
        self.connection.close()

    def evaluate(self, rule: RelationalRule) -> Evaluation:
        predictions = self._count_assignments(rule.body, sorted(rule.body_variables()))
        support = self._count_assignments(rule.all_atoms(), sorted(rule.body_variables()))
        return Evaluation(support=support, predictions=predictions)

    def projected_head_rows(self, rule: RelationalRule) -> set[tuple[object, ...]]:
        atoms = rule.all_atoms()
        head_variables = [variable for _, variable in rule.head.terms]
        query, params = self._build_select_query(atoms, head_variables)
        try:
            rows = self.connection.execute(query, params).fetchall()
        except sqlite3.Error as exc:
            raise AuditEvaluationError(str(exc)) from exc
        return {tuple(row) for row in rows}

    def is_fk_joinable(self, rule: RelationalRule) -> bool:
        occurrences = _indexed_atoms(rule.all_atoms())
        variable_refs: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for alias, atom in occurrences:
            del alias
            for column, variable in atom.terms:
                variable_refs[variable].append((atom.table, column))

        for refs in variable_refs.values():
            for left_index, left in enumerate(refs):
                for right in refs[left_index + 1 :]:
                    if left == right:
                        continue
                    if not self._attributes_fk_joinable(left, right):
                        return False
        return True

    def is_relation_disjoint_vacuous(self, rule: RelationalRule) -> bool:
        atoms_by_table: dict[str, list[Atom]] = defaultdict(list)
        for atom in rule.all_atoms():
            atoms_by_table[atom.table].append(atom)

        for table, atoms in atoms_by_table.items():
            if len(atoms) < 2:
                continue
            primary_keys = self._primary_keys.get(table, ())
            if not primary_keys:
                continue
            for left_index, left in enumerate(atoms):
                left_terms = dict(left.terms)
                for right in atoms[left_index + 1 :]:
                    right_terms = dict(right.terms)
                    if all(left_terms.get(pk) == right_terms.get(pk) for pk in primary_keys):
                        return True
        return False

    def _count_assignments(self, atoms: Iterable[Atom], count_variables: list[str]) -> int:
        atoms_tuple = tuple(atoms)
        if not atoms_tuple:
            return 0
        query, params = self._build_count_query(atoms_tuple, count_variables)
        try:
            row = self.connection.execute(query, params).fetchone()
        except sqlite3.Error as exc:
            raise AuditEvaluationError(str(exc)) from exc
        if row is None:
            return 0
        return int(row[0] or 0)

    def _build_count_query(self, atoms: tuple[Atom, ...], count_variables: list[str]) -> tuple[str, list[object]]:
        select_query, params = self._build_select_query(atoms, count_variables)
        if "SELECT DISTINCT" not in select_query:
            return select_query.replace("SELECT *", "SELECT COUNT(*)", 1), params
        return f"SELECT COUNT(*) FROM ({select_query})", params

    def _build_select_query(self, atoms: tuple[Atom, ...], variables: list[str]) -> tuple[str, list[object]]:
        aliases = _indexed_atoms(atoms)
        from_clause = ", ".join(f"{_quote(atom.table)} AS {_quote(alias)}" for alias, atom in aliases)
        where_clauses: list[str] = []
        params: list[object] = []
        variable_refs: dict[str, list[str]] = defaultdict(list)

        for alias, atom in aliases:
            if atom.table not in self._tables:
                raise AuditEvaluationError(f"unknown_table:{atom.table}")
            table_columns = self._tables[atom.table]
            for column, variable in atom.terms:
                if column not in table_columns:
                    raise AuditEvaluationError(f"unknown_column:{atom.table}.{column}")
                variable_refs[variable].append(f"{_quote(alias)}.{_quote(column)}")

        for refs in variable_refs.values():
            first = refs[0]
            for other in refs[1:]:
                where_clauses.append(f"{first} = {other}")

        if self.relation_disjoint:
            where_clauses.extend(self._relation_disjoint_conditions(aliases))
        where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        select_cols = self._select_columns_for_variables(variable_refs, variables)
        if not select_cols:
            return f"SELECT * FROM {from_clause}{where_sql}", params
        select_sql = ", ".join(select_cols)
        return f"SELECT DISTINCT {select_sql} FROM {from_clause}{where_sql}", params

    def _relation_disjoint_conditions(self, aliases: tuple[tuple[str, Atom], ...]) -> list[str]:
        conditions: list[str] = []
        by_table: dict[str, list[tuple[str, Atom]]] = defaultdict(list)
        for alias, atom in aliases:
            by_table[atom.table].append((alias, atom))

        for table, table_aliases in by_table.items():
            if len(table_aliases) < 2:
                continue
            primary_keys = self._primary_keys.get(table, ())
            if not primary_keys:
                raise AuditEvaluationError(f"missing_primary_key_for_relation_disjoint:{table}")
            for left_index, (left_alias, _) in enumerate(table_aliases):
                for right_alias, _ in table_aliases[left_index + 1 :]:
                    pk_inequalities = [
                        f"{_quote(left_alias)}.{_quote(pk)} != {_quote(right_alias)}.{_quote(pk)}"
                        for pk in primary_keys
                    ]
                    conditions.append(f"({' OR '.join(pk_inequalities)})")
        return conditions

    @staticmethod
    def _select_columns_for_variables(variable_refs: dict[str, list[str]], variables: list[str]) -> list[str]:
        columns: list[str] = []
        for variable in variables:
            refs = variable_refs.get(variable)
            if refs:
                columns.append(refs[0])
        return columns

    def _attributes_fk_joinable(self, left: tuple[str, str], right: tuple[str, str]) -> bool:
        if left[0] == right[0]:
            return True
        return (left, right) in self._foreign_keys or (right, left) in self._foreign_keys

    def _load_tables(self) -> dict[str, set[str]]:
        tables: dict[str, set[str]] = {}
        rows = self.connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        for row in rows:
            table = str(row[0])
            columns = self.connection.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
            tables[table] = {str(column[1]) for column in columns}
        return tables

    def _load_primary_keys(self) -> dict[str, tuple[str, ...]]:
        primary_keys: dict[str, tuple[str, ...]] = {}
        for table in self._tables:
            rows = self.connection.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
            keys = sorted(((int(row[5]), str(row[1])) for row in rows if int(row[5]) > 0), key=lambda item: item[0])
            primary_keys[table] = tuple(column for _, column in keys)
        return primary_keys

    def _load_foreign_keys(self) -> set[tuple[tuple[str, str], tuple[str, str]]]:
        foreign_keys: set[tuple[tuple[str, str], tuple[str, str]]] = set()
        for table in self._tables:
            rows = self.connection.execute(f"PRAGMA foreign_key_list({_quote(table)})").fetchall()
            for row in rows:
                foreign_keys.add(((table, str(row[3])), (str(row[2]), str(row[4]))))
        return foreign_keys


def _indexed_atoms(atoms: Iterable[Atom]) -> tuple[tuple[str, Atom], ...]:
    counters: dict[tuple[str, int], int] = defaultdict(int)
    indexed: list[tuple[str, Atom]] = []
    for atom in atoms:
        key = (atom.table, atom.occurrence)
        counters[key] += 1
        suffix = counters[key] - 1
        alias = f"{atom.table}_{atom.occurrence}_{suffix}"
        indexed.append((alias, atom))
    return tuple(indexed)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
