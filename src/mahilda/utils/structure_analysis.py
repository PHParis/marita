"""Utility helpers to analyse and report on discovered rule structures.

These helpers were extracted from the batch processor so that the logic can be
re-used by experiments and ablations without duplicating code.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any, Dict, Iterable

import logging


def _safe_len(value: Any) -> int:
    try:
        return len(value)  # type: ignore[arg-type]
    except Exception:
        return 0


def analyze_rule_structure(rules: Iterable[Any]) -> Dict[str, Any]:
    """Compute structural statistics for a collection of discovered rules."""
    rules_list = list(rules)

    analysis: Dict[str, Any] = {
        "total_rules": len(rules_list),
        "pattern_analysis": {
            "pattern_1_1": 0,
            "pattern_1_2": 0,
            "pattern_2_1": 0,
            "pattern_2_2": 0,
            "pattern_2_plus": 0,
            "other_patterns": 0,
        },
        "rule_types": {},
        "accuracy_stats": {
            "perfect_accuracy": 0,
            "high_accuracy": 0,
            "medium_accuracy": 0,
            "low_accuracy": 0,
        },
        "confidence_stats": {
            "perfect_confidence": 0,
            "high_confidence": 0,
            "medium_confidence": 0,
            "low_confidence": 0,
        },
        "existential_quantifiers": 0,
        "universal_quantifiers": 0,
        "pattern_2_2_rules": [],
        "avg_accuracy_all_rules": 0.0,
        "avg_confidence_all_rules": 0.0,
    }

    if not rules_list:
        return analysis

    total_accuracy = 0.0
    total_confidence = 0.0

    for rule in rules_list:
        body = getattr(rule, "body", [])
        head = getattr(rule, "head", [])
        body_count = _safe_len(body)
        head_count = _safe_len(head)
        display = getattr(rule, "display", str(rule))
        rule_type = getattr(rule, "type", "Unknown")

        if body_count == 1 and head_count == 1:
            analysis["pattern_analysis"]["pattern_1_1"] += 1
        elif body_count == 1 and head_count == 2:
            analysis["pattern_analysis"]["pattern_1_2"] += 1
        elif body_count == 2 and head_count == 1:
            analysis["pattern_analysis"]["pattern_2_1"] += 1
        elif body_count == 2 and head_count == 2:
            analysis["pattern_analysis"]["pattern_2_2"] += 1
            analysis["pattern_2_2_rules"].append(
                {
                    "body": body,
                    "head": head,
                    "display": display,
                    "accuracy": float(getattr(rule, "accuracy", 0.0) or 0.0),
                    "confidence": float(getattr(rule, "confidence", 0.0) or 0.0),
                    "support": float(getattr(rule, "support", 0.0) or 0.0),
                    "type": rule_type,
                }
            )
        elif body_count >= 2 and head_count >= 2:
            analysis["pattern_analysis"]["pattern_2_plus"] += 1
        else:
            analysis["pattern_analysis"]["other_patterns"] += 1

        accuracy = float(getattr(rule, "accuracy", 0.0) or 0.0)
        confidence = float(getattr(rule, "confidence", 0.0) or 0.0)
        total_accuracy += accuracy
        total_confidence += confidence

        analysis["rule_types"][rule_type] = analysis["rule_types"].get(rule_type, 0) + 1

        if accuracy == 1.0:
            analysis["accuracy_stats"]["perfect_accuracy"] += 1
        elif accuracy >= 0.9:
            analysis["accuracy_stats"]["high_accuracy"] += 1
        elif accuracy >= 0.7:
            analysis["accuracy_stats"]["medium_accuracy"] += 1
        else:
            analysis["accuracy_stats"]["low_accuracy"] += 1

        if confidence == 1.0:
            analysis["confidence_stats"]["perfect_confidence"] += 1
        elif confidence >= 0.9:
            analysis["confidence_stats"]["high_confidence"] += 1
        elif confidence >= 0.7:
            analysis["confidence_stats"]["medium_confidence"] += 1
        else:
            analysis["confidence_stats"]["low_confidence"] += 1

        if display:
            quantifiers = _count_quantifiers(display)
            analysis["existential_quantifiers"] += quantifiers["existential"]
            analysis["universal_quantifiers"] += quantifiers["universal"]

    total_rules = analysis["total_rules"] or 1
    analysis["avg_accuracy_all_rules"] = total_accuracy / total_rules
    analysis["avg_confidence_all_rules"] = total_confidence / total_rules

    return analysis


def _count_quantifiers(rule_display: str) -> Dict[str, int]:
    existential = rule_display.count("∃")
    universal = rule_display.count("∀")
    return {"existential": existential, "universal": universal}


def log_structure_analysis(
    logger: logging.Logger,
    analysis: Dict[str, Any],
    database_name: str,
) -> None:
    """Pretty-print structure statistics to the provided logger."""
    total_rules = analysis.get("total_rules", 0) or 0
    logger.info("=== RULE STRUCTURE ANALYSIS for %s ===", database_name)
    logger.info("Total rules analyzed: %d", total_rules)

    if not total_rules:
        logger.info("No rules discovered; skipping detailed analysis.")
        return

    patterns = analysis.get("pattern_analysis", {})
    for label in [
        ("1 body → 1 head", "pattern_1_1"),
        ("1 body → 2 head", "pattern_1_2"),
        ("2 body → 1 head", "pattern_2_1"),
        ("2 body → 2 head", "pattern_2_2"),
        ("2+ body → 2+ head", "pattern_2_plus"),
        ("Other patterns", "other_patterns"),
    ]:
        value = patterns.get(label[1], 0)
        logger.info("  • %s: %d (%.1f%%)", label[0], value, (value / total_rules) * 100)

    acc = analysis.get("accuracy_stats", {})
    conf = analysis.get("confidence_stats", {})
    logger.info("Quality Distribution:")
    logger.info(
        "  • Perfect accuracy (1.0): %d (%.1f%%)",
        acc.get("perfect_accuracy", 0),
        (acc.get("perfect_accuracy", 0) / total_rules) * 100,
    )
    logger.info(
        "  • High accuracy (≥0.9): %d (%.1f%%)",
        acc.get("high_accuracy", 0),
        (acc.get("high_accuracy", 0) / total_rules) * 100,
    )
    logger.info(
        "  • Perfect confidence (1.0): %d (%.1f%%)",
        conf.get("perfect_confidence", 0),
        (conf.get("perfect_confidence", 0) / total_rules) * 100,
    )
    logger.info(
        "  • High confidence (≥0.9): %d (%.1f%%)",
        conf.get("high_confidence", 0),
        (conf.get("high_confidence", 0) / total_rules) * 100,
    )

    logger.info("Quantifier Distribution:")
    logger.info(
        "  • Universal (∀): %d", analysis.get("universal_quantifiers", 0)
    )
    logger.info(
        "  • Existential (∃): %d", analysis.get("existential_quantifiers", 0)
    )

    pattern_2_2_count = patterns.get("pattern_2_2", 0)
    if pattern_2_2_count:
        logger.info("🎯 %d rules follow the target 2 body → 2 head pattern", pattern_2_2_count)
        top_examples = sorted(
            analysis.get("pattern_2_2_rules", []),
            key=lambda r: (r.get("accuracy", 0.0), r.get("confidence", 0.0)),
            reverse=True,
        )[:3]
        if top_examples:
            logger.info("Top examples:")
            for idx, rule in enumerate(top_examples, 1):
                display = str(rule.get("display", ""))
                if len(display) > 120:
                    display = f"{display[:117]}..."
                logger.info(
                    "  %d. %s | acc=%.3f conf=%.3f",
                    idx,
                    display,
                    rule.get("accuracy", 0.0),
                    rule.get("confidence", 0.0),
                )


def save_pattern_2_2_rules(
    analysis: Dict[str, Any],
    output_dir: Path,
    db_stem: str,
    logger: logging.Logger | None = None,
) -> Path | None:
    """Persist the list of 2→2 pattern rules if any were found."""
    pattern_rules = analysis.get("pattern_2_2_rules", [])
    if not pattern_rules:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"pattern_2_2_rules_{db_stem}.json"
    with file_path.open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "database": db_stem,
                "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "count": len(pattern_rules),
                "pattern_2_2_rules": pattern_rules,
            },
            fh,
            indent=2,
            ensure_ascii=False,
        )
    if logger:
        logger.info("Saved %d pattern 2→2 rules to %s", len(pattern_rules), file_path)
    return file_path
