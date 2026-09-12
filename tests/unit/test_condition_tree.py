import pytest

from w3_knowledge.models import ConditionNode, ConditionOperator
from w3_knowledge.requirements import (
    ConditionTreeError,
    validate_condition_tree,
    validate_requirement_candidate,
)
from tests.support.factories import requirement_candidate


def test_condition_tree_requires_reachable_children() -> None:
    nodes = requirement_candidate().condition_nodes + (
        ConditionNode(node_id="orphan-node", operator=ConditionOperator.LEAF, text="orphan"),
    )
    with pytest.raises(ConditionTreeError, match="도달"):
        validate_condition_tree(nodes, "condition-root")


def test_condition_tree_rejects_node_with_multiple_parents() -> None:
    nodes = (
        ConditionNode(
            node_id="root-node",
            operator=ConditionOperator.AND,
            text="root",
            children=("left-node", "right-node"),
        ),
        ConditionNode(
            node_id="left-node",
            operator=ConditionOperator.AND,
            text="left",
            children=("shared-node", "left-leaf"),
        ),
        ConditionNode(
            node_id="right-node",
            operator=ConditionOperator.AND,
            text="right",
            children=("shared-node", "right-leaf"),
        ),
        ConditionNode(node_id="shared-node", operator=ConditionOperator.LEAF, text="shared"),
        ConditionNode(node_id="left-leaf", operator=ConditionOperator.LEAF, text="left leaf"),
        ConditionNode(node_id="right-leaf", operator=ConditionOperator.LEAF, text="right leaf"),
    )

    with pytest.raises(ConditionTreeError):
        validate_condition_tree(nodes, "root-node")


def test_condition_tree_rejects_unary_and_or_branch() -> None:
    nodes = (
        ConditionNode(
            node_id="root-node", operator=ConditionOperator.OR, text="root", children=("leaf-node",)
        ),
        ConditionNode(node_id="leaf-node", operator=ConditionOperator.LEAF, text="leaf"),
    )

    with pytest.raises(ConditionTreeError):
        validate_condition_tree(nodes, "root-node")


def test_requirement_candidate_rejects_condition_evidence_outside_requirement_evidence() -> None:
    candidate = requirement_candidate().model_copy(
        update={
            "condition_nodes": (
                requirement_candidate()
                .condition_nodes[0]
                .model_copy(update={"evidence_id": "evidence-not-on-requirement"}),
                *requirement_candidate().condition_nodes[1:],
            )
        }
    )

    with pytest.raises(ConditionTreeError):
        validate_requirement_candidate(candidate)


def test_condition_tree_rejects_cycle() -> None:
    nodes = (
        ConditionNode(
            node_id="cycle-one", operator=ConditionOperator.AND, text="one", children=("cycle-two",)
        ),
        ConditionNode(
            node_id="cycle-two", operator=ConditionOperator.OR, text="two", children=("cycle-one",)
        ),
    )
    with pytest.raises(ConditionTreeError, match="순환"):
        validate_condition_tree(nodes, "cycle-one")
