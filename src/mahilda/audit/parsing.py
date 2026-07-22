from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from mahilda.audit.models import Atom, ParsedRule, RelationalDependency, RelationalRule, SourceRule
from mahilda.utils.relation_names import parse_relation_atom, split_conjuncts, split_implication

if TYPE_CHECKING:
    from pathlib import Path


def load_source_rules(results_dir: Path, algorithm: str) -> list[SourceRule]:
    rules: list[SourceRule] = []
    for result_path in sorted(results_dir.glob(f"{algorithm}/{algorithm}_*/*_results.json")):
        database = result_path.parent.name.removeprefix(f"{algorithm}_")
        try:
            import json

            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, list):
            continue
        for index, raw_rule in enumerate(payload):
            if not isinstance(raw_rule, dict):
                continue
            display = str(raw_rule.get("display") or _fallback_display(raw_rule))
            rules.append(
                SourceRule(
                    algorithm=algorithm,
                    database=database,
                    source_path=result_path,
                    index=index,
                    display=display,
                    payload=raw_rule,
                )
            )
    return rules


def parse_source_rule(source: SourceRule) -> ParsedRule:
    algorithm = source.algorithm.upper()
    payload_type = str(source.payload.get("type") or "")

    if payload_type == "InclusionDependency":
        return _parse_inclusion_dependency(source)
    if algorithm == "AMIE3":
        return ParsedRule(source=source, rule=None, unsupported_reason="amie3_rdf_triple_rule")
    if algorithm == "POPPER" and ":-" in source.display:
        return ParsedRule(source=source, rule=None, unsupported_reason="popper_ilp_rule")

    try:
        dependency = parse_dependency_formula(source.display)
        return ParsedRule(source=source, rule=dependency.to_horn_rule(), dependency=dependency)
    except ValueError as exc:
        return ParsedRule(source=source, rule=None, unsupported_reason=str(exc))


def parse_formula(display: str) -> RelationalRule:
    dependency = parse_dependency_formula(display)
    if len(dependency.head) != 1:
        raise ValueError("head_atom_count_not_one")
    return RelationalRule(body=dependency.body, head=dependency.head[0])


def parse_dependency_formula(display: str) -> RelationalDependency:
    body_text, head_text = split_implication(display)
    if body_text.strip().startswith("∀") and ":" in body_text:
        body_text = body_text.split(":", maxsplit=1)[1]
    if ":" in head_text and head_text.strip().startswith("∃"):
        head_text = head_text.split(":", maxsplit=1)[1]

    body_atoms = tuple(_parse_atom(atom) for atom in split_conjuncts(body_text))
    head_atoms = tuple(_parse_atom(atom) for atom in split_conjuncts(head_text))
    if not body_atoms:
        raise ValueError("missing_body_atoms")
    if not head_atoms:
        raise ValueError("missing_head_atoms")
    return RelationalDependency(body=body_atoms, head=head_atoms)


def _parse_atom(text: str) -> Atom:
    parsed = parse_relation_atom(text)
    return Atom(table=parsed.table, occurrence=parsed.occurrence, terms=tuple(sorted(parsed.terms)))


def _parse_inclusion_dependency(source: SourceRule) -> ParsedRule:
    try:
        dependent_table = str(source.payload["table_dependant"])
        dependent_columns = tuple(source.payload["columns_dependant"])
        referenced_table = str(source.payload["table_referenced"])
        referenced_columns = tuple(source.payload["columns_referenced"])
    except KeyError:
        return ParsedRule(source=source, rule=None, unsupported_reason="invalid_inclusion_dependency_payload")

    if len(dependent_columns) != 1 or len(referenced_columns) != 1:
        return ParsedRule(source=source, rule=None, unsupported_reason="multi_column_inclusion_dependency")

    variable = "x0"
    rule = RelationalRule(
        body=(Atom(dependent_table, 0, ((str(dependent_columns[0]), variable),)),),
        head=Atom(referenced_table, 0, ((str(referenced_columns[0]), variable),)),
    )
    return ParsedRule(source=source, rule=rule, dependency=rule.as_dependency())


def parse_popper_source(source: SourceRule, database_path: Path) -> ParsedRule:
    text = source.display.strip().rstrip(".")
    if ":-" not in text:
        return ParsedRule(source=source, rule=None, unsupported_reason="popper_missing_implication")
    head_text, body_text = text.split(":-", 1)
    try:
        head_literals = _split_prolog_literals(head_text)
        body_literals = _split_prolog_literals(body_text)
        if len(head_literals) != 1 or not body_literals:
            raise ValueError("popper_invalid_rule_shape")
        table_columns = _sqlite_table_columns(database_path)
        safe_tables: dict[str, str] = {}
        for table in table_columns:
            safe = _popper_safe_table(table)
            if safe in safe_tables and safe_tables[safe] != table:
                raise ValueError(f"popper_table_collision:{safe}")
            safe_tables[safe] = table
        counters: dict[str, int] = {}

        def parse_literal(literal: str) -> Atom:
            if "(" not in literal or not literal.endswith(")"):
                raise ValueError("popper_malformed_literal")
            relation, arguments_text = literal[:-1].split("(", 1)
            table = safe_tables.get(relation.strip())
            if table is None:
                raise ValueError(f"popper_unknown_relation:{relation.strip()}")
            arguments = tuple(argument.strip() for argument in arguments_text.split(","))
            columns = table_columns[table]
            if len(arguments) != len(columns):
                raise ValueError(f"popper_arity_mismatch:{table}")
            if any(not argument or not argument[0].isupper() for argument in arguments):
                raise ValueError("popper_constants_not_supported")
            occurrence = counters.get(table, 0)
            counters[table] = occurrence + 1
            return Atom(table, occurrence, tuple(zip(columns, arguments, strict=True)))

        body = tuple(parse_literal(literal) for literal in body_literals)
        head = tuple(parse_literal(literal) for literal in head_literals)
        dependency = RelationalDependency(body=body, head=head)
        return ParsedRule(source=source, rule=dependency.to_horn_rule(), dependency=dependency)
    except (sqlite3.Error, ValueError) as exc:
        return ParsedRule(source=source, rule=None, unsupported_reason=str(exc))


def _split_prolog_literals(text: str) -> tuple[str, ...]:
    literals: list[str] = []
    current: list[str] = []
    depth = 0
    for character in text:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        if character == "," and depth == 0:
            literals.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    if current:
        literals.append("".join(current).strip())
    return tuple(literal for literal in literals if literal)


def _sqlite_table_columns(database_path: Path) -> dict[str, tuple[str, ...]]:
    connection = sqlite3.connect(database_path)
    try:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {
            table: tuple(str(row[1]) for row in connection.execute(f"PRAGMA table_info({_sqlite_quote(table)})"))
            for table in tables
        }
    finally:
        connection.close()


def _popper_safe_table(table: str) -> str:
    forbidden = "'() \n.:-/,¡"
    return "".join(character for character in table.lower().replace(" ", "") if character not in forbidden)


def _sqlite_quote(identifier: str) -> str:
    return "'" + identifier.replace("'", "''") + "'"


def _fallback_display(raw_rule: dict[str, Any]) -> str:
    body = raw_rule.get("body")
    head = raw_rule.get("head")
    if body is not None or head is not None:
        return f"{body} => {head}"
    return repr(raw_rule)
