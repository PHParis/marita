import json
import logging
import os
from datetime import datetime
from pathlib import Path

from marita.algorithms.base_algorithm import BaseAlgorithm
from marita.utils.rules import InclusionDependency, Rule
from marita.utils.run_cmd import run_cmd

logger = logging.getLogger(__name__)


class Spider(BaseAlgorithm):
    def discover_rules(self, **kwargs) -> Rule:
        rules = {}
        script_dir = Path(__file__).resolve().parent
        results_path = Path(str(kwargs.get("results_dir", "results")))
        results_path.mkdir(parents=True, exist_ok=True)

        algorithm_name = "SPIDER"
        timeout = int(kwargs.get("timeout", 300))
        memory_gb = float(kwargs.get("memory_gb", 15.0))
        java_heap_gb = int(kwargs.get("java_heap_gb", max(1, int(memory_gb) - 2)))
        class_path = "de.metanome.algorithms.spider.SPIDERFile"
        csv_files = [
            os.path.join(self.database.base_csv_dir, str(table)) for table in os.listdir(self.database.base_csv_dir)
        ]
        current_time = datetime.now()
        jar_path = script_dir.parent / "third_party" / "metanome"
        file_name = f"{current_time.strftime('%Y-%m-%d_%H-%M-%S')}_{algorithm_name}"
        metanome_work_dir = results_path / "_metanome_spider" / file_name
        metanome_work_dir.mkdir(parents=True, exist_ok=True)
        output_prefix = "raw"
        result_file_path = metanome_work_dir / "results" / f"{output_prefix}_inds"
        stderr_path = metanome_work_dir / "stderr.log"

        cmd = [
            "java",
            f"-Xmx{java_heap_gb}G",
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
        if not run_cmd(
            cmd,
            timeout=timeout,
            memory_limit_gb=memory_gb,
            stderr_path=stderr_path,
            cwd=metanome_work_dir,
            logger=logger,
        ):
            raise RuntimeError("SPIDER command failed, timed out, or exceeded memory limit.")

        try:
            with result_file_path.open(encoding="utf-8") as f:
                raw_rules = [line for line in f if line.strip()]
        except FileNotFoundError as exc:
            diagnostic = ""
            if stderr_path.exists():
                diagnostic = stderr_path.read_text(encoding="utf-8", errors="replace").strip()
            message = f"SPIDER did not produce expected Metanome output file: {result_file_path}"
            if diagnostic:
                message = f"{message}. stderr: {diagnostic}"
            raise RuntimeError(message) from exc

        for raw_rule in raw_rules:
            try:
                raw_rule = json.loads(raw_rule)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"SPIDER produced malformed JSON result: {raw_rule.strip()}") from exc

            if raw_rule.get("type") != "InclusionDependency":
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
            except (KeyError, IndexError, AttributeError, TypeError) as exc:
                raise RuntimeError(f"SPIDER produced unsupported inclusion dependency result: {raw_rule}") from exc

        return rules
