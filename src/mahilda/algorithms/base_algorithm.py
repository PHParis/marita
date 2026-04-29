from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TypedDict

    from mahilda.database.alchemy_utility import AlchemyUtility
    from mahilda.utils.rules import Rule

    class CommonDiscoveryKwargs(TypedDict, total=False):
        """Kwargs recognised by all BaseAlgorithm subclasses.

        Subclasses may accept additional kwargs beyond these.
        """

        results_dir: str | Path  # directory where intermediate/output files are written
        timeout: int  # hard wall-clock limit in seconds; enforced externally by ResourceMonitor
        input_tsv: str | Path  # pre-built TSV input (AMIE3 only; skips DB export when provided)


class BaseAlgorithm(ABC):
    def __init__(self, database: AlchemyUtility):
        self.database = database

    @abstractmethod
    def discover_rules(self, **kwargs) -> list[Rule]:
        """Discover rules from the database.

        Common kwargs are described in CommonDiscoveryKwargs. Subclasses may
        require or accept additional keyword arguments.
        """
        raise NotImplementedError
