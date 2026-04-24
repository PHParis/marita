import copy
import os
from collections.abc import Callable, Iterator
from itertools import chain, combinations
from statistics import mean
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Tuple

from textwrap import indent
from mahilda.utils.rules import Rule
import json
import re
from mahilda.utils.rules import Predicate, TGDRule
from tqdm import tqdm
from mahilda.algorithms.mahilda_core.constraint_graph import (
    Attribute,
    AttributeMapper,
    ConstraintGraph,
    IndexedAttribute,
    JoinableIndexedAttributes,
)
from mahilda.algorithms.mahilda_core.candidate_rule_chains import CandidateRuleChains
from mahilda.database.alchemy_utility import AlchemyUtility
import time

# from runs_utils.postprocessing.analytics.generate_overall_table import logger

_MAHILDA_LOG_DIR = Path(os.environ.get("MAHILDA_LOG_DIR", "logs"))
_MAHILDA_LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(_MAHILDA_LOG_DIR / "tgd_computation.log"),
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    filemode='w'
)

APPLY_DISJOINT = True  # Set to True if you want to apply disjoint semantics
SPLIT_PRUNING_MEAN_THRESHOLD = 0
TableOccurrence = tuple[int, int]
CandidateRule = list[JoinableIndexedAttributes]

import json
import logging
import time
from tqdm import tqdm

def init(
    db_inspector: AlchemyUtility,
    max_nb_occurrence: int = 3,
    max_nb_occurrence_per_table_and_column: dict[str, dict[str, int]] = {},
    results_path: str = None,
) -> tuple[ConstraintGraph, AttributeMapper, list[JoinableIndexedAttributes]]:
    """
    Initialize the constraint graph and attribute mapper.
    :param db_inspector: AlchemyUtility instance
    :param max_nb_occurrence: Maximum number of occurrences for each table
    :return: A tuple containing the constraint graph, attribute mapper, and list of compatible indexed attributes
    """
    # Input validation
    if not db_inspector or not hasattr(db_inspector, "base_name"):
        raise ValueError("Invalid db_inspector provided.")

    try:
        time_taken_init = time.time()
        # Generate all attributes
        attributes = Attribute.generate_attributes(db_inspector)
        if not attributes:
            logging.warning("No attributes generated. Exiting initialization.")
            return None, None, []

        # Retrieve database parameters (if available)
        base_name = db_inspector.base_name

        # Find compatible attributes
        compatible_attributes: set[tuple[Attribute, Attribute]] = set()
        for i, attr1 in enumerate(
            tqdm(attributes, desc="Finding compatible attributes", leave=False)
        ):
            for attr2 in attributes[i:]:
                if attr1.is_compatible(
                    attr2,
                    db_inspector=db_inspector,
                ):
                    compatible_attributes.add((attr1, attr2))

        # Export compatible attributes as JSON
        compatible_dict_to_export = {}
        for attr1, attr2 in compatible_attributes:
            key1 = f"{attr1.table}___sep___{attr1.name}"
            key2 = f"{attr2.table}___sep___{attr2.name}"
            compatible_dict_to_export.setdefault(key1, []).append(key2)
            compatible_dict_to_export.setdefault(key2, []).append(key1)

        with open(f"{results_path}/compatibility_{base_name}.json", "w") as f:
            json.dump(compatible_dict_to_export, f, indent=4)


        time_compute_compatible = time.time() - time_taken_init

        # Create indexes for compatible attributes
        try:
            db_inspector.create_composed_indexes(
                [
                    (attr1.table, attr1.name, attr2.table, attr2.name)
                    for attr1, attr2 in compatible_attributes
                ]
            )
        except Exception as e:
            logging.error(f"Error creating composed indexes: {e}")
        time_to_compute_indexed = time.time() - time_taken_init

        # Attribute index mapping
        tables = db_inspector.get_table_names()
        table_name_to_index = {table: i for i, table in enumerate(tables)}
        attribute_name_to_index = {
            table: {attr: i for i, attr in enumerate(db_inspector.get_attribute_names(table))}
            for table in tables
        }

        mapper = AttributeMapper(table_name_to_index, attribute_name_to_index)

        # List creation of compatible indexed attributes
        jia_list: list[JoinableIndexedAttributes] = []
        for table_occurrence1 in range(max_nb_occurrence):
            for table_occurrence2 in range(max_nb_occurrence):
                for attr1, attr2 in compatible_attributes:
                    if (
                        max_nb_occurrence_per_table_and_column.get(attr1.table, {}).get(
                            attr1.name, max_nb_occurrence
                        )
                        < table_occurrence1
                    ):
                        continue
                    if (
                        max_nb_occurrence_per_table_and_column.get(attr2.table, {}).get(
                            attr2.name, max_nb_occurrence
                        )
                        < table_occurrence2
                    ):
                        continue
                    # if (
                    #     attr1.table == attr2.table
                    #     and table_occurrence1 == table_occurrence2
                    # ):
                    #     continue
                    jia = JoinableIndexedAttributes(
                        mapper.attribute_to_indexed(attr1, table_occurrence1),
                        mapper.attribute_to_indexed(attr2, table_occurrence2),
                    )
                    jia_list.append(jia)
        jia_list.sort()

        # Create a constraint graph
        cg = ConstraintGraph()
        for i, jia in enumerate(
            tqdm(jia_list, desc="Creating constraint graph", leave=False)
        ):
            cg.add_node(jia)
            for jia2 in jia_list[i + 1 :]:

                if jia != jia2 and jia.is_connected(jia2):
                    cg.add_node(jia2)
                    cg.add_edge(jia, jia2)
        time_building_cg = time.time() - time_taken_init

        # Export constraint graph metrics
        with open(f"{results_path}/cg_metrics_{base_name}.json", "w") as f:
            json.dump(str(cg), f)
        with open(f"{results_path}/init_time_metrics_{base_name}.json", "w") as f:
            json.dump(
                {
                    "time_compute_compatible": time_compute_compatible,
                    "time_to_compute_indexed": time_to_compute_indexed,
                    "time_building_cg": time_building_cg,
                },
                f,
                indent=4,
            )

        return cg, mapper, jia_list

    except Exception as e:
        logging.error(f"An error occurred during initialization: {e}")
        return None, None, []


def dfs(
    graph: ConstraintGraph,
    start_node: JoinableIndexedAttributes,
    pruning_prediction: Callable[
        [CandidateRule, AttributeMapper, AlchemyUtility],
        bool,
    ],
    db_inspector: AlchemyUtility,
    mapper: AttributeMapper,
    visited: set[JoinableIndexedAttributes] = None,
    candidate_rule: CandidateRule = None,
        max_table: int = 3,
        max_vars: int = 4,
        horn_rule_pruning_dict: Dict[JoinableIndexedAttributes, Tuple[float, float]] = dict(),
) -> Iterator[CandidateRule, Tuple[JoinableIndexedAttributes, JoinableIndexedAttributes], Tuple[float, float]]:
    """
    Perform a Depth-First Search (DFS) traversal with a path-based heuristic,
    yielding the candidate rule leading up to a heuristic-determined stop.

    :param graph: An instance of the ConstraintGraph class.
    :param start_node: The node from which the DFS starts.
    :param heuristic: A function that takes the current path and decides whether to continue.
    :param visited: A set to keep track of visited nodes to avoid cycles.
    :param candidate_rule: A list to track the current path of nodes being visited.
    :yield: The path up to but not including the node that causes the heuristic to return False.
    """
    if visited is None:
        visited: set[JoinableIndexedAttributes] = set()
    if candidate_rule is None:
        candidate_rule = []
    if start_node is None:
        for next_node in tqdm(graph.nodes, desc="Initial Nodes"):
            """
            note: mandatory because some jia are built with not the right order in table occurrences
            they are needed to have all the runs, but at init we prune them.
            """

            if next_node_test(candidate_rule, next_node, visited, max_table, max_vars):
                yield from dfs(
                    graph,
                    next_node,
                    pruning_prediction,
                    db_inspector,
                    mapper,
                    visited=set(),
                    candidate_rule=copy.deepcopy(candidate_rule),
                    max_table=max_table,
                    max_vars=max_vars
                )
        return
    visited.add(start_node)
    candidate_rule.append(start_node)
    # Apply the heuristic to the current path; if False,
    # yield the path without the last node and return
    if not pruning_prediction(candidate_rule, mapper, db_inspector):
        return
    
    splits = split_candidate_rule(candidate_rule)
    should_continue = False 
    for body, head in splits:
        if not body or not head : continue
        if len(head) != 1: continue 
        head_relation = next(iter(head))
        condition_check, support, confidence = split_pruning(
            candidate_rule, body, head, db_inspector, mapper
        )

        new_metrics= (support,confidence)
        should_yield = False
        if head_relation not in horn_rule_pruning_dict: 
            horn_rule_pruning_dict[head_relation] = new_metrics
            should_yield = True 
            #print(f"New rule: {head_relation} with support {support} and confidence {confidence}")
        else:
            # prune on support
            if horn_rule_pruning_dict[head_relation][0] < support:
                horn_rule_pruning_dict[head_relation] = new_metrics
                should_yield = True
        if should_yield:
            yield candidate_rule,(body, head), new_metrics
            should_continue = True 
    cr_chains = CandidateRuleChains(candidate_rule).cr_chains
    # 2. Affecter les variables
    variable_assignment = assign_variables(cr_chains, (body, head))
    # 3. Récupérer les variables du head et du body
    head_vars = set(variable_assignment[attr] for attr in variable_assignment if (attr.i, attr.j) in head)
    body_vars = set(variable_assignment[attr] for attr in variable_assignment if (attr.i, attr.j) in body)
    # 4. Vérifier la connexion
    for var in head_vars:
        if var not in body_vars:
            # pass 
            should_continue = True 
            # print(f"Variable {var} in head is not connected to the body!")

    if not should_continue :
        #print("early stop, no splits found")
        return 
    
    # pass 
    #print("early stop, no splits found")    
    # return 
    # yield candidate_rule

    # for next_node in tqdm(graph.neighbors(start_node), desc=f"Expanding {start_node}", leave=False):
    # neighbours = graph.neighbors(start_node)
    # splits = split_candidate_rule(candidate_rule)
    # split = splits.pop()
    #debug = instantiate_tgd(candidate_rule, split, mapper)
    big_neighbours = []
    for node in candidate_rule:
        big_neighbours += [e for e in graph.neighbors(node) if e not in visited]
    # big_neighbours = [e for e in graph.neighbors(node) for node in candidate_rule
    for next_node in big_neighbours: # graph.neighbors(start_node):
        if next_node_test(candidate_rule, next_node, visited, max_table, max_vars):
            yield from dfs(
                graph,
                next_node,
                pruning_prediction,
                db_inspector,
                mapper,
                visited=visited,
                candidate_rule=candidate_rule,
                max_table=max_table,
                max_vars=max_vars
            )
            visited.remove(next_node)
            candidate_rule.pop()
    # visited.pop()
    # candidate_rule.pop()
def prediction(
    path: CandidateRule,
    mapper: AttributeMapper,
    db_inspector: AlchemyUtility,
    body: set[TableOccurrence] = None,
    head: set[TableOccurrence] = None,
    threshold: int = None,
) -> int:
    """
    Calculate the set of tuples that satisfy the tuple-generating dependency (TGD) R,
    considering the disjoint semantics to ensure non-redundancy.

    :param path: A list of JoinableIndexedAttributes instances.
    :param mapper: An instance of AttributeMapper for attribute mapping.
    :param db_inspector: An instance of AlchemyUtility for database interaction.
    :return: A set or list of tuples that satisfy the TGDs according to the disjoint semantics.
    """
    join_conditions: list[tuple[str, int, str, str, int, str]] = []
    if body is not None and head is not None:
        x_chains = CandidateRuleChains(path).get_x_chains(body, head, mapper)
    else:
        x_chains = None
    if path is None:
        return 0
    for indexed_attr1, indexed_attr2 in path:
        attr1 = mapper.indexed_attribute_to_attribute(indexed_attr1)
        attr2 = mapper.indexed_attribute_to_attribute(indexed_attr2)
        join_conditions.append(
            (
                attr1.table,
                indexed_attr1.j,
                attr1.name,
                attr2.table,
                indexed_attr2.j,
                attr2.name,
            )
        )
    #logging.info("join_conditions",join_conditions)
    if threshold is not None:
        return bool(db_inspector.check_threshold(
            join_conditions,
            count_over=x_chains,
            flag="x_prediction",
            disjoint_semantics=APPLY_DISJOINT,
            threshold=threshold,
        ))
    if x_chains is not None:
        return db_inspector.get_join_row_count(
            join_conditions,
            count_over=x_chains,
            flag="x_prediction",
            disjoint_semantics=APPLY_DISJOINT,
        )
    return db_inspector.get_join_row_count(
        join_conditions, disjoint_semantics=APPLY_DISJOINT, flag="prediction"
    )


def path_pruning(
    path: CandidateRule,
    mapper: AttributeMapper,
    db_inspector: AlchemyUtility,
) -> bool:
    """
    This function checks if a given path (candidate rule) should be pruned or not based on the prediction count.
    The prediction count is the number of tuples that satisfy the tuple-generating dependency (TGD) R,
    considering the disjoint semantics to ensure non-redundancy.

    :param path: A list of JoinableIndexedAttributes instances representing the candidate rule.
    :param mapper: An instance of AttributeMapper for attribute mapping.
    :param db_inspector: An instance of AlchemyUtility for database interaction.
    :return: A boolean value indicating whether the path should be pruned or not.
             If the prediction count is greater than 0, the function returns True, meaning the path should not be pruned.
             If the prediction count is 0 or the path is None or empty, the function returns False, meaning the path should be pruned.
    """
    if path is None:
        return False
    if len(path) == 0:
        return False
    return prediction(path,mapper,db_inspector, threshold=0)
    #prediction_count = prediction(path, mapper, db_inspector)
    #return prediction_count > 0


def split_pruning(
        candidate_rule: CandidateRule,
        body: set[TableOccurrence],
        head: set[TableOccurrence],
        db_inspector: AlchemyUtility,
        mapper: AttributeMapper,
) -> bool:
    """
    This function checks if a given candidate rule should be pruned based on its support and confidence.
    Logs the instantiated TGD, support, and confidence.

    :param candidate_rule: A list of JoinableIndexedAttributes instances representing the candidate rule.
    :param body: A set of table occurrences representing the body of the candidate rule.
    :param head: A set of table occurrences representing the head of the candidate rule.
    :param db_inspector: An instance of AlchemyUtility for database interaction.
    :param mapper: An instance of AttributeMapper for attribute mapping.
    :return: A boolean value indicating whether the candidate rule should be pruned.
    """
    if len(body) == 0 and len(head) == 0:
        return False, 0, 0  # invalid split, should not happen
    if len(body) == 0 or len(head) == 0:
        return False, 0, 0  # we prune empty body or head
    
    # pruning non-horn rules 
    if len(head) != 1:
        return False, 0, 0
    pairs_count = Counter((attr.i, attr.j) for jia in candidate_rule for attr in jia)

    table_indexed = defaultdict(list)
    for i, j in head | body:  # Union of both frozensets
        table_indexed[i].append(j)
    for i in table_indexed:
        if len(table_indexed[i]) > 1:
            if pairs_count[(i, table_indexed[i][0])] == 1:
                return False, 0, 0
            #return False, 0, 0


    # for each table indexed , if the number of element is greater than 1, we prune if there is two tables with the same


    # Filter out rules where the same table is repeated with the same variables
    #body_tables={attr.i for attr in body}

    #body_tables=
    #if len(body_tables) < len(body) or len(head_tables) < len(head):
    #    return False, 0, 0
    total_tuple_test = prediction(candidate_rule, mapper, db_inspector, body, head, threshold=0)
    if total_tuple_test is False:
        return False, 0, 0  # prune if the prediction is 0

    total_tuples = prediction(candidate_rule, mapper, db_inspector, body, head)

    support = calculate_support(candidate_rule, body, head, db_inspector, mapper, total_tuples)
    confidence = calculate_confidence(candidate_rule, body, head, db_inspector, mapper, total_tuples)

    if confidence == 0 and support == 0:
        return False, 0, 0

    return mean([support, confidence]) > SPLIT_PRUNING_MEAN_THRESHOLD, support, confidence


def powerset(iterable):
    "powerset([1,2,3]) --> () (1,) (2,) (3,) (1,2,) (1,3,) (2,3,) (1,2,3)"
    s = list(iterable)
    return chain.from_iterable(combinations(s, r) for r in range(len(s) + 1))


def extract_table_occurrences(
    candidate_rule: CandidateRule,
) -> set[TableOccurrence]:
    """
    Extracts the set of table occurrences from a candidate rule.
    :param candidate_rule: List of JoinableIndexedAttributes representing the candidate rule.
    :return: Set of tuples representing table occurrences (i, j).
    """
    table_occurrences = set()
    for attr1, attr2 in candidate_rule:
        table_occurrences.add((attr1.i, attr1.j))
        table_occurrences.add((attr2.i, attr2.j))
    return table_occurrences


def attr(
    table_occurrence: TableOccurrence,
    candidate_rule: CandidateRule,
) -> JoinableIndexedAttributes:
    """
    Extracts the attributes of a table occurrence from a candidate_rule.
    :param table_occurrence: Tuple representing a table occurrence (i, j).
    :param candidate_rule: List of JoinableIndexedAttributes representing the candidate rule.
    :return: The list of attributes of the table occurrence.
    """
    cr_chains = CandidateRuleChains(candidate_rule).cr_chains
    cr_chains_table_occurrence = []
    for chain in cr_chains:
        for attribute in chain:
            if attribute.i == table_occurrence[0] and attribute.j == table_occurrence[1]:
                cr_chains_table_occurrence.append(attribute)
        # if any(
        #     attr.i == table_occurrence[0] and attr.j == table_occurrence[1]
        #     for attr in chain
        # ):
        #     cr_chains_table_occurrence.append(chain)
    return cr_chains_table_occurrence

def split_candidate_rule(
    candidate_rule: CandidateRule,
) -> set[
    tuple[set[(int, int)], set[(int, int)]]
]:  # where (int, int) is a table occurrence
    """
    Split a path into a set of table occurrence pairs.

    :param candidate_rule: A list of tuples of JoinableIndexedAttributes (representing the candidate_rule)
    :return: A set of table occurrence pairs
    """
    table_occurrences = extract_table_occurrences(candidate_rule)
    if candidate_rule is None or len(candidate_rule) == 0:
        return False
    valid_splits = set()
    for body in powerset(table_occurrences):
        body = set(body)
        head = table_occurrences - body
        # Check the condition for all (i, j) in head
        condition_met = True
        if len(head) == 0:
            condition_met = False
        for ij in body:
            if any(
                ij[0] == ijp[0]
                and ijp[1] < ij[1]
                and attr(ij, candidate_rule) == attr(ijp, candidate_rule)
                for ijp in table_occurrences
            ):
                condition_met = False
                break
            else:
                condition_met = True
        if condition_met:
            valid_splits.add((frozenset(body), frozenset(head)))
    return valid_splits


def instantiate_tgd(
    candidate_rule: CandidateRule,
    split: tuple[set[TableOccurrence], set[TableOccurrence]],
    mapper: AttributeMapper,
) -> str:
    """
    This function instantiates a tuple-generating dependency (TGD) from a candidate rule and a split.
    The function performs the following steps:
    1. Determine the equivalence classes from the candidate rule.
    2. Assign variables to each equivalence class.
    3. Construct the predicates for the body and head of the TGD.
    The function returns the instantiated TGD as a string.

    :param candidate_rule: A list of JoinableIndexedAttributes instances representing the candidate rule.
    :param split: A tuple containing two sets of table occurrences representing the body and head of the split.
    :param mapper: An instance of AttributeMapper for attribute mapping.
    :return: A string representing the instantiated TGD.
    """
    # Step 1: Determine the equivalence classes from the candidate rule
    cr_chains = CandidateRuleChains(candidate_rule).cr_chains
    # Step 2: Assign variables to each equivalence class
    variable_assignment = assign_variables(cr_chains, split)
    # Step 3: Construct the predicates
    psi, phi = construct_predicates(variable_assignment, candidate_rule, mapper, split)
    # Return the instantiated TGD as a string
    tgd_str = construct_tgd_string(psi, phi, variable_assignment, split[0], split[1])
    return tgd_str


def construct_tgd_string(
    psi: str,
    phi: str,
    variable_assignment: dict[IndexedAttribute, str],
    body: set[TableOccurrence],
    head: set[TableOccurrence],
):
    """
    This function constructs a string representation of a tuple-generating dependency (TGD) given the predicates for the body and head,
    the variable assignments, and the body and head of the split.

    The function performs the following steps:
    1. Extract unique variables for psi and phi from variable_assignment.
    2. Variables in psi but not in phi are universally quantified (for all).
    3. Variables in phi are existentially quantified (exists), including those also in psi.
    4. Format the variables for inclusion in the TGD string.
    5. Construct the full TGD string.

    :param psi: A string representing the predicate for the body.
    :param phi: A string representing the predicate for the head.
    :param variable_assignment: A dictionary mapping each IndexedAttribute to a variable name.
    :param body: A set of table occurrences representing the body of the split.
    :param head: A set of table occurrences representing the head of the split.
    :return: A string representing the instantiated TGD.
    """
    # Extract unique variables for psi and phi from variable_assignment
    # Considering the body and head to distinguish between variables in psi
    # and phi
    body_vars = set(
        variable_assignment[attr]
        for attr in variable_assignment
        if (attr.i, attr.j) in body
    )
    head_vars = set(
        variable_assignment[attr]
        for attr in variable_assignment
        if (attr.i, attr.j) in head
    )
    # Variables in psi but not in phi are universally quantified (for all)
    universal_vars = body_vars - (head_vars - body_vars)
    # Variables in phi are existentially quantified (exists), including those
    # also in psi
    existential_vars = head_vars - body_vars
    # Format the variables for inclusion in the TGD string
    universal_vars_str = ", ".join(sorted(universal_vars))
    existential_vars_str = ", ".join(sorted(existential_vars - universal_vars))
    # Construct the full TGD string
    if existential_vars_str and phi:
        head_str = f"∃ {existential_vars_str}: {phi}"
    elif phi:
        head_str = f"{phi}"
    else:
        head_str = "⊥"
    if universal_vars_str and psi:
        body_str = f"∀ {universal_vars_str}: {psi}"
    elif psi:
        body_str = f"{psi}"
    else:
        body_str = "⊤"
    tgd_string = f"{body_str} ⇒ {head_str}"
    return tgd_string


def assign_variables(
    equivalence_classes: list[set[JoinableIndexedAttributes]],
    split: tuple[set[TableOccurrence], set[TableOccurrence]],
) -> dict[IndexedAttribute, str]:
    """
    This function assigns variables to the attributes in the equivalence classes based on their presence in the body and head of the split.
    The variables are named following a convention: 'x' for attributes present in both body and head, 'y' for attributes only in the body, and 'z' for attributes only in the head.

    :param equivalence_classes: A list of sets of JoinableIndexedAttributes, each set represents an equivalence class.
    :param split: A tuple containing two sets of table occurrences representing the body and head of the split.
    :return: A dictionary mapping each IndexedAttribute to a variable name.
    """
    variables_assignment = {}
    body, head = split
    # Variable naming convention for clarity
    variable_counter = {"x": 0, "y": 0, "z": 0}
    for ec in equivalence_classes:
        # Determine if any member of the equivalence class is in the body and head
        in_body = any((attr.i, attr.j) in body for attr in ec)
        in_head = any((attr.i, attr.j) in head for attr in ec)
        if in_body and in_head:
            # Assign to x variables
            variable_name = f"x{variable_counter['x']}"
            variable_counter["x"] += 1
        elif in_body and not in_head:
            # Assign to y variables
            variable_name = f"y{variable_counter['y']}"
            variable_counter["y"] += 1
        else:
            # Assign to z variables
            variable_name = f"z{variable_counter['z']}"
            variable_counter["z"] += 1
        # Assign the variable name to all members of the equivalence class
        for attr in ec:
            variables_assignment[attr] = variable_name
    return variables_assignment


def construct_predicates(
    variable_assignment: dict[IndexedAttribute, str],
    candidate_rule: CandidateRule,
    mapper: AttributeMapper,
    split: tuple[set[TableOccurrence], set[TableOccurrence]],
) -> tuple[str, str]:
    """
    This function constructs the predicates for the body and head of a candidate rule.
    The predicates are constructed based on the variable assignments and the split of the candidate rule.
    The function returns two strings representing the predicates for the body and head.

    :param variable_assignment: A dictionary mapping each IndexedAttribute to a variable name.
    :param candidate_rule: A list of JoinableIndexedAttributes instances representing the candidate rule.
    :param mapper: An instance of AttributeMapper for attribute mapping.
    :param split: A tuple containing two sets of table occurrences representing the body and head of the split.
    :return: A tuple containing two strings. The first string is the predicate for the body (psi),
             and the second string is the predicate for the head (phi).
    """
    # Initialize the components of the predicates
    body_predicates = []  # For \psi
    head_predicates = []  # For \phi
    body, head = split
    # Group by table occurrences
    attr_by_table_occurrence = {}
    for pair in candidate_rule:
        for indexed_attr in pair:
            # Convert IndexedAttribute to Attribute for readable representation
            attribute = mapper.indexed_attribute_to_attribute(indexed_attr)
            attr_name = attribute.name
            # Determine the variable assigned to this indexed attribute
            variable = variable_assignment[indexed_attr]
            if (
                indexed_attr.i,
                indexed_attr.j,
            ) not in attr_by_table_occurrence:
                attr_by_table_occurrence[(indexed_attr.i, indexed_attr.j)] = []
            attr_str = f"{attr_name}={variable}"
            if (
                attr_str
                not in attr_by_table_occurrence[(indexed_attr.i, indexed_attr.j)]
            ):
                attr_by_table_occurrence[(indexed_attr.i, indexed_attr.j)].append(
                    attr_str
                )
    for (
        table_occurrence,
        attr_list,
    ) in attr_by_table_occurrence.items():
        # Convert IndexedAttribute to Attribute for readable representation
        table = mapper.index_to_table_name[table_occurrence[0]]
        attr_list = ", ".join(attr_list)
        predicate = f"{table}_{table_occurrence[1]}({attr_list})"
        # Append the variable-attribute pair to the appropriate predicate part
        if table_occurrence in body:
            body_predicates.append(predicate)
        else:
            head_predicates.append(predicate)

    # Combine predicate components into strings
    psi = " ∧ ".join(body_predicates)
    phi = " ∧ ".join(head_predicates)
    return psi, phi


def calculate_support(
    candidate_rule: CandidateRule,
    body: set[TableOccurrence],
    head: set[TableOccurrence],
    db_inspector: AlchemyUtility,
    mapper: AttributeMapper,
    total_tuples: int = None,
) -> float:
    """
    Calculate the support of a candidate rule.
    :param candidate_rule: The candidate rule for which to calculate support.
    :param body: The body part of the candidate rule for which to calculate support.
    :param db_inspector: An instance of a class that provides database inspection functionalities.
    :param mapper: An instance of AttributeMapper for mapping indexed attributes to actual database attributes.
    :return: The support value as a float.
    """
    cr_chains = CandidateRuleChains(candidate_rule).cr_chains

    x_chains = CandidateRuleChains(candidate_rule).get_x_chains(
        body, head, mapper, select_body=True
    )

    #if total_tuples == 0:
    #    return 0

    support_condition: list[tuple[str, int, str, str, int, str]] = []
    # First, add the constraints from the body
    for jia in candidate_rule:
        attr1, attr2 = jia
        if (attr1.i, attr1.j) in body or (
            attr2.i,
            attr2.j,
        ) in body:
            support_condition.append(
                (
                    mapper.indexed_attribute_to_attribute(attr2).table,
                    attr2.j,
                    mapper.indexed_attribute_to_attribute(attr2).name,
                    mapper.indexed_attribute_to_attribute(attr1).table,
                    attr1.j,
                    mapper.indexed_attribute_to_attribute(attr1).name,
                )
            )
    # add other constraints for each respective chain in the cr_chains
    for jia in candidate_rule:
        for attr11 in jia:
            for chain in cr_chains:
                if attr11 in chain:
                    for attr22 in chain:
                        if (
                                attr11 != attr22
                                and attr22 not in jia
                                and (attr11.i, attr11.j) in body
                                and (attr22.i, attr22.j) in body
                        ):
                            support_condition.append(
                                (
                                    mapper.indexed_attribute_to_attribute(attr22).table,
                                    attr22.j,
                                    mapper.indexed_attribute_to_attribute(attr22).name,
                                    mapper.indexed_attribute_to_attribute(attr11).table,
                                    attr11.j,
                                    mapper.indexed_attribute_to_attribute(attr11).name,
                                )
                            )
    support_condition = list(set(support_condition))
    is_body_tuples_emtpy = db_inspector.check_threshold(
        support_condition, count_over=x_chains, flag="support", disjoint_semantics=APPLY_DISJOINT, threshold=0
    )
    if not  bool(is_body_tuples_emtpy):
        return 0
    total_tuples_satisfying_body = db_inspector.get_join_row_count(
        support_condition, count_over=x_chains, flag="support", disjoint_semantics=APPLY_DISJOINT
    )
    if total_tuples_satisfying_body == 0 :
        return 0
    support = total_tuples / total_tuples_satisfying_body
    return support


def calculate_confidence(
    candidate_rule: CandidateRule,
    body: set[TableOccurrence],
    head: set[TableOccurrence],
    db_inspector: AlchemyUtility,
    mapper: AttributeMapper,
    total_tuples: int = None,

) -> float:
    """
    Calculate the confidence of a candidate rule.

    :param candidate_rule: The candidate rule for which to calculate confidence.
    :param head: The head part of the candidate rule for which to calculate confidence.
    :param db_inspector: An instance of a class that provides database inspection functionalities.
    :param mapper: An instance of AttributeMapper for mapping indexed attributes to actual database attributes.
    :return: The confidence value as a float.
    """
    # total_tuples = prediction(candidate_rule, mapper, db_inspector, body, head)
    x_chains = CandidateRuleChains(candidate_rule).get_x_chains(
        body, head, mapper, select_head=True
    )
    cr_chains = CandidateRuleChains(candidate_rule).cr_chains

    #confidence_conditions: list[tuple[str, int, str, str, int, str]] = []
    # add constraints in head
    head_conditions = []
    for jia in candidate_rule:
        attr1, attr2 = jia
        if (attr1.i, attr1.j) in head or (
            attr2.i,
            attr2.j,
        ) in head:
            head_conditions.append(
                (
                    mapper.indexed_attribute_to_attribute(attr2).table,
                    attr2.j,
                    mapper.indexed_attribute_to_attribute(attr2).name,
                    mapper.indexed_attribute_to_attribute(attr1).table,
                    attr1.j,
                    mapper.indexed_attribute_to_attribute(attr1).name,
                )
            )
    for jia in candidate_rule:
        for attr11 in jia:
            for chain in cr_chains:
                if attr11 in chain:
                    for attr22 in chain:
                        if (
                                attr11 != attr22
                                and attr22 not in jia
                                and (attr11.i, attr11.j) in head
                                and (attr22.i, attr22.j) in head
                        ):
                            head_conditions.append(
                                (
                                    mapper.indexed_attribute_to_attribute(attr22).table,
                                    attr22.j,
                                    mapper.indexed_attribute_to_attribute(attr22).name,
                                    mapper.indexed_attribute_to_attribute(attr11).table,
                                    attr11.j,
                                    mapper.indexed_attribute_to_attribute(attr11).name,
                                )
                            )
    is_body_tuples_emtpy = db_inspector.check_threshold(
        head_conditions, count_over=x_chains, flag="head", disjoint_semantics=APPLY_DISJOINT, threshold=0
    )
    if not  bool(is_body_tuples_emtpy):
        return 0

    total_tuples_satisfying_head = db_inspector.get_join_row_count(
        head_conditions, count_over=x_chains, flag="head", disjoint_semantics=APPLY_DISJOINT
    )
    if total_tuples_satisfying_head == 0:
        return 0
    support = total_tuples / total_tuples_satisfying_head
    return support

def next_node_test(
    candidate_rule: CandidateRule,
    next_node: JoinableIndexedAttributes,
    visited: set[JoinableIndexedAttributes],
    max_table: int = 10,
    max_vars: int = 10,
) -> bool:
    """
    This function checks if the next node can be added to the candidate rule.
    It performs three checks:
    1. If the next node is already visited, it cannot be added.
    2. If the table occurrences are not consecutive after adding the next node, it cannot be added.
    3. If the candidate rule is not minimal after adding the next node, it cannot be added.

    :param candidate_rule: A list of JoinableIndexedAttributes instances representing the current candidate rule.
    :param next_node: The next JoinableIndexedAttributes instance to be added to the candidate rule.
    :param visited: A set of JoinableIndexedAttributes instances that have been visited.
    :return: A boolean value indicating whether the next node can be added to the candidate rule.
             If all checks pass, the function returns True, meaning the next node can be added.
             If any check fails, the function returns False, meaning the next node cannot be added.
    """
    if next_node in visited:
        return False
    if not check_table_occurrences(candidate_rule, next_node):
        return False
    if not check_minimal_candidate_rule(candidate_rule, next_node):
        return False
    if not check_max_table(candidate_rule, next_node, max_table):
        return False
    if not check_max_vars(candidate_rule, next_node, max_vars):
        return False
    return True
def is_start_node(
    candidate_rule: CandidateRule) -> bool:
    """
    Check if the candidate rule is a start node.
    """
    # 
    return len(candidate_rule) == 1

def check_table_occurrences(
    candidate_rule: set[TableOccurrence], next_node: JoinableIndexedAttributes
) -> bool:
    """
    Check if the table occurrences are consecutive.
    :param tables_occurrences: Set of tuples representing table occurrences (i, j).
    :param next_node: next node to add to the candidate rule
    :return: True if the table occurrences are consecutive, False otherwise.
    """
    test_candidate_rule = copy.deepcopy(candidate_rule)
    test_candidate_rule.append(next_node)
    table_occurrences = extract_table_occurrences(test_candidate_rule)
    test_candidate_rule.pop()
    table_occurrences = sorted(list(table_occurrences))
    tables_occurrences_dict = {}
    for table_occur in table_occurrences:
        if table_occur[0] not in tables_occurrences_dict.keys():
            tables_occurrences_dict[table_occur[0]] = []
        tables_occurrences_dict[table_occur[0]].append(table_occur[1])
    for table_index in tables_occurrences_dict:
        tmp_array = sorted(tables_occurrences_dict[table_index])
        test_array = list(range(0, tmp_array[-1] + 1))
        if len(set(tables_occurrences_dict[table_index])) != len(test_array):
            return False
    return True

def check_minimal_candidate_rule(
    candidate_rule: set[TableOccurrence], next_node: JoinableIndexedAttributes
) -> bool:
    """
    This function checks if the candidate rule is minimal after adding the next node.
    A candidate rule is minimal if it cannot be made smaller by removing any of its elements while preserving its meaning.
    The function performs the following steps:
    1. It creates a copy of the candidate rule and adds the next node to it.
    2. It finds the chains of the candidate rule.
    3. It builds a minimal candidate rule from the chains.
    4. It compares the minimal candidate rule with the test candidate rule. If they are not the same, the function returns False, meaning the candidate rule is not minimal.
    5. If the minimal candidate rule and the test candidate rule are the same, the function returns True, meaning the candidate rule is minimal.

    :param candidate_rule: A set of table occurrences representing the current candidate rule.
    :param next_node: The next JoinableIndexedAttributes instance to be added to the candidate rule.
    :return: A boolean value indicating whether the candidate rule is minimal.
             If the candidate rule is minimal, the function returns True.
             If the candidate rule is not minimal, the function returns False.
    """
    test_candidate_rule = copy.deepcopy(candidate_rule)
    test_candidate_rule.append(next_node)
    cr_chains = CandidateRuleChains(test_candidate_rule).cr_chains
    min_candidate_rule = []
    for chain in cr_chains:
        for jia in build_minimal_chain(chain):
            min_candidate_rule.append(jia)
    if min_candidate_rule != test_candidate_rule:
        return False
    return True


def check_max_table(
    candidate_rule: set[TableOccurrence],
    next_node: JoinableIndexedAttributes,
    max_table: int,
):
    test_candidate_rule = copy.deepcopy(candidate_rule)
    test_candidate_rule.append(next_node)
    tables = set()
    for jia in test_candidate_rule:
        for attr in jia:
            tables.add(f"{attr.i}_{attr.j}")
    return len(tables) <= max_table


def check_max_vars(
    candidate_rule: set[TableOccurrence],
    next_node: JoinableIndexedAttributes,
    max_vars: int,
):
    return len(candidate_rule) + 1 <= max_vars

def build_minimal_chain(chain: set[JoinableIndexedAttributes]):
    """
    :param chain: set of jia
    :return: the minimal associated chain
    """
    # 1. find minimal index attribute
    min_ia = chain[0]
    for ia in chain[1:]:
        if ia < min_ia:
            min_ia = ia
    # 2. generate the star pattern
    for ia in chain:
        if min_ia is not ia:
            yield JoinableIndexedAttributes(min_ia, ia)


def duplicate_test(tgds):
    """
    Args:
        tgds: list of tgds as strings
    Returns:
        - number of duplicate tgds (strings)
        - outputs a debug message in the console
    """
    duplicate_rules = len(tgds) - len(set(tgds))
    if duplicate_rules > 0:
        raise ValueError(f"Duplicate rules found: {duplicate_rules}")
    return duplicate_rules
from mahilda.utils.rules import Rule
import json

import re
from mahilda.utils.rules import Predicate, TGDRule

import re
import random
def str_to_predicate(relation_str):
    relation_pattern = r"\s*(\w+)\((.*?)\)\s*"
    relation_match = re.match(relation_pattern, relation_str)
    if relation_match:
        relation, assignments_str = relation_match.groups()
        assignments = assignments_str.split(", ")
        predicates = []
        random_int = str(random.randint(0, 10000))
        for assignment in assignments:
            var, idx = assignment.split("=")
            relation_id = relation.split("_")[-1].lower()
            relation_sep = "___sep___"
            relation_name = "".join(relation.split("_")[:-1]).lower()
            relation_clean = f"{relation_name}{relation_sep}{var}".lower()
            relation_table = "_".join(relation.split("_")[:-1]).lower()
            variable = f"t{relation_table}{random_int}"

            predicates.append(
                Predicate(
                    variable1=variable, relation=relation_clean, variable2=idx
                )
            )
        return predicates
    else:
        print(f"No match for relation string: {relation_str}")
        return []
def str_to_tgd(tgd_str,support, confidence):
    # Regular expression pattern to match the TGD format
    pattern = r"∀ (.*): (.*?) ⇒ (∃.*:)?(.*?)$"
    match = re.match(pattern, tgd_str)
    # print("match",match)
    if match:
        variables_str, body_str, variables_head_str, head_str = match.groups()
        body_predicates = []
        for split in body_str.split(" \u2227 "):
            for pred in str_to_predicate(split):
                body_predicates.append(pred)
        body = tuple(body_predicates)
        head_predicates = []
        for split in head_str.split(" \u2227 "):
            for pred in str_to_predicate(split):
                head_predicates.append(pred)
        head = tuple(head_predicates)
        # Convert the body and head strings into Predicates
        # body = tuple(map(str_to_predicate, body_str.split(" ^ ")))
        # head = tuple(map(str_to_predicate, head_str.split(" ^ ")))
        # Create and return the TGDRule
        return TGDRule(body=body, head=head,display=tgd_str,accuracy=support, confidence=confidence)

    else:
        raise ValueError(f"Invalid TGD string format: {tgd_str}")
