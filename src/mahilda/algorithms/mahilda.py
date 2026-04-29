from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Generator, Iterable, Optional, Tuple, cast

import mahilda.algorithms.mahilda_core.tgd_discovery as mahilda_core
from mahilda.algorithms.base_algorithm import BaseAlgorithm
from mahilda.algorithms.mahilda_core.tgd_discovery import dfs, init, instantiate_tgd, path_pruning
from mahilda.utils.rules import Predicate, TGDRule
from mahilda.utils.tgd_factory import TGDRuleFactory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HornRuleExtended:
    """Compatibility data structure for legacy consumers of MAHILDA."""

    body: Tuple[Predicate, ...]
    head: Predicate
    support: float
    confidence: float
    display: str

    def to_tgd_rule(self) -> TGDRule:
        return TGDRuleFactory.str_to_tgd(
            self.display,
            float(self.support),
            float(self.confidence),
        )


class MAHILDA(BaseAlgorithm):
    """Horn-focused rule discovery algorithm for tuple-generating dependencies."""

    DEFAULT_SETTINGS: Dict[str, Any] = {
        "walk_length": 4,
        "max_tables": 100,
        "max_variables": 100,
        "max_nb_occurrence_per_table_and_column": {},
        "disjoint_semantics": False,
        "split_mean_threshold": 0.0,
        "timeout": None,
        "results_dir": None,
    }

    KEY_NORMALISATION: Dict[str, str] = {
        "nb_occurrence": "walk_length",
        "max_table": "max_tables",
        "max_vars": "max_variables",
        "disjoint_semantic": "disjoint_semantics",
        "disjoint_semantics": "disjoint_semantics",
    }

    def __init__(
        self,
        database: Any,
        settings: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        self.db_inspector = database

        base_settings = dict(self.DEFAULT_SETTINGS)
        config_settings = self._extract_config_settings(config)
        runtime_overrides: Dict[str, Any] = {}
        if kwargs:
            runtime_overrides.update(kwargs)

        merged = self._apply_overrides(base_settings, config_settings)
        merged = self._apply_overrides(merged, settings)
        merged = self._apply_overrides(merged, runtime_overrides)

        self.settings = merged
        self.config = config or {}
        logger.debug("MAHILDA initialised with settings: %s", self.settings)

    @staticmethod
    def _extract_config_settings(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not config:
            return {}

        extracted: Dict[str, Any] = {}

        algorithm_config_raw = config.get("algorithm")
        if isinstance(algorithm_config_raw, dict):
            algorithm_config = cast(Dict[str, Any], algorithm_config_raw)
            parameters = algorithm_config.get("parameters")
            if isinstance(parameters, dict):
                extracted.update(cast(Dict[str, Any], parameters))
            for key, value in algorithm_config.items():
                if key != "parameters":
                    if key not in extracted:
                        extracted[key] = value

        config_parameters_raw = config.get("parameters")
        if isinstance(config_parameters_raw, dict):
            extracted.update(cast(Dict[str, Any], config_parameters_raw))

        return extracted

    @staticmethod
    def _apply_overrides(base: Dict[str, Any], overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        merged = dict(base)
        if not overrides:
            return merged

        for raw_key, value in overrides.items():
            if value is None:
                continue

            normalised_key = MAHILDA.KEY_NORMALISATION.get(raw_key, raw_key)

            if raw_key == "recursivity":
                try:
                    rec_value = int(value)
                except (TypeError, ValueError):
                    continue
                merged["recursivity"] = rec_value
                merged["walk_length"] = max(1, rec_value + 1)
                continue

            if normalised_key == "disjoint_semantics":
                merged["disjoint_semantics"] = MAHILDA._to_bool(value)
                continue

            merged[normalised_key] = value

        return merged

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _coerce_positive_int(value: Any, fallback: int) -> int:
        try:
            candidate = int(value)
            if candidate > 0:
                return candidate
        except (TypeError, ValueError):
            return fallback
        return fallback

    @staticmethod
    def _coerce_timeout(value: Any) -> Optional[int]:
        try:
            if value is None:
                return None
            candidate = int(value)
            return candidate if candidate > 0 else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_float(value: Any, fallback: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def discover_rules(self, **kwargs: Any) -> Generator[TGDRule, None, None]:  # type: ignore[override]
        runtime_kwargs = dict(kwargs)
        should_stop = cast(Optional[Callable[[], bool]], runtime_kwargs.pop("should_stop", None))
        timeout_override = runtime_kwargs.pop("timeout", None)

        runtime_settings = self._apply_overrides(self.settings, runtime_kwargs)
        if timeout_override is not None:
            runtime_settings["timeout"] = timeout_override

        time_limit = self._coerce_timeout(runtime_settings.get("timeout"))
        walk_length = self._coerce_positive_int(
            runtime_settings.get("walk_length"),
            self.DEFAULT_SETTINGS["walk_length"],
        )
        max_tables = self._coerce_positive_int(
            runtime_settings.get("max_tables"),
            self.DEFAULT_SETTINGS["max_tables"],
        )
        max_vars = self._coerce_positive_int(
            runtime_settings.get("max_variables"),
            self.DEFAULT_SETTINGS["max_variables"],
        )

        max_occurrence_map = runtime_settings.get("max_nb_occurrence_per_table_and_column", {})
        if not isinstance(max_occurrence_map, dict):
            max_occurrence_map = {}
        max_occurrence_map_typed = cast(Dict[str, Dict[str, int]], max_occurrence_map)

        disjoint_semantics = bool(runtime_settings.get("disjoint_semantics", False))
        split_mean_threshold = self._coerce_float(
            runtime_settings.get("split_mean_threshold"),
            self.DEFAULT_SETTINGS["split_mean_threshold"],
        )

        results_path = runtime_settings.get("results_dir")
        temp_results_dir: Optional[str] = None
        if not results_path:
            temp_results_dir = tempfile.mkdtemp(prefix="mahilda_results_")
            results_path = temp_results_dir
        os.makedirs(str(results_path), exist_ok=True)
        results_path_str = str(results_path)

        previous_disjoint = mahilda_core.APPLY_DISJOINT
        previous_threshold = mahilda_core.SPLIT_PRUNING_MEAN_THRESHOLD
        mahilda_core.APPLY_DISJOINT = disjoint_semantics
        mahilda_core.SPLIT_PRUNING_MEAN_THRESHOLD = split_mean_threshold

        start_time = time.time()

        try:
            if should_stop and should_stop():
                logger.debug("Early stop requested before initialisation.")
                return
            if time_limit is not None and (time.time() - start_time) > time_limit:
                logger.debug("Timeout reached before initialisation.")
                return

            cg, mapper, jia_list = init(
                self.db_inspector,
                max_nb_occurrence=walk_length,
                max_nb_occurrence_per_table_and_column=max_occurrence_map_typed,
                results_path=results_path_str,
            )

            if not jia_list:
                logger.info("No joinable indexed attributes produced; aborting discovery.")
                return

            for candidate_rule, (body, head), (support, confidence) in dfs(
                cg,
                cast(Any, None),
                path_pruning,
                self.db_inspector,
                mapper,
                max_table=max_tables,
                max_vars=max_vars,
            ):  # type: ignore[arg-type]
                if should_stop and should_stop():
                    logger.debug("Early stop requested during DFS traversal.")
                    return
                if time_limit is not None and (time.time() - start_time) > time_limit:
                    logger.info("Timeout reached during rule discovery.")
                    return
                if not candidate_rule:
                    continue

                try:
                    candidate_rule_list = cast(Any, candidate_rule)
                    split_body = cast(set[Any], body)
                    split_head = cast(set[Any], head)
                    tgd_str = instantiate_tgd(  # type: ignore[arg-type]
                        candidate_rule_list,
                        (split_body, split_head),
                        mapper,
                    )
                    rule = TGDRuleFactory.str_to_tgd(
                        tgd_str,
                        float(cast(float, support)),
                        float(cast(float, confidence)),
                    )
                    if len(rule.head) != 1:
                        continue
                    yield rule
                except Exception as exc:
                    logger.debug("Failed to instantiate rule: %s", exc, exc_info=True)
                    continue

        finally:
            mahilda_core.APPLY_DISJOINT = previous_disjoint
            mahilda_core.SPLIT_PRUNING_MEAN_THRESHOLD = previous_threshold
            if temp_results_dir and os.path.isdir(temp_results_dir):
                shutil.rmtree(temp_results_dir, ignore_errors=True)

    @staticmethod
    def get_horn_rule_statistics(rules: Iterable[TGDRule]) -> Dict[str, Any]:
        rules_list = [rule for rule in rules if len(rule.head) == 1]
        total = len(rules_list)
        if total == 0:
            return {
                "horn_rules": 0,
                "average_support": 0.0,
                "average_confidence": 0.0,
            }
        avg_support = sum(rule.accuracy for rule in rules_list) / total
        avg_confidence = sum(rule.confidence for rule in rules_list) / total
        return {
            "horn_rules": total,
            "average_support": avg_support,
            "average_confidence": avg_confidence,
        }

    @staticmethod
    def export_horn_rules(rules: Iterable[TGDRule], filepath: str) -> None:
        horn_rules = [rule for rule in rules if len(rule.head) == 1]
        if not horn_rules:
            logger.info("No Horn rules to export.")
            return
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as handle:
            for rule in horn_rules:
                handle.write(f"{rule.display}\n")
