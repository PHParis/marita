import json
import logging
from dataclasses import asdict

from mahilda.utils.rules import (
    DenialConstraint,
    FunctionalDependency,
    HornRule,
    InclusionDependency,
    PredicateUtils,
    Rule,
    TGDRule,
)


class RuleIO:
    @staticmethod
    def rule_to_dict(rule: Rule) -> dict:
        if isinstance(rule, InclusionDependency):
            return {"type": "InclusionDependency", **asdict(rule)}
        elif isinstance(rule, FunctionalDependency):
            return {"type": "FunctionalDependency", **asdict(rule)}
        elif isinstance(rule, DenialConstraint):
            return {
                "type": "DenialConstraint",
                "table": rule.table,
                "conditions": [str(cond) for cond in rule.conditions],
                "correct": rule.correct,
                "compatible": rule.compatible,
            }
        elif isinstance(rule, HornRule):
            return {
                "type": "HornRule",
                "body": [str(pred) for pred in rule.body],
                "head": str(rule.head),
                "display": rule.display,
                "correct": rule.correct,
                "compatible": rule.compatible,
            }
        elif isinstance(rule, TGDRule):
            return {
                "type": "TGDRule",
                "body": [str(pred) for pred in rule.body],
                "head": [str(pred) for pred in rule.head],
                "display": rule.display,
                "accuracy": rule.accuracy,
                "confidence": rule.confidence,
                "correct": rule.correct,
                "compatible": rule.compatible,
            }
        else:
            raise ValueError("Unknown rule type")

    @staticmethod
    def rule_from_dict(d: dict) -> Rule:
        rule_type = d.get("type", "TGDRule")

        try:
            if "table_dependant" in d or "columns_dependant" in d or "table_referenced" in d:
                return InclusionDependency(
                    table_dependant=d["table_dependant"],
                    columns_dependant=tuple(d["columns_dependant"]),
                    table_referenced=d["table_referenced"],
                    columns_referenced=tuple(d["columns_referenced"]),
                    display=d.get("display"),
                    correct=d.get("correct"),
                    compatible=d.get("compatible"),
                )
            elif rule_type == "FunctionalDependency":
                rule_data = d.copy()
                rule_data.pop("type", None)
                return FunctionalDependency(**rule_data)
            elif rule_type == "DenialConstraint":
                raise NotImplementedError("DenialConstraint reconstruction not fully implemented.")
            elif rule_type == "HornRule":
                if "body" not in d or "head" not in d:
                    raise ValueError("Missing 'body' or 'head' in HornRule.")
                body = tuple(PredicateUtils.str_to_predicate(pred) for pred in d["body"])
                head = PredicateUtils.str_to_predicate(d["head"])
                return HornRule(
                    body=body,
                    head=head,
                    display=d.get("display"),
                    correct=d.get("correct"),
                    compatible=d.get("compatible"),
                )
            elif rule_type == "TGDRule":
                if "body" not in d or "head" not in d:
                    raise ValueError("Missing 'body' or 'head' in TGDRule.")
                body = tuple(PredicateUtils.str_to_predicate(pred) for pred in d["body"])
                head = tuple(PredicateUtils.str_to_predicate(pred) for pred in d["head"])
                return TGDRule(
                    body=body,
                    head=head,
                    display=d.get("display"),
                    accuracy=d.get("accuracy", 0.0),
                    confidence=d.get("confidence", 0.0),
                    correct=d.get("correct"),
                    compatible=d.get("compatible"),
                )
            else:
                raise ValueError(f"Unknown rule type: {rule_type}")
        except Exception as e:
            logging.error(f"Error converting rule from dict: {e}. Rule data: {d}")
            raise

    @staticmethod
    def save_rules_to_json(rules: list[Rule], filepath: str) -> int:
        rules_generated = [RuleIO.rule_to_dict(rule) for rule in rules]
        with open(filepath, "w") as f:
            json.dump(rules_generated, f, indent=4)
        return len(rules_generated)

    @staticmethod
    def save_yielded_rule_to_json(rule: Rule, filepath: str) -> None:
        try:
            with open(filepath) as f:
                data = json.load(f)
        except FileNotFoundError:
            data = []

        data.append(RuleIO.rule_to_dict(rule))

        with open(filepath, "w") as f:
            json.dump(data, f, indent=4)

    @staticmethod
    def load_rules_from_json(filepath: str) -> list[Rule]:
        with open(filepath) as f:
            return [RuleIO.rule_from_dict(d) for d in json.load(f)]
