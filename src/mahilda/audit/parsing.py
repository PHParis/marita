from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mahilda.audit.models import Atom, ParsedRule, RelationalRule, SourceRule
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
        return ParsedRule(source=source, rule=parse_formula(source.display))
    except ValueError as exc:
        return ParsedRule(source=source, rule=None, unsupported_reason=str(exc))


def parse_formula(display: str) -> RelationalRule:
    body_text, head_text = split_implication(display)
    if body_text.strip().startswith("∀") and ":" in body_text:
        body_text = body_text.split(":", maxsplit=1)[1]
    if ":" in head_text and head_text.strip().startswith("∃"):
        head_text = head_text.split(":", maxsplit=1)[1]

    body_atoms = tuple(_parse_atom(atom) for atom in split_conjuncts(body_text))
    head_atoms = tuple(_parse_atom(atom) for atom in split_conjuncts(head_text))
    if not body_atoms:
        raise ValueError("missing_body_atoms")
    if len(head_atoms) != 1:
        raise ValueError("head_atom_count_not_one")
    return RelationalRule(body=body_atoms, head=head_atoms[0])


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
    return ParsedRule(source=source, rule=rule)


def _fallback_display(raw_rule: dict[str, Any]) -> str:
    body = raw_rule.get("body")
    head = raw_rule.get("head")
    if body is not None or head is not None:
        return f"{body} => {head}"
    return repr(raw_rule)
