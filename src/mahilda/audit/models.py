from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from mahilda.utils.relation_names import canonical_relation_reference

if TYPE_CHECKING:
    from pathlib import Path


class AuditClassification(str, Enum):
    PARSE_FAILED = "parse_failed"
    APPROXIMATE = "approximate"
    VACUOUS = "vacuous"
    OUT_OF_SCOPE = "out_of_scope"
    COMPARABLE_TRUE = "comparable_true"


class PaperCategory(str, Enum):
    TECHNICAL_EXCLUSION = "technical_exclusion"
    VACUOUS = "vacuous"
    NON_EXACT = "non_exact"
    EXACT_NON_HORN_TGD = "exact_non_horn_tgd"
    OTHER_OUT_OF_SCOPE = "other_out_of_scope"
    COMPARABLE_EXACT = "comparable_exact"


class RuleKind(str, Enum):
    UNKNOWN = "unknown"
    HORN = "horn"
    EXISTENTIAL_TGD = "existential_tgd"
    MULTI_HEAD_TGD = "multi_head_tgd"
    EXISTENTIAL_MULTI_HEAD_TGD = "existential_multi_head_tgd"


class MatchStatus(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    RECALLED_ALPHA = "recalled_alpha"
    RECALLED_SUBSUMED = "recalled_subsumed"
    COVERED_ON_INSTANCE = "covered_on_instance"
    UNMATCHED = "unmatched"


class ScopeStatus(str, Enum):
    NOT_PARSED = "not_parsed"
    UNSUPPORTED_REPRESENTATION = "unsupported_representation"
    IN_TARGET_CLASS = "in_target_class"
    EMPTY_BODY = "empty_body"
    HEAD_ONLY_VARIABLE = "head_only_variable"
    NON_HORN_TGD = "non_horn_tgd"
    OUTSIDE_BOUNDS = "outside_bounds"
    OUTSIDE_CONNECTIVITY = "outside_connectivity"
    OUTSIDE_FK_JOINABILITY = "outside_fk_joinability"
    OUTSIDE_RELATION_DISJOINTNESS = "outside_relation_disjointness"
    MISSING_DATABASE = "missing_database"
    MISSING_PK = "missing_pk"
    EVALUATOR_ERROR = "evaluator_error"


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
            return f"{canonical_relation_reference(atom.table)}({terms})"

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

    def as_dependency(self) -> RelationalDependency:
        return RelationalDependency(body=self.body, head=(self.head,))


@dataclass(frozen=True)
class RelationalDependency:
    """A relational dependency, including existential and multi-head TGDs."""

    body: tuple[Atom, ...]
    head: tuple[Atom, ...]

    def body_variables(self) -> set[str]:
        return set().union(*(atom.variables() for atom in self.body)) if self.body else set()

    def head_variables(self) -> set[str]:
        return set().union(*(atom.variables() for atom in self.head)) if self.head else set()

    def existential_variables(self) -> set[str]:
        return self.head_variables() - self.body_variables()

    def all_atoms(self) -> tuple[Atom, ...]:
        return (*self.body, *self.head)

    def rule_kind(self) -> RuleKind:
        existential = bool(self.existential_variables())
        multi_head = len(self.head) != 1
        if existential and multi_head:
            return RuleKind.EXISTENTIAL_MULTI_HEAD_TGD
        if existential:
            return RuleKind.EXISTENTIAL_TGD
        if multi_head:
            return RuleKind.MULTI_HEAD_TGD
        return RuleKind.HORN

    def to_horn_rule(self) -> RelationalRule | None:
        if self.rule_kind() != RuleKind.HORN or not self.head:
            return None
        return RelationalRule(body=self.body, head=self.head[0])

    def canonical_key(self) -> str:
        variable_map: dict[str, str] = {}

        def normalize_var(variable: str) -> str:
            if variable not in variable_map:
                variable_map[variable] = f"v{len(variable_map)}"
            return variable_map[variable]

        def normalize_atom(atom: Atom) -> str:
            terms = ",".join(f"{column}={normalize_var(variable)}" for column, variable in sorted(atom.terms))
            return f"{canonical_relation_reference(atom.table)}({terms})"

        body = sorted(self.body, key=lambda atom: (atom.table, atom.occurrence, atom.terms))
        head = sorted(self.head, key=lambda atom: (atom.table, atom.occurrence, atom.terms))
        body_key = " & ".join(normalize_atom(atom) for atom in body)
        head_key = " & ".join(normalize_atom(atom) for atom in head)
        return f"{body_key} => {head_key}"


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
    dependency: RelationalDependency | None = None

    def relational_dependency(self) -> RelationalDependency | None:
        if self.dependency is not None:
            return self.dependency
        return self.rule.as_dependency() if self.rule is not None else None


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
    scope_status: ScopeStatus
    scope_reason: str
    target_class_member: bool
    coverage_alpha: bool
    coverage_subsumption: bool
    coverage_instance: bool
    diagnosis: str
    claim_relevant: bool
    reason: str
    support: int | None
    predictions: int | None
    confidence: float | None
    canonical_rule: str
    matched_rule: str
    display: str
    paper_category: PaperCategory = PaperCategory.TECHNICAL_EXCLUSION
    rule_kind: RuleKind = RuleKind.UNKNOWN
    native_parseable: bool = False
    relationally_translatable: bool = False
    evaluable: bool = False
    structurally_eligible: bool = False
    scope_reasons: str = ""
    vacuity_reasons: str = ""
    ordinary_support: int | None = None
    ordinary_predictions: int | None = None
    disjoint_support: int | None = None
    disjoint_predictions: int | None = None
    support_reduced: bool = False
    canonical_dependency: str = ""
