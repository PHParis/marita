from abc import ABC, abstractmethod

from utils.rules import Rule
from database.alchemy_utility import AlchemyUtility
class BaseAlgorithm(ABC):
    def __init__(self, database: AlchemyUtility):
        """
        Initialize the algorithm with a database.
        """
        self.database = database
    @abstractmethod
    def discover_rules(self, **kwargs) -> Rule:
        """
        Discovers rules from the provided tables and columns using given parameters.

        Args:
        - **kwargs: Additional parameters required by specific algorithms.

        Returns:
        - List of discovered rules.
        """
        pass
