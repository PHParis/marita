import importlib
import json
import os
import shutil
import warnings
from datetime import datetime
from types import ModuleType
from typing import List, Optional

from algorithms.rule_discovery_algorithm import RuleDiscoveryAlgorithm
from utils.rules import HornRule, Predicate, Rule, TGDRule

warnings.filterwarnings("ignore")


def import_and_reload_package(package_name: str) -> ModuleType:
    """
    Import and reload a Python package.

    This function imports a Python package and then reloads it to ensure that
    any changes made to the package are reflected in the current session.

    Parameters:
        package_name (str): The name of the package to import and reload.

    Returns:
        ModuleType: The reloaded package.
    """
    package = importlib.import_module(package_name)
    importlib.reload(package)
    return package


class ILP(RuleDiscoveryAlgorithm):
    def discover_rules(self, **kwargs) -> List[Rule]:
        # Copy Popper sources into the current directory
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            target_dir = os.path.join(script_dir,"../", "popper")
            shutil.copytree(f"{script_dir}/bins/popper", target_dir, dirs_exist_ok=True)
        except Exception as e:
            print(f"Error copying Popper sources: {e}")
            return []

        tables = self.database.get_table_names()
        if not tables:
            print("No tables found in the database.")
            return []

        number_max_attributes = max(len(self.database.get_attribute_names(table)) for table in tables)
        number_of_tables = len(tables)
        max_body = max(3, number_of_tables - 1)
        max_vars = number_max_attributes

        prolog_tmp = "prolog_tmp"
        results_path = kwargs.get("results_dir", "results")

        compatibility_path = os.path.join(results_path, f"compatibility_{self.database.base_name}.json")

        if os.path.exists(compatibility_path):
            with open(compatibility_path) as f:
                compatibility_dir = json.load(f)
        else:
            compatibility_dir = []

        directories = self.generate_prolog_files(prolog_tmp, compatibility_dir)
        rules = []

        for directory in directories:
            try:
                popper = import_and_reload_package("popper")
                settings = popper.util.Settings(
                    kbpath=directory,
                    max_body=max_body,
                    max_vars=max_vars,
                    quiet=False,
                    debug=True,
                )
                try:
                    prog, score, stats = popper.loop.learn_solution(settings)
                except Exception as e:
                    print(f"Popper learning error: {e}")
                    raise Exception("Popper failed to learn a solution") from e

                if prog is None:
                    continue

                raw_rules = popper.util.format_prog(popper.util.order_prog(prog)).split("\n")
                for raw_rule in raw_rules:
                    if not raw_rule.strip():
                        continue
                    rule = self.process_raw_rule(raw_rule, score)
                    if rule:
                        rules.append(rule)
            except Exception as e:
                print(f"Error processing directory {directory}: {e}")
                raise

        return rules

    def process_raw_rule(self, raw_rule: str, score) -> Optional[Rule]:
        variables_used = {}
        try:
            head, body = raw_rule.split(":-")
            head = head.strip()
            body = body.strip().rstrip(".")
        except ValueError:
            print(f"Invalid rule format: {raw_rule}")
            return None

        body_lst = self.parse_predicates(body, variables_used)
        head_lst = self.parse_head(head, variables_used)

        try:
            precision = float(score[0]) / (score[0] + score[1])
        except (IndexError, ZeroDivisionError):
            precision = 0

        # Remove variables used only once
        variables_to_delete = [var for var, count in variables_used.items() if count == 1]
        body_lst = [pred for pred in body_lst if pred.variable2 not in variables_to_delete]
        head_lst = [pred for pred in head_lst if pred.variable2 not in variables_to_delete]

        if not body_lst and not head_lst:
            return None

        return self.convert_prologrule_to_rule(raw_rule, precision, -1)

    def parse_predicates(self, body: str, variables_used: dict) -> List[Predicate]:
        predicates = []
        for predicate in body.split("),"):
            predicate = predicate.strip().rstrip(")")
            try:
                relation, variables = predicate.split("(")
                variables = variables.split(",")
            except ValueError:
                print(f"Invalid predicate format: {predicate}")
                continue
            for var in variables:
                variables_used[var] = variables_used.get(var, 0) + 1
            pred_id = self.get_random_id()
            predicates.append(Predicate(pred_id, relation.strip(), var))
        return predicates

    def parse_head(self, head: str, variables_used: dict) -> List[Predicate]:
        predicates = []
        try:
            relation, variables = head.split("(")
            variables = variables.split(",")
        except ValueError:
            print(f"Invalid head format: {head}")
            return predicates
        for var in variables:
            variables_used[var] = variables_used.get(var, 0) + 1
        pred_id = self.get_random_id()
        predicates.append(Predicate(pred_id, relation.strip(), var))
        return predicates

    def convert_prologrule_to_rule(self, prolog_rule: str, precision: float, recall: float) -> TGDRule:
        def get_random_id():
            import random
            return f"id-{random.randint(0, 10000)}"

        data_str = prolog_rule.replace(".", "")
        try:
            head, body = data_str.split(":-")
        except ValueError:
            print(f"Invalid rule format: {prolog_rule}")
            return None

        body = body.split("),") if body.count(")") > 1 else [body]
        new_body = []
        new_head = []
        variables_usage = {}

        for attribute in body:
            attribute = attribute.strip().rstrip(")")
            try:
                relation, vars_part = attribute.split("(")
            except ValueError:
                print(f"Invalid attribute format: {attribute}")
                continue
            variables = vars_part.split(",")
            attributes_names = self.database.get_attribute_names(relation.strip())
            for i, var in enumerate(variables):
                if i >= len(attributes_names):
                    attribute_name = f"attribute{i}"
                else:
                    attribute_name = attributes_names[i]
                pred_id = get_random_id()
                new_body.append(Predicate(pred_id, f"{relation}{self.relation_attribute_sep}{attribute_name}", var))
                variables_usage[var] = variables_usage.get(var, 0) + 1

        head_relation, head_vars = head.split("(")
        head_vars = head_vars.rstrip(")").split(",")
        attributes_names = self.database.get_attribute_names(head_relation.strip())

        for i, var in enumerate(head_vars):
            if i >= len(attributes_names):
                attribute_name = f"attribute{i}"
            else:
                attribute_name = attributes_names[i]
            pred_id = get_random_id()
            new_head.append(Predicate(pred_id, f"{head_relation}{self.relation_attribute_sep}{attribute_name}", var))
            variables_usage[var] = variables_usage.get(var, 0) + 1

        # Filter predicates based on variable usage
        new_new_body = [pred for pred in new_body if variables_usage.get(pred.variable2, 0) > 1]
        new_new_head = [pred for pred in new_head if variables_usage.get(pred.variable2, 0) > 1]

        return TGDRule(
            list(set(new_new_body)),
            list(set(new_new_head)),
            display=prolog_rule,
            accuracy=precision,
            confidence=recall
        )

    def generate_prolog_files(self, prolog_tmp_path: str, compatibility_dir: dict = None) -> List[str]:
        """
        Generates Prolog files for each table in the database.

        Args:
            prolog_tmp_path (str): The path where the Prolog files will be created.
            compatibility_dir (dict, optional): Compatibility information. Defaults to None.

        Returns:
            List[str]: A list of paths to the directories created for each table.
        """
        if compatibility_dir is None:
            compatibility_dir = {}

        tables = self.database.get_table_names()
        if not tables:
            print("No tables found in the database.")
            return []

        max_vars = 3
        max_body = 6
        created_dirs = []

        if compatibility_dir:
            possible_heads = self.get_possible_heads(compatibility_dir)
            possible_other_tables = self.get_possible_other_tables(compatibility_dir)
        else:
            possible_heads = tables
            possible_other_tables = {table: [t for t in tables if t != table] for table in tables}

        for table in tables:
            if table not in possible_heads:
                continue

            dir_path = os.path.join(prolog_tmp_path, table)
            os.makedirs(dir_path, exist_ok=True)
            created_dirs.append(dir_path)

            predicates = self.database.get_attribute_names(table)
            examples = [
                f"pos({self.clean_string(table)}({','.join(self.sanitize_identifier(str(el)) for el in row)}))."
                for row in self.database._select_query(table, predicates)
            ]

            with open(os.path.join(dir_path, "exs.pl"), "w") as exs_file:
                exs_file.write("\n".join(examples) + "\n")

            max_vars = max(max_vars, len(predicates))

            head_pred = f"head_pred({self.clean_string(table)}, {len(predicates)}).\n"
            body_preds = []
            bk_predicates = [f":- dynamic {self.clean_string(table)}/{len(predicates)}.\n"]

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
            with open(os.path.join(dir_path, "bk.pl"), "w") as bk_file:
                bk_file.writelines(bk_predicates)

            bias_content = (
                f"max_body({max_body}).\n"
                f"max_vars({max_vars}).\n"
                "allow_singletons.\n"
                f"{head_pred}"
                + "".join(body_preds)
            )

            with open(os.path.join(dir_path, "bias.pl"), "w") as bias_file:
                bias_file.write(bias_content)

        return created_dirs

    def is_integer(self, value) -> bool:
        if isinstance(value, int) and not isinstance(value, bool):
            return True
        if isinstance(value, str):
            return value.isdigit()
        return False

    def filter_non_alpha(self, input_string: str) -> str:
        import re
        return re.sub(r'[^a-zA-Z]', '', input_string)

    def sanitize_identifier(self, identifier: str) -> str:
        """Sanitize the given identifier."""
        if identifier is None or str(identifier).lower() == "none":
            return '_'
        if self.is_integer(identifier):
            return str(identifier)
        identifier = self.filter_non_alpha(identifier)
        filtered = "".join(ch for ch in identifier if ch.isalpha()).lower()
        return filtered if filtered else "_"

    def clean_string(self, s: str) -> str:
        """Clean the given string by removing specified characters."""
        forbidden_chars = "'() \n.:-/,¡"
        return "".join(ch for ch in s.lower().replace(" ", "") if ch not in forbidden_chars)

    def get_possible_heads(self, compatibility_dir: dict, sep: str = "___sep___") -> List[str]:
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
