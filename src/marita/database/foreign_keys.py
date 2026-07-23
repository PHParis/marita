from __future__ import annotations

from typing import TypeAlias, cast

ForeignKeyTarget: TypeAlias = tuple[str, str]
ForeignKeyTargets: TypeAlias = tuple[ForeignKeyTarget, ...]
ForeignKeyMap: TypeAlias = dict[str, dict[str, ForeignKeyTargets]]


def normalise_foreign_key_targets(value: object) -> ForeignKeyTargets:
    """Return a deterministic tuple of referenced table/column pairs.

    The legacy API represented one target as ``(table, column)``. Accept that
    shape while migrating callers to the lossless multi-target representation.
    """
    target = _coerce_target(value)
    if target is not None:
        return (target,)
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()

    targets: set[ForeignKeyTarget] = set()
    for target in value:
        target_pair = _coerce_target(target)
        if target_pair is not None:
            targets.add(target_pair)
    return tuple(sorted(targets))


def _coerce_target(value: object) -> ForeignKeyTarget | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if not isinstance(value[0], str) or not isinstance(value[1], str):
        return None
    return cast("ForeignKeyTarget", (value[0], value[1]))
