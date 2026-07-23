import logging
import re
from datetime import datetime
from pathlib import Path

from marita.algorithms.base_algorithm import BaseAlgorithm
from marita.utils.rules import Predicate, TGDRule
from marita.utils.run_cmd import run_cmd

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Amie3(BaseAlgorithm):
    def discover_rules(self, **kwargs) -> list[TGDRule]:
        algorithm_name = "amie3"
        results_path = kwargs.get("results_dir", "results")
        script_dir = Path(__file__).resolve().parent

        input_tsv = kwargs.get("input_tsv")
        timeout = int(kwargs.get("timeout", 300))
        memory_gb = float(kwargs.get("memory_gb", 15.0))
        java_heap_gb = int(kwargs.get("java_heap_gb", max(1, int(memory_gb) - 2)))
        database_path = Path(input_tsv) if input_tsv else Path(self.database.database_path_tsv)
        current_time = datetime.now()

        jar_file = script_dir.parent / "third_party" / "amie3" / "amie-milestone-intKB.jar"
        output_file = Path(results_path) / f"{current_time.strftime('%Y-%m-%d_%H-%M-%S')}_{algorithm_name}.tsv"

        cmd = [
            "java",
            f"-Xmx{java_heap_gb}G",
            "-jar",
            str(jar_file),
            "-mins",
            "0",
            "-minc",
            "0",
            "-minpca",
            "0",
            "-minhc",
            "0",
            "-minis",
            "0",
            str(database_path),
        ]

        if not run_cmd(cmd, timeout=timeout, memory_limit_gb=memory_gb, stdout_path=output_file, logger=logger):
            raise RuntimeError("AMIE3 command failed, timed out, or exceeded memory limit.")

        with output_file.open(encoding="utf-8") as file:
            raw_rules = file.read()

        rules = self.parse_horn_rules(raw_rules)

        if output_file.exists():
            output_file.unlink()

        return rules

    @staticmethod
    def safe_float_conversion(value: str) -> float:
        try:
            return float(value.replace(",", "."))
        except ValueError as exc:
            raise ValueError(f"Cannot convert '{value}' to float.") from exc

    def parse_horn_rules(self, rules_str: str) -> list[TGDRule]:
        rule_pattern = re.compile(r"^(?P<body>.+?)\s+=>\s+(?P<head>.+?)\t(?P<confidence>[\d.]+)\t(?P<support>[\d.]+)")

        rules = []
        nb_transaction = 0

        for line in rules_str.splitlines():
            if line.startswith("Loaded "):
                try:
                    nb_transaction = int(line.split()[1])
                except (IndexError, ValueError) as e:
                    logger.error("Error parsing transactions: %s", e)
                continue

            match = rule_pattern.match(line)
            if not match:
                continue

            body_str = match.group("body")
            head_str = match.group("head")
            confidence = self.safe_float_conversion(match.group("confidence"))
            support = self.safe_float_conversion(match.group("support"))
            accuracy = support / nb_transaction if nb_transaction > 0 else 0.0

            body_predicates = self._parse_predicates(body_str)
            head_predicates = self._parse_predicates(head_str)

            if not body_predicates or not head_predicates:
                continue

            horn_rule = TGDRule(
                body=body_predicates,
                head=head_predicates,
                display=line,
                accuracy=accuracy,
                confidence=confidence,
            )
            rules.append(horn_rule)

        return rules

    def _parse_predicates(self, predicate_str: str) -> list[Predicate]:
        tokens = predicate_str.split()
        if len(tokens) % 3 != 0:
            raise ValueError(f"Expected multiples of 3 tokens, got {len(tokens)} in '{predicate_str}'")

        predicates = []
        relation_counts: dict[str, int] = {}

        for i in range(0, len(tokens), 3):
            var1, relation, var2 = tokens[i], tokens[i + 1], tokens[i + 2]
            relation_counts[relation] = relation_counts.get(relation, 0) + 1
            relation_id = f"{relation[0]}_{relation_counts[relation]}"

            splitted_rel = relation.split(".")
            if len(splitted_rel) == 3:
                base_relation = splitted_rel[0].replace("_", "")
                new_relation_1 = f"{base_relation}{splitted_rel[1]}"
                new_relation_2 = f"{base_relation}{splitted_rel[2]}"

                predicates.append(Predicate(variable1=relation_id, relation=new_relation_1, variable2=var1))
                predicates.append(Predicate(variable1=relation_id, relation=new_relation_2, variable2=var2))
            else:
                predicates.append(Predicate(variable1=var1, relation=relation, variable2=var2))

        return predicates
