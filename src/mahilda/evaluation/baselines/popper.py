from __future__ import annotations

import json
import logging
import os
import re
import shlex
import tempfile
import warnings
from pathlib import Path
from typing import Any, cast

from mahilda.algorithms.base_algorithm import BaseAlgorithm
from mahilda.utils.rules import Predicate, Rule, TGDRule
from mahilda.utils.run_cmd import run_cmd

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


class Popper(BaseAlgorithm):
    def __init__(self, database: Any) -> None:
        super().__init__(database)
        self._safe_to_original_table: dict[str, str] = {}

    def discover_rules(self, **kwargs: Any) -> list[Rule]:
        results_path = Path(str(kwargs.get("results_dir", "results")))
        results_path.mkdir(parents=True, exist_ok=True)
        runtime_root_arg = kwargs.get("runtime_dir")
        timeout = int(kwargs.get("timeout", 300))
        memory_gb = float(kwargs.get("memory_gb", 15.0))
        popper_command = self._resolve_popper_command(kwargs.get("popper_command"))

        runtime_context: tempfile.TemporaryDirectory[str] | None = None
        if runtime_root_arg:
            runtime_root = Path(str(runtime_root_arg))
            runtime_root.mkdir(parents=True, exist_ok=True)
        else:
            runtime_context = tempfile.TemporaryDirectory(prefix=f"popper_{self.database.base_name}_")
            runtime_root = Path(runtime_context.name)

        try:
            return self._discover_with_external_popper(
                results_path=results_path,
                runtime_root=runtime_root,
                timeout=timeout,
                memory_gb=memory_gb,
                popper_command=popper_command,
            )
        finally:
            if runtime_context is not None:
                runtime_context.cleanup()

    @staticmethod
    def _resolve_popper_command(configured_command: Any) -> list[str]:
        value = configured_command or os.environ.get("MAHILDA_POPPER_CMD") or "run-popper"
        if isinstance(value, (list, tuple)):
            command = [str(item) for item in value]
        else:
            command = shlex.split(str(value))
        if not command:
            raise ValueError("Popper command cannot be empty.")
        return command

    def _discover_with_external_popper(
        self,
        *,
        results_path: Path,
        runtime_root: Path,
        timeout: int,
        memory_gb: float,
        popper_command: list[str],
    ) -> list[Rule]:
        tables = self.database.get_table_names()
        if not tables:
            logger.warning("No tables found in the database.")
            return []

        number_max_attributes = max(len(self.database.get_attribute_names(table)) for table in tables)
        number_of_tables = len(tables)
        max_body = max(3, number_of_tables - 1)
        max_vars = number_max_attributes

        compatibility_path = results_path / f"compatibility_{self.database.base_name}.json"
        if compatibility_path.exists():
            loaded_compatibility = json.loads(compatibility_path.read_text(encoding="utf-8"))
            compatibility_dir = loaded_compatibility if isinstance(loaded_compatibility, dict) else {}
        else:
            compatibility_dir = {}

        problem_dirs = self.generate_prolog_files(str(runtime_root / "prolog_tmp"), compatibility_dir)
        rules: list[Rule] = []
        for problem_dir in problem_dirs:
            output_file = results_path / f"{Path(problem_dir).name}_popper.stdout"
            cmd = [
                *popper_command,
                problem_dir,
                "--timeout",
                str(timeout),
                "--max-body",
                str(max_body),
                "--max-vars",
                str(max_vars),
            ]
            if not run_cmd(cmd, timeout=timeout, memory_limit_gb=memory_gb, stdout_path=output_file, logger=logger):
                raise RuntimeError("External Popper command failed, timed out, or exceeded memory limit.")
            rules.extend(self.parse_popper_output(output_file.read_text(encoding="utf-8", errors="replace")))
        return rules

    def parse_popper_output(self, output: str) -> list[Rule]:
        if "NO SOLUTION" in output:
            return []

        precision = self._extract_score(output, "Precision")
        recall = self._extract_score(output, "Recall")
        rules: list[Rule] = []
        in_solution = False
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if "SOLUTION" in line:
                in_solution = True
                continue
            if in_solution and set(line) == {"*"}:
                in_solution = False
                continue
            if in_solution and ":-" in line and line.endswith("."):
                rule = self.convert_prologrule_to_rule(line, precision, recall)
                if rule is not None:
                    rules.append(rule)
        return rules

    @staticmethod
    def _extract_score(output: str, label: str) -> float:
        match = re.search(rf"{label}:([0-9.]+|n/a)", output)
        if not match or match.group(1) == "n/a":
            return -1.0
        return float(match.group(1))

    def convert_prologrule_to_rule(self, prolog_rule: str, precision: float, recall: float) -> TGDRule | None:
        data_str = prolog_rule.strip().rstrip(".")
        try:
            head, body = data_str.split(":-")
        except ValueError:
            logger.warning("Invalid rule format: %s", prolog_rule)
            return None

        new_body: list[Predicate] = []
        new_head: list[Predicate] = []
        variable_usage: dict[str, int] = {}

        for attribute in self._split_literals(body):
            self._append_literal_predicates(attribute, new_body, variable_usage)
        self._append_literal_predicates(head, new_head, variable_usage)

        filtered_body = [pred for pred in new_body if variable_usage.get(pred.variable2, 0) > 1]
        filtered_head = [pred for pred in new_head if variable_usage.get(pred.variable2, 0) > 1]

        return TGDRule(
            cast("Any", tuple(set(filtered_body))),
            cast("Any", tuple(set(filtered_head))),
            display=prolog_rule,
            accuracy=precision,
            confidence=recall,
        )

    @staticmethod
    def _split_literals(body: str) -> list[str]:
        literals: list[str] = []
        current: list[str] = []
        depth = 0
        for char in body:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            if char == "," and depth == 0:
                literal = "".join(current).strip()
                if literal:
                    literals.append(literal)
                current = []
                continue
            current.append(char)
        literal = "".join(current).strip()
        if literal:
            literals.append(literal)
        return literals

    def _append_literal_predicates(
        self,
        literal: str,
        target: list[Predicate],
        variable_usage: dict[str, int],
    ) -> None:
        try:
            relation, vars_part = literal.strip().rstrip(")").split("(", 1)
        except ValueError:
            logger.warning("Invalid literal format: %s", literal)
            return
        safe_relation = relation.strip()
        original_relation = self._safe_to_original_table.get(safe_relation, safe_relation)
        variables = [var.strip() for var in vars_part.split(",")]
        attribute_names = self.database.get_attribute_names(original_relation)
        for index, var in enumerate(variables):
            attribute_name = attribute_names[index] if index < len(attribute_names) else f"attribute{index}"
            pred_id = self.get_random_id()
            target.append(Predicate(pred_id, f"{original_relation}{self.relation_attribute_sep}{attribute_name}", var))
            variable_usage[var] = variable_usage.get(var, 0) + 1

    def generate_prolog_files(self, prolog_tmp_path: str, compatibility_dir: dict | None = None) -> list[str]:
        if compatibility_dir is None:
            compatibility_dir = {}

        tables = self.database.get_table_names()
        if not tables:
            logger.warning("No tables found in the database.")
            return []

        max_vars = 3
        max_body = 6
        created_dirs = []
        self._safe_to_original_table = {self.clean_string(table): table for table in tables}

        if compatibility_dir:
            possible_heads = self.get_possible_heads(compatibility_dir)
            possible_other_tables = self.get_possible_other_tables(compatibility_dir)
        else:
            possible_heads = tables
            possible_other_tables = {table: [t for t in tables if t != table] for table in tables}

        for table in tables:
            if table not in possible_heads:
                continue

            safe_table = self.clean_string(table)
            dir_path = os.path.join(prolog_tmp_path, safe_table)
            os.makedirs(dir_path, exist_ok=True)
            created_dirs.append(dir_path)

            predicates = self.database.get_attribute_names(table)
            examples = [
                f"pos({safe_table}({','.join(self.sanitize_identifier(str(el)) for el in row)}))."
                for row in self.database._select_query(table, predicates)
            ]

            with open(os.path.join(dir_path, "exs.pl"), "w", encoding="utf-8") as exs_file:
                exs_file.write("\n".join(examples) + "\n")

            max_vars = max(max_vars, len(predicates))

            head_pred = f"head_pred({safe_table}, {len(predicates)}).\n"
            body_preds = []
            bk_predicates = [f":- dynamic {safe_table}/{len(predicates)}.\n"]

            for other_table in tables:
                if other_table == table or other_table not in possible_other_tables.get(table, []):
                    continue

                other_safe_table = self.clean_string(other_table)
                other_predicates = self.database.get_attribute_names(other_table)
                bk_predicates.append(f":- dynamic {other_safe_table}/{len(other_predicates)}.\n")
                body_preds.append(f"body_pred({other_safe_table}, {len(other_predicates)}).\n")

                for row in self.database._select_query(other_table, other_predicates):
                    str_row = [self.sanitize_identifier(str(el)) for el in row]
                    bk_predicates.append(f"{other_safe_table}({','.join(str_row)}).\n")

            bk_predicates = sorted(bk_predicates)
            with open(os.path.join(dir_path, "bk.pl"), "w", encoding="utf-8") as bk_file:
                bk_file.writelines(bk_predicates)

            bias_content = f"max_body({max_body}).\nmax_vars({max_vars}).\nallow_singletons.\n{head_pred}" + "".join(
                body_preds
            )

            with open(os.path.join(dir_path, "bias.pl"), "w", encoding="utf-8") as bias_file:
                bias_file.write(bias_content)

        return created_dirs

    def is_integer(self, value: Any) -> bool:
        if isinstance(value, int) and not isinstance(value, bool):
            return True
        if isinstance(value, str):
            return value.isdigit()
        return False

    def filter_non_alpha(self, input_string: str) -> str:
        return re.sub(r"[^a-zA-Z]", "", input_string)

    def sanitize_identifier(self, identifier: str) -> str:
        if identifier is None or str(identifier).lower() == "none":
            return "_"
        if self.is_integer(identifier):
            return str(identifier)
        identifier = self.filter_non_alpha(identifier)
        filtered = "".join(ch for ch in identifier if ch.isalpha()).lower()
        return filtered if filtered else "_"

    def clean_string(self, value: str) -> str:
        forbidden_chars = "'() \n.:-/,¡"
        return "".join(ch for ch in value.lower().replace(" ", "") if ch not in forbidden_chars)

    def get_possible_heads(self, compatibility_dir: dict, sep: str = "___sep___") -> list[str]:
        heads = []
        for key, values in compatibility_dir.items():
            heads.append(key.split(sep)[0])
            heads.extend(value.split(sep)[0] for value in values)
        return heads

    def get_possible_other_tables(self, compatibility_dir: dict, sep: str = "___sep___") -> dict:
        tables = {}
        for key, values in compatibility_dir.items():
            table = key.split(sep)[0]
            tables.setdefault(table, []).extend(value.split(sep)[0] for value in values)
        return tables

    def get_random_id(self) -> str:
        import random

        return f"id-{random.randint(0, 10000)}"

    @property
    def relation_attribute_sep(self) -> str:
        return "_"
