from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class AuditClassification(str, Enum):
    PARSE_FAILED = "parse_failed"
    APPROXIMATE = "approximate"
    VACUOUS = "vacuous"
    OUT_OF_SCOPE = "out_of_scope"
    COMPARABLE_TRUE = "comparable_true"


class MatchStatus(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    RECALLED_ALPHA = "recalled_alpha"
    RECALLED_SUBSUMED = "recalled_subsumed"
    UNMATCHED = "unmatched"


@dataclass(frozen=True, order=True)
class Atom:
    table: str
    occurrence: int
    terms: tuple[tuple[str, str], ...]

    def variables(self) -> set[str]:
        return {variable for _, variable in self.terms}

    def without_occurrence(self) -> tuple[str, tuple[tuple[str, str], ...]]:
        return self.table, self.terms


@dataclass(frozen=True)
class RelationalRule:
    body: tuple[Atom, ...]
    head: Atom

    def body_variables(self) -> set[str]:
        return set().union(*(atom.variables() for atom in self.body)) if self.body else set()

    def head_variables(self) -> set[str]:
        return self.head.variables()

    def all_atoms(self) -> tuple[Atom, ...]:
        return (*self.body, self.head)

    def canonical_key(self) -> str:
        variable_map: dict[str, str] = {}

        def normalize_var(variable: str) -> str:
            if variable not in variable_map:
                variable_map[variable] = f"v{len(variable_map)}"
            return variable_map[variable]

        def normalize_atom(atom: Atom) -> str:
            terms = ",".join(f"{column}={normalize_var(variable)}" for column, variable in sorted(atom.terms))
            return f"{atom.table}({terms})"

        body = sorted(self.body, key=lambda atom: (atom.table, atom.occurrence, atom.terms))
        body_key = " & ".join(normalize_atom(atom) for atom in body)
        head_key = normalize_atom(self.head)
        return f"{body_key} => {head_key}"

    def canonical_head_key(self) -> str:
        return RelationalRule(body=(), head=self.head).canonical_key().removeprefix(" => ")

    def canonical_body_atoms(self) -> frozenset[str]:
        keys: set[str] = set()
        for atom in self.body:
            keys.add(RelationalRule(body=(), head=atom).canonical_head_key())
        return frozenset(keys)


@dataclass(frozen=True)
class SourceRule:
    algorithm: str
    database: str
    source_path: Path
    index: int
    display: str
    payload: dict


@dataclass(frozen=True)
class ParsedRule:
    source: SourceRule
    rule: RelationalRule | None
    unsupported_reason: str | None = None


@dataclass(frozen=True)
class Evaluation:
    support: int
    predictions: int

    @property
    def confidence(self) -> float:
        if self.predictions == 0:
            return 0.0
        return self.support / self.predictions


@dataclass(frozen=True)
class AuditRecord:
    algorithm: str
    database: str
    source_path: str
    rule_index: int
    classification: AuditClassification
    match_status: MatchStatus
    reason: str
    support: int | None
    predictions: int | None
    confidence: float | None
    canonical_rule: str
    matched_rule: str
    display: str
