import ast
import logging
import os
from datetime import datetime
from pathlib import Path

from mahilda.algorithms.base_algorithm import BaseAlgorithm
from mahilda.utils.rules import InclusionDependency, Rule
from mahilda.utils.run_cmd import run_cmd

logger = logging.getLogger(__name__)


class Spider(BaseAlgorithm):
    def discover_rules(self, **kwargs) -> Rule:
        rules = {}
        script_dir = Path(__file__).resolve().parent
        results_path = str(kwargs.get("results_dir", "results"))
        os.makedirs(results_path, exist_ok=True)

        algorithm_name = "SPIDER"
        class_path = "de.metanome.algorithms.spider.SPIDERFile"
        rule_type = "inds"
        csv_files = [
            os.path.join(self.database.base_csv_dir, str(table)) for table in os.listdir(self.database.base_csv_dir)
        ]
        current_time = datetime.now()
        jar_path = script_dir.parent / "third_party" / "metanome"
        file_name = f"{current_time.strftime('%Y-%m-%d_%H-%M-%S')}_{algorithm_name}"
        output_prefix = os.path.join(results_path, file_name)

        cmd = [
            "java",
            "-cp",
            f"{jar_path}/metanome-cli-1.2-SNAPSHOT.jar:{jar_path}/{algorithm_name}-1.2-SNAPSHOT.jar",
            "de.metanome.cli.App",
            "--algorithm",
            class_path,
            "--files",
            *csv_files,
            "--table-key",
            "INPUT_FILES",
            "--separator",
            ",",
            "--output",
            f"file:{output_prefix}",
            "--header",
        ]
        if not run_cmd(cmd, logger=logger):
            return rules

        result_file_path = f"{output_prefix}_{rule_type}"
        try:
            with open(result_file_path) as f:
                raw_rules = [line for line in f if line.strip()]
        except FileNotFoundError:
            return rules

        if os.path.exists(result_file_path):
            os.remove(result_file_path)

        for raw_rule in raw_rules:
            try:
                raw_rule = ast.literal_eval(raw_rule)
            except (ValueError, SyntaxError):
                continue

            try:
                table_dependant = raw_rule["dependant"]["columnIdentifiers"][0]["tableIdentifier"].replace(".csv", "")
                columns_dependant = (raw_rule["dependant"]["columnIdentifiers"][0]["columnIdentifier"],)
                table_referenced = raw_rule["referenced"]["columnIdentifiers"][0]["tableIdentifier"].replace(".csv", "")
                columns_referenced = (raw_rule["referenced"]["columnIdentifiers"][0]["columnIdentifier"],)
                inclusion_dependency = InclusionDependency(
                    table_dependant=table_dependant,
                    columns_dependant=columns_dependant,
                    table_referenced=table_referenced,
                    columns_referenced=columns_referenced,
                )
                rules[inclusion_dependency] = (1, 1)
            except (KeyError, IndexError, AttributeError):
                continue

        return rules
