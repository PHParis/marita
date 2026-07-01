from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from mahilda.audit.models import Atom, ParsedRule, RelationalRule, SourceRule

if TYPE_CHECKING:
    from pathlib import Path

FORMULA_SPLIT_RE = re.compile(r"\s*(?:⇒|=>)\s*")
ATOM_RE = re.compile(r"(?P<relation>[A-Za-z_][A-Za-z0-9_]*)\((?P<args>[^()]*)\)")
TRAILING_OCCURRENCE_RE = re.compile(r"^(?P<table>.+)_(?P<occurrence>\d+)$")


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
        return ParsedRule(source=source, rule=parse_formula(source.display))
    except ValueError as exc:
        return ParsedRule(source=source, rule=None, unsupported_reason=str(exc))


def parse_formula(display: str) -> RelationalRule:
    parts = FORMULA_SPLIT_RE.split(display, maxsplit=1)
    if len(parts) != 2:
        raise ValueError("missing_implication")
    body_text, head_text = parts
    if ":" in body_text:
        body_text = body_text.split(":", maxsplit=1)[1]
    if ":" in head_text and head_text.strip().startswith("∃"):
        head_text = head_text.split(":", maxsplit=1)[1]

    body_atoms = tuple(_parse_atom(match) for match in ATOM_RE.finditer(body_text))
    head_atoms = tuple(_parse_atom(match) for match in ATOM_RE.finditer(head_text))
    if not body_atoms:
        raise ValueError("missing_body_atoms")
    if len(head_atoms) != 1:
        raise ValueError("head_atom_count_not_one")
    return RelationalRule(body=body_atoms, head=head_atoms[0])


def _parse_atom(match: re.Match[str]) -> Atom:
    relation = match.group("relation")
    occurrence_match = TRAILING_OCCURRENCE_RE.match(relation)
    if occurrence_match:
        table = occurrence_match.group("table")
        occurrence = int(occurrence_match.group("occurrence"))
    else:
        table = relation
        occurrence = 0

    terms: list[tuple[str, str]] = []
    for raw_assignment in match.group("args").split(","):
        assignment = raw_assignment.strip()
        if not assignment:
            continue
        if "=" not in assignment:
            raise ValueError("atom_argument_without_column_assignment")
        column, variable = assignment.split("=", maxsplit=1)
        column = column.strip()
        variable = variable.strip()
        if not column or not variable:
            raise ValueError("empty_atom_assignment")
        terms.append((column, variable))
    if not terms:
        raise ValueError("atom_without_terms")
    return Atom(table=table, occurrence=occurrence, terms=tuple(sorted(terms)))


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
    return ParsedRule(source=source, rule=rule)


def _fallback_display(raw_rule: dict[str, Any]) -> str:
    body = raw_rule.get("body")
    head = raw_rule.get("head")
    if body is not None or head is not None:
        return f"{body} => {head}"
    return repr(raw_rule)
