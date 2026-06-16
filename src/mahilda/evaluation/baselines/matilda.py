from __future__ import annotations

import importlib
import os
import sys
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Iterator

from mahilda.algorithms.base_algorithm import BaseAlgorithm
from mahilda.utils.rules import Predicate, Rule, TGDRule


class Matilda(BaseAlgorithm):
    """Adapter for the external sibling MATILDA repository."""

    def discover_rules(self, **kwargs: Any) -> list[Rule]:
        matilda_path = self._resolve_matilda_path(kwargs.get("matilda_path"))
        matilda_cls = self._load_external_matilda(matilda_path)

        settings = kwargs.get("settings")
        algo = matilda_cls(self.database, settings=settings if isinstance(settings, dict) else None)

        discover_kwargs: dict[str, Any] = {
            "results_dir": kwargs.get("results_dir"),
            "timeout": kwargs.get("timeout"),
        }
        if "traversal_algorithm" in kwargs:
            discover_kwargs["traversal_algorithm"] = kwargs["traversal_algorithm"]

        rules = []
        for raw_rule in algo.discover_rules(
            **{key: value for key, value in discover_kwargs.items() if value is not None}
        ):
            rules.append(self._convert_rule(raw_rule))
        return rules

    @staticmethod
    def _resolve_matilda_path(configured_path: Any) -> Path:
        raw_path = configured_path or os.environ.get("MAHILDA_MATILDA_PATH")
        if raw_path:
            path = Path(str(raw_path)).expanduser().resolve()
        else:
            path = Path(__file__).resolve().parents[5] / "MATILDA"

        src_path = path / "src"
        if not src_path.is_dir():
            raise RuntimeError(
                "MATILDA sibling repository not found. Set benchmark.matilda_path or "
                f"MAHILDA_MATILDA_PATH to a MATILDA repo containing src/: {path}"
            )
        return path

    @staticmethod
    def _load_external_matilda(matilda_path: Path) -> Any:
        src_path = matilda_path / "src"
        with _isolated_external_import(src_path):
            try:
                module = importlib.import_module("algorithms.matilda")
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "MATILDA baseline dependencies are not available in the active environment. "
                    f"Install the sibling repo dependencies from {matilda_path}/pyproject.toml."
                ) from exc
            return module.MATILDA

    @staticmethod
    def _convert_rule(raw_rule: Any) -> Rule:
        if isinstance(raw_rule, TGDRule):
            return raw_rule
        if all(hasattr(raw_rule, attr) for attr in ("body", "head", "display", "accuracy", "confidence")):
            return TGDRule(
                body=cast("Any", tuple(Matilda._convert_predicate(predicate) for predicate in raw_rule.body)),
                head=cast("Any", tuple(Matilda._convert_predicate(predicate) for predicate in raw_rule.head)),
                display=str(raw_rule.display),
                accuracy=float(raw_rule.accuracy),
                confidence=float(raw_rule.confidence),
                correct=getattr(raw_rule, "correct", None),
                compatible=getattr(raw_rule, "compatible", None),
            )
        return raw_rule

    @staticmethod
    def _convert_predicate(raw_predicate: Any) -> Predicate:
        return Predicate(
            variable1=str(raw_predicate.variable1),
            relation=str(raw_predicate.relation),
            variable2=str(raw_predicate.variable2),
        )


@contextmanager
def _isolated_external_import(src_path: Path) -> Iterator[None]:
    """Import MATILDA's top-level packages without leaking name collisions."""
    module_prefixes = ("algorithms", "database", "utils")
    saved_modules: dict[str, Any] = {}
    imported_module_names = [name for name in sys.modules if _is_external_module_name(name, module_prefixes)]
    for name in imported_module_names:
        saved_modules[name] = sys.modules.pop(name)

    src_path_text = str(src_path)
    sys.path.insert(0, src_path_text)
    try:
        yield
    finally:
        with suppress(ValueError):
            sys.path.remove(src_path_text)
        for name in list(sys.modules):
            if _is_external_module_name(name, module_prefixes):
                sys.modules.pop(name)
        sys.modules.update(saved_modules)


def _is_external_module_name(name: str, module_prefixes: tuple[str, ...]) -> bool:
    return name in module_prefixes or name.startswith(tuple(f"{prefix}." for prefix in module_prefixes))
