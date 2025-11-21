import os
import re
import logging
from datetime import datetime

from algorithms.rule_discovery_algorithm import RuleDiscoveryAlgorithm
from utils.rules import Predicate, TGDRule
from utils.run_cmd import run_cmd

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Amie3(RuleDiscoveryAlgorithm):
    def discover_rules(self, **kwargs) -> list:
        """
        Discover rules using the AMIE3 algorithm.

        Returns:
            list: A list of discovered TGDRule objects.
        """
        algorithm_name = "amie3"
        results_path = kwargs.get("results_dir", "results")
        script_dir = os.path.dirname(os.path.abspath(__file__))

        database_path = self.database.database_path_tsv
        current_time = datetime.now()

        jar_path = f"{script_dir}/bins/amie3/"
        output_file = os.path.join(
            results_path,
            f"{current_time.strftime('%Y-%m-%d_%H-%M-%S')}_{algorithm_name}.tsv",
        )

        cmd = (
            f"java -Xmx15G -jar {jar_path}amie-milestone-intKB.jar "
            f"-mins 0 -minc 0 -minpca 0 -minhc 0 -minis 0 {database_path} > {output_file}"
        )

        if not run_cmd(cmd, timeout=300):
            return []

        with open(output_file, "r") as file:
            raw_rules = file.read()

        rules = self.parse_horn_rules(raw_rules)

        # Clean up the output file
        os.remove(output_file) if os.path.exists(output_file) else None

        return rules

    @staticmethod
    def safe_float_conversion(value: str) -> float:
        """
        Safely convert a string to a float, replacing commas with periods.

        Args:
            value (str): The string to convert.

        Returns:
            float: The converted float.

        Raises:
            ValueError: If the string cannot be converted to float.
        """
        try:
            return float(value.replace(",", "."))
        except ValueError:
            raise ValueError(f"Cannot convert '{value}' to float.")

    def parse_horn_rules(self, rules_str: str) -> list:
        """
        Parse raw rules from AMIE3 output into TGDRule objects.

        Args:
            rules_str (str): The raw string containing rules.

        Returns:
            list: A list of TGDRule objects.
        """
        rule_pattern = re.compile(
            r"^(?P<body>.+?)\s+=>\s+(?P<head>.+?)\t(?P<confidence>[\d.]+)\t(?P<support>[\d.]+)"
        )

        rules = []
        nb_transaction = 0

        for line in rules_str.splitlines():
            if line.startswith("Loaded "):
                try:
                    nb_transaction = int(line.split()[1])
                except (IndexError, ValueError) as e:
                    logger.error(f"Error parsing transactions: {e}")
                continue

            match = rule_pattern.match(line)
            if not match:
                continue

            body_str = match.group("body")
            head_str = match.group("head")
            confidence = self.safe_float_conversion(match.group("confidence"))
            support = self.safe_float_conversion(match.group("support")) / nb_transaction

            body_predicates = self._parse_predicates(body_str)
            head_predicates = self._parse_predicates(head_str, is_head=True)

            if not body_predicates or not head_predicates:
                continue

            horn_rule = TGDRule(
                body=body_predicates,
                head=head_predicates,
                display=line,
                accuracy=-1,  # Placeholder, update as needed
                confidence=confidence
            )
            rules.append(horn_rule)

        return rules

    def _parse_predicates(self, predicate_str: str, is_head: bool = False) -> list:
        """
        Parse predicate strings into Predicate objects.

        Args:
            predicate_str (str): The predicate string.
            is_head (bool): Flag indicating if parsing head predicates.

        Returns:
            list: A list of Predicate objects.
        """
        tokens = predicate_str.split()
        if len(tokens) % 3 != 0:
            raise ValueError(
                f"Expected multiples of 3 tokens, got {len(tokens)} in '{predicate_str}'"
            )

        predicates = []
        relation_counts = {}

        for i in range(0, len(tokens), 3):
            var1, relation, var2 = tokens[i], tokens[i + 1], tokens[i + 2]

            # Update relation count for unique identification
            relation_counts[relation] = relation_counts.get(relation, 0) + 1
            relation_id = f"{relation[0]}_{relation_counts[relation]}"

            # Handle compound relations
            splitted_rel = relation.split(".")
            if len(splitted_rel) == 3:
                base_relation = splitted_rel[0].replace("_", "")
                new_relation_1 = f"{base_relation}{splitted_rel[1]}"
                new_relation_2 = f"{base_relation}{splitted_rel[2]}"

                predicates.append(
                    Predicate(
                        variable1=relation_id,
                        relation=new_relation_1,
                        variable2=var1,
                    )
                )
                predicates.append(
                    Predicate(
                        variable1=relation_id,
                        relation=new_relation_2,
                        variable2=var2,
                    )
                )
            else:
                predicates.append(
                    Predicate(
                        variable1=var1,
                        relation=relation,
                        variable2=var2,
                    )
                )

        return predicates