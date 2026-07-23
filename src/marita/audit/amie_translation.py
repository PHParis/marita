from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from marita.audit.models import Atom, ParsedRule, RelationalDependency, SourceRule

if TYPE_CHECKING:
    from pathlib import Path


def sanitize_identifier(identifier: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in identifier)


@dataclass(frozen=True)
class PredicateMapping:
    predicate: str
    table: str
    column: str
    primary_key: tuple[str, ...]
    object_kind: str
    referenced_table: str | None = None
    referenced_column: str | None = None


@dataclass(frozen=True)
class RdbKgMapping:
    schema_signature: str
    predicates: dict[str, PredicateMapping]
    collisions: dict[str, tuple[tuple[str, str], ...]]
    skipped_tables: tuple[str, ...]

    def to_manifest(self) -> dict[str, object]:
        return {
            "version": 1,
            "schema_signature": self.schema_signature,
            "predicates": {predicate: asdict(mapping) for predicate, mapping in sorted(self.predicates.items())},
            "collisions": {
                predicate: [list(source) for source in sources]
                for predicate, sources in sorted(self.collisions.items())
            },
            "skipped_tables": list(self.skipped_tables),
        }


@dataclass(frozen=True)
class KgTriple:
    subject: str
    predicate: str
    object: str


@lru_cache(maxsize=128)
def build_rdb_kg_mapping(database_path: Path) -> RdbKgMapping:
    connection = sqlite3.connect(database_path)
    try:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        candidates: dict[str, list[PredicateMapping]] = {}
        skipped: list[str] = []
        signature_rows: list[object] = []
        for table in tables:
            columns = connection.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
            primary_key = tuple(
                str(row[1]) for row in sorted((row for row in columns if int(row[5]) > 0), key=lambda row: int(row[5]))
            )
            foreign_keys: dict[str, list[tuple[int, int, str, str]]] = {}
            for row in connection.execute(f"PRAGMA foreign_key_list({_quote(table)})").fetchall():
                foreign_keys.setdefault(str(row[3]), []).append((int(row[0]), int(row[1]), str(row[2]), str(row[4])))
            signature_rows.append(
                {
                    "table": table,
                    "columns": [(str(row[1]), str(row[2]), int(row[5])) for row in columns],
                    "foreign_keys": sorted(item for values in foreign_keys.values() for item in values),
                }
            )
            if not primary_key or len(columns) == 1:
                skipped.append(table)
                continue
            for column_row in columns:
                column = str(column_row[1])
                targets = foreign_keys.get(column, [])
                if not targets and column in primary_key:
                    continue  # TripleConverter does not export ordinary PK values.
                object_kind = "literal"
                referenced_table: str | None = None
                referenced_column: str | None = None
                if targets:
                    object_kind = "foreign_key"
                    fk_ids = {target[0] for target in targets}
                    if len(fk_ids) == 1 and len(targets) == 1:
                        _, _, referenced_table, referenced_column = targets[0]
                    else:
                        object_kind = "unsupported_foreign_key"
                predicate = f"{sanitize_identifier(table)}.{sanitize_identifier(column)}"
                candidates.setdefault(predicate, []).append(
                    PredicateMapping(
                        predicate=predicate,
                        table=table,
                        column=column,
                        primary_key=primary_key,
                        object_kind=object_kind,
                        referenced_table=referenced_table,
                        referenced_column=referenced_column,
                    )
                )
        collisions = {
            predicate: tuple((mapping.table, mapping.column) for mapping in mappings)
            for predicate, mappings in candidates.items()
            if len(mappings) != 1
        }
        predicates = {predicate: mappings[0] for predicate, mappings in candidates.items() if len(mappings) == 1}
        signature = hashlib.sha256(
            json.dumps(signature_rows, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return RdbKgMapping(
            schema_signature=signature,
            predicates=predicates,
            collisions=collisions,
            skipped_tables=tuple(skipped),
        )
    finally:
        connection.close()


def write_mapping_manifest(
    database_path: Path,
    output_path: Path,
    *,
    tsv_path: Path | None = None,
) -> None:
    mapping = build_rdb_kg_mapping(database_path)
    manifest = mapping.to_manifest()
    manifest["database_sha256"] = _file_sha256(database_path)
    manifest["tsv_sha256"] = _file_sha256(tsv_path) if tsv_path is not None and tsv_path.exists() else None
    output_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def validate_tsv_predicates(mapping: RdbKgMapping, tsv_path: Path) -> tuple[str, ...]:
    unknown: set[str] = set()
    with tsv_path.open(encoding="utf-8") as handle:
        for line in handle:
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 3:
                unknown.add("<malformed-row>")
            elif fields[1] not in mapping.predicates:
                unknown.add(fields[1])
    return tuple(sorted(unknown))


@lru_cache(maxsize=128)
def validate_mapping_artifacts(
    database_path: Path,
    tsv_path: Path,
    manifest_path: Path,
) -> str | None:
    if not manifest_path.exists():
        return "amie_mapping_manifest_missing"
    if not tsv_path.exists():
        return "amie_tsv_missing"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "amie_mapping_manifest_invalid"
    if manifest.get("version") != 1:
        return "amie_mapping_manifest_version"
    mapping = build_rdb_kg_mapping(database_path)
    if manifest.get("schema_signature") != mapping.schema_signature:
        return "amie_mapping_schema_mismatch"
    if manifest.get("database_sha256") != _file_sha256(database_path):
        return "amie_mapping_database_hash_mismatch"
    if manifest.get("tsv_sha256") != _file_sha256(tsv_path):
        return "amie_mapping_tsv_hash_mismatch"
    unknown = validate_tsv_predicates(mapping, tsv_path)
    if unknown:
        return f"amie_mapping_unknown_predicates:{','.join(unknown[:5])}"
    return None


def translate_amie_source(source: SourceRule, database_path: Path) -> ParsedRule:
    try:
        body_triples, head_triples = parse_amie_display(source.display)
    except ValueError as exc:
        return ParsedRule(source=source, rule=None, unsupported_reason=str(exc))
    mapping = build_rdb_kg_mapping(database_path)
    try:
        dependency = translate_amie_dependency(body_triples, head_triples, mapping)
    except ValueError as exc:
        return ParsedRule(source=source, rule=None, unsupported_reason=str(exc))
    return ParsedRule(
        source=source,
        rule=dependency.to_horn_rule(),
        dependency=dependency,
    )


def parse_amie_display(display: str) -> tuple[tuple[KgTriple, ...], tuple[KgTriple, ...]]:
    rule_text = display.split("\t", 1)[0].strip()
    if "=>" not in rule_text:
        raise ValueError("amie_missing_implication")
    body_text, head_text = rule_text.split("=>", 1)
    body = _parse_triples(body_text, "body")
    head = _parse_triples(head_text, "head")
    if not body:
        raise ValueError("amie_empty_body")
    if not head:
        raise ValueError("amie_empty_head")
    return body, head


def translate_amie_dependency(
    body_triples: tuple[KgTriple, ...],
    head_triples: tuple[KgTriple, ...],
    mapping: RdbKgMapping,
) -> RelationalDependency:
    entity_terms: dict[tuple[str, str, str], str] = {}
    value_terms: dict[str, str] = {}
    term_kinds: dict[str, str] = {}
    occurrence_counters: dict[str, int] = {}

    def entity_term(term: str, table: str, column: str) -> str:
        _record_term_kind(term_kinds, term, f"entity:{table}")
        key = term, table, column
        return entity_terms.setdefault(
            key,
            f"kg_entity_{_safe_variable(term)}_{sanitize_identifier(table)}_{sanitize_identifier(column)}",
        )

    def value_term(term: str) -> str:
        _record_term_kind(term_kinds, term, "literal")
        return value_terms.setdefault(term, f"kg_value_{_safe_variable(term)}")

    def side_to_atoms(triples: tuple[KgTriple, ...]) -> tuple[Atom, ...]:
        groups: dict[tuple[str, str], dict[str, str]] = {}
        order: list[tuple[str, str]] = []
        for triple in triples:
            if not _is_variable(triple.subject) or not _is_variable(triple.object):
                raise ValueError("amie_constants_not_supported")
            if triple.predicate in mapping.collisions:
                raise ValueError(f"amie_predicate_collision:{triple.predicate}")
            predicate = mapping.predicates.get(triple.predicate)
            if predicate is None:
                raise ValueError(f"amie_unknown_predicate:{triple.predicate}")
            if predicate.object_kind == "unsupported_foreign_key":
                raise ValueError(f"amie_unsupported_composite_fk:{triple.predicate}")
            group_key = predicate.table, triple.subject
            if group_key not in groups:
                groups[group_key] = {}
                order.append(group_key)
            terms = groups[group_key]
            for primary_key_column in predicate.primary_key:
                _set_term(
                    terms,
                    primary_key_column,
                    entity_term(triple.subject, predicate.table, primary_key_column),
                )
            if predicate.object_kind == "foreign_key":
                if predicate.referenced_table is None or predicate.referenced_column is None:
                    raise ValueError(f"amie_invalid_fk_mapping:{triple.predicate}")
                object_variable = entity_term(
                    triple.object,
                    predicate.referenced_table,
                    predicate.referenced_column,
                )
            else:
                object_variable = value_term(triple.object)
            _set_term(terms, predicate.column, object_variable)

        atoms: list[Atom] = []
        for table, subject in order:
            occurrence = occurrence_counters.get(table, 0)
            occurrence_counters[table] = occurrence + 1
            atoms.append(
                Atom(
                    table=table,
                    occurrence=occurrence,
                    terms=tuple(sorted(groups[(table, subject)].items())),
                )
            )
        return tuple(atoms)

    body = side_to_atoms(body_triples)
    head = side_to_atoms(head_triples)
    return RelationalDependency(body=body, head=head)


def _parse_triples(text: str, side: str) -> tuple[KgTriple, ...]:
    tokens = text.split()
    if len(tokens) % 3 != 0:
        raise ValueError(f"amie_malformed_{side}")
    return tuple(KgTriple(*tokens[index : index + 3]) for index in range(0, len(tokens), 3))


def _record_term_kind(term_kinds: dict[str, str], term: str, kind: str) -> None:
    previous = term_kinds.setdefault(term, kind)
    if previous != kind:
        raise ValueError(f"amie_incompatible_variable_roles:{term}")


def _set_term(terms: dict[str, str], column: str, variable: str) -> None:
    previous = terms.setdefault(column, variable)
    if previous != variable:
        raise ValueError(f"amie_conflicting_column_terms:{column}")


def _is_variable(token: str) -> bool:
    return token.startswith("?") and len(token) > 1


def _safe_variable(token: str) -> str:
    return sanitize_identifier(token.removeprefix("?"))


def _quote(identifier: str) -> str:
    return "'" + identifier.replace("'", "''") + "'"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
