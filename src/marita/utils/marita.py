from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

from marita.utils.rules import MARITARule  # noqa: TC001, UP035


def best_per_head(rules: Iterable[MARITARule]) -> list[MARITARule]:
    """Explicitly rank MARITA rules by normalized head relation.

    This is presentation/post-processing only; discovery never calls it.
    Every rule tied for the maximum support is retained.
    """
    grouped: dict[str, list[MARITARule]] = defaultdict(list)
    for rule in rules:
        grouped[rule.head[0].relation].append(rule)
    selected: list[MARITARule] = []
    for group in grouped.values():
        maximum = max(rule.support for rule in group)
        selected.extend(rule for rule in group if rule.support == maximum)
    return selected
