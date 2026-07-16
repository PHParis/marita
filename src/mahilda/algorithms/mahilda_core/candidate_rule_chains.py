from mahilda.algorithms.mahilda_core.constraint_graph import (
    AttributeMapper,
    IndexedAttribute,
    JoinableIndexedAttributes,
)

TableOccurrence = tuple[int, int]
CandidateRule = list[JoinableIndexedAttributes]


class CandidateRuleChains:
    """
    This class is used to find and manage chains of candidate rules.
    A candidate rule is a list of JoinableIndexedAttributes.
    """

    def __init__(self, candidate_rule: CandidateRule = None):
        """
        Initialize the CandidateRuleChains object.
        :param candidate_rule: The candidate rule to be used for finding chains.
        """
        self.cr = candidate_rule
        self.cr_chains = self.find_candidate_rule_chains(candidate_rule)

    def get_x_chains(
        self,
        body: set[TableOccurrence],
        head: set[TableOccurrence],
        mapper: AttributeMapper,
        select_body: bool = False,
        select_head: bool = False,
    ) -> list[list[tuple[str, int, str]]]:
        """
        Get chains of x from the candidate rule chains.
        :param body: The body part of the candidate rule.
        :param head: The head part of the candidate rule.
        :param mapper: An instance of AttributeMapper for attribute mapping.
        :param select_body: A flag to indicate whether to select body.
        :param select_head: A flag to indicate whether to select head.
        :return: A list of x chains.
        """
        x_chains = []
        for chain in self.cr_chains:
            body_check = False
            head_check = False
            for ia in chain:
                table_occurrence_ia = (ia.i, ia.j)
                if table_occurrence_ia in body:
                    body_check = True
                if table_occurrence_ia in head:
                    head_check = True
            if body_check and head_check:
                attribute_class = []
                for indexed_attr1 in chain:
                    attr1 = mapper.indexed_attribute_to_attribute(indexed_attr1)
                    test_attribute = (indexed_attr1.i, indexed_attr1.j)
                    if select_body and test_attribute not in body:
                        continue
                    if select_head and test_attribute not in head:
                        continue
                    attribute_class.append((attr1.table, indexed_attr1.j, attr1.name))
                x_chains.append(attribute_class)
        return x_chains

    def find_candidate_rule_chains(
        self,
        candidate_rule: CandidateRule,
    ) -> list[list[IndexedAttribute]]:
        """Return the deterministic transitive closure of attribute equalities."""
        if not candidate_rule:
            return []

        parent: dict[IndexedAttribute, IndexedAttribute] = {}

        def find(attribute: IndexedAttribute) -> IndexedAttribute:
            parent.setdefault(attribute, attribute)
            if parent[attribute] != attribute:
                parent[attribute] = find(parent[attribute])
            return parent[attribute]

        def union(left: IndexedAttribute, right: IndexedAttribute) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root == right_root:
                return
            if right_root < left_root:
                left_root, right_root = right_root, left_root
            parent[right_root] = left_root

        for left, right in candidate_rule:
            union(left, right)

        classes: dict[IndexedAttribute, set[IndexedAttribute]] = {}
        for attribute in parent:
            classes.setdefault(find(attribute), set()).add(attribute)
        return [sorted(classes[root]) for root in sorted(classes)]

    def add_to_chain(
        self,
        chains: list[set[JoinableIndexedAttributes]],
        pair: JoinableIndexedAttributes,
        candidate_rule: CandidateRule,
    ):
        """
        Add a pair to a chain.
        :param chains: The list of chains.
        :param pair: The pair to be added to a chain.
        :param candidate_rule: The candidate rule to be used for finding chains.
        """
        # Check if the pair belongs to an existing chains
        for chain in chains:
            if pair in chain:
                return  # Pair already in a chain
            # Check connectivity with any member of the chain to decide
            for member in chain:
                if self.is_directly_connected(pair, member, candidate_rule):
                    chain.add(pair)
                    return
        # If no chain is found, create a new equivalence class
        chains.append({pair})

    def is_directly_connected(
        self,
        pair1: JoinableIndexedAttributes,
        pair2: JoinableIndexedAttributes,
        candidate_rule: CandidateRule,
    ) -> bool:
        """
        This method checks if two pairs of JoinableIndexedAttributes are directly connected.
        A direct connection is defined as any attribute in pair1 being the same as any attribute in pair2.
        If no direct connection is found, the method checks for a connection through a sequence in the candidate rule.
        If the intermediate pair connects to both pair1 and pair2, the method returns True.

        :param pair1: The first pair of JoinableIndexedAttributes.
        :param pair2: The second pair of JoinableIndexedAttributes.
        :param candidate_rule: The candidate rule to be used for finding chains.
        :return: True if pair1 and pair2 are directly connected or connected through a sequence in the candidate rule, False otherwise.
        """
        # Direct connection by definition would mean any attribute in pair1 is the same as any in pair2
        # Unpacking pairs for clarity
        a1, a1_prime = pair1
        a2, a2_prime = pair2
        # Check direct connection by comparing attributes
        if a1 == a2 or a1 == a2_prime or a1_prime == a2 or a1_prime == a2_prime:
            return True
        # Check for a connection through a sequence in the candidate rule
        for intermediate_pair in candidate_rule:
            if pair1 != intermediate_pair and pair2 != intermediate_pair:
                inter_a, inter_a_prime = intermediate_pair
                # If the intermediate pair connects to both, return True
                if (a1 == inter_a or a1_prime == inter_a_prime) and (
                    inter_a == a2 or inter_a_prime == a2_prime
                ):
                    return True
        return False
