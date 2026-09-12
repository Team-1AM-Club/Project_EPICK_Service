"""명시 Requirement의 조건 트리를 검사한다."""

from __future__ import annotations

from .models import ConditionNode, RequirementCandidate


class ConditionTreeError(ValueError):
    pass


def validate_condition_tree(nodes: tuple[ConditionNode, ...], root_node_id: str) -> None:
    by_id = {node.node_id: node for node in nodes}
    if len(by_id) != len(nodes):
        raise ConditionTreeError("조건 node_id가 중복되었습니다.")
    if root_node_id not in by_id:
        raise ConditionTreeError("root_node_id가 존재하지 않습니다.")
    parent_counts = {node.node_id: 0 for node in nodes}
    for node in nodes:
        for child in node.children:
            if child in parent_counts:
                parent_counts[child] += 1
            if child not in by_id:
                raise ConditionTreeError(f"조건 자식 참조가 없습니다: {child}")
    visited: set[str] = set()
    active: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in active:
            raise ConditionTreeError("조건 트리에 순환이 있습니다.")
        if node_id in visited:
            return
        active.add(node_id)
        for child in by_id[node_id].children:
            visit(child)
        active.remove(node_id)
        visited.add(node_id)

    visit(root_node_id)
    if visited != set(by_id):
        raise ConditionTreeError("root에서 도달하지 못하는 조건 node가 있습니다.")

    if parent_counts[root_node_id] != 0:
        raise ConditionTreeError("루트 조건은 부모를 가질 수 없습니다.")
    if any(parent_counts[node_id] != 1 for node_id in by_id if node_id != root_node_id):
        raise ConditionTreeError("루트 외 조건은 정확히 하나의 부모를 가져야 합니다.")

    for node in nodes:
        if node.operator.value in {"AND", "OR"} and len(node.children) < 2:
            raise ConditionTreeError("AND/OR 조건은 둘 이상의 자식 조건이 필요합니다.")


def validate_requirement_candidate(candidate: RequirementCandidate) -> None:
    validate_condition_tree(candidate.condition_nodes, candidate.root_node_id)
    known_evidence_ids = set(candidate.evidence_ids)
    for node in candidate.condition_nodes:
        if node.evidence_id is not None and node.evidence_id not in known_evidence_ids:
            raise ConditionTreeError("조건 근거는 Requirement의 Evidence 참조에 포함되어야 합니다.")
    if not candidate.evidence_ids:
        raise ConditionTreeError("Requirement에는 원문 Evidence가 필요합니다.")
