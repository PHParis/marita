from __future__ import annotations

import re
from dataclasses import dataclass

SAFE_RELATION_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
OCCURRENCE_SUFFIX_RE = re.compile(r"_(?P<occurrence>\d+)\Z")


@dataclass(frozen=True)
class RelationAtom:
    table: str
    occurrence: int
    has_occurrence: bool
    terms: tuple[tuple[str, str], ...]


def format_relation_reference(table: str, occurrence: int) -> str:
    """Format a table occurrence while preserving simple legacy output."""
    if occurrence < 0:
        raise ValueError("Relation occurrence must be non-negative.")
    if SAFE_RELATION_NAME_RE.fullmatch(table) and not OCCURRENCE_SUFFIX_RE.search(table):
        return f"{table}_{occurrence}"
    escaped = table.replace('"', '""')
    return f'"{escaped}"_{occurrence}'


def parse_relation_reference(reference: str) -> tuple[str, int, bool]:
    """Parse a legacy or quoted table occurrence reference."""
    text = reference.strip()
    if not text:
        raise ValueError("Empty relation name")

    if text.startswith('"'):
        table, suffix = _parse_quoted_table(text)
        if not suffix:
            return table, 0, False
        match = re.fullmatch(r"_(\d+)", suffix)
        if match is None:
            raise ValueError(f"Invalid quoted relation occurrence: {reference}")
        return table, int(match.group(1)), True

    occurrence_match = OCCURRENCE_SUFFIX_RE.search(text)
    if occurrence_match is None:
        return text, 0, False
    table = text[: occurrence_match.start()]
    if not table:
        raise ValueError(f"Invalid relation occurrence: {reference}")
    return table, int(occurrence_match.group("occurrence")), True


def internal_relation_name(table: str, occurrence: int, has_occurrence: bool = True) -> str:
    """Return the unquoted relation identity used by ``Predicate`` objects."""
    return f"{table}_{occurrence}" if has_occurrence else table


def parse_relation_atom(text: str) -> RelationAtom:
    relation_text, arguments = split_relation_atom(text)
    table, occurrence, has_occurrence = parse_relation_reference(relation_text)
    terms: list[tuple[str, str]] = []
    for raw_assignment in arguments.split(","):
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
    return RelationAtom(table, occurrence, has_occurrence, tuple(terms))


def split_relation_atom(text: str) -> tuple[str, str]:
    """Split ``relation(arguments)`` without assuming relation-name syntax."""
    stripped = text.strip()
    opening = _find_unquoted_opening_parenthesis(stripped)
    if opening <= 0 or not stripped.endswith(")"):
        raise ValueError(f"Invalid relation atom: {text}")
    relation = stripped[:opening].strip()
    arguments = stripped[opening + 1 : -1]
    if _parenthesis_depth(arguments) != 0:
        raise ValueError(f"Invalid relation atom: {text}")
    return relation, arguments


def split_conjuncts(text: str) -> list[str]:
    """Split conjunctions while ignoring conjunctions inside quoted names."""
    parts: list[str] = []
    start = 0
    quoted = False
    index = 0
    while index < len(text):
        character = text[index]
        if character == '"':
            if quoted and index + 1 < len(text) and text[index + 1] == '"':
                index += 2
                continue
            quoted = not quoted
        elif character == "∧" and not quoted:
            parts.append(text[start:index].strip())
            start = index + 1
        index += 1
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def split_implication(text: str) -> tuple[str, str]:
    """Split the first implication outside a quoted relation name."""
    quoted = False
    index = 0
    while index < len(text):
        character = text[index]
        if character == '"':
            if quoted and index + 1 < len(text) and text[index + 1] == '"':
                index += 2
                continue
            quoted = not quoted
        elif not quoted:
            if text.startswith("⇒", index):
                return text[:index], text[index + 1 :]
            if text.startswith("=>", index):
                return text[:index], text[index + 2 :]
        index += 1
    raise ValueError("missing_implication")


def canonical_relation_reference(reference: str) -> str:
    """Canonicalize a relation reference without inventing an occurrence."""
    if SAFE_RELATION_NAME_RE.fullmatch(reference) and not OCCURRENCE_SUFFIX_RE.search(reference):
        return reference
    return f'"{reference.replace(chr(34), chr(34) * 2)}"'


def _parse_quoted_table(text: str) -> tuple[str, str]:
    table_characters: list[str] = []
    index = 1
    while index < len(text):
        character = text[index]
        if character == '"':
            if index + 1 < len(text) and text[index + 1] == '"':
                table_characters.append('"')
                index += 2
                continue
            return "".join(table_characters), text[index + 1 :]
        table_characters.append(character)
        index += 1
    raise ValueError(f"Unterminated quoted relation name: {text}")


def _find_unquoted_opening_parenthesis(text: str) -> int:
    quoted = False
    index = 0
    while index < len(text):
        character = text[index]
        if character == '"':
            if quoted and index + 1 < len(text) and text[index + 1] == '"':
                index += 2
                continue
            quoted = not quoted
        elif character == "(" and not quoted:
            return index
        index += 1
    return -1


def _parenthesis_depth(text: str) -> int:
    depth = 0
    quoted = False
    index = 0
    while index < len(text):
        character = text[index]
        if character == '"':
            if quoted and index + 1 < len(text) and text[index + 1] == '"':
                index += 2
                continue
            quoted = not quoted
        elif not quoted and character == "(":
            depth += 1
        elif not quoted and character == ")":
            depth -= 1
            if depth < 0:
                return depth
        index += 1
    return depth
