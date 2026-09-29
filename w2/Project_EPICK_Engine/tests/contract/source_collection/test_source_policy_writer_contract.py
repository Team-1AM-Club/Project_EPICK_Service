"""Keep SourcePolicyDecision writes behind the shared Source row lock boundary."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[3] / "src/epick_engine/source_collection"
PERSISTENCE = SOURCE_ROOT / "persistence.py"
ALLOWED_WRITER = "append_source_policy_decision"


def _annotation_names(annotation: ast.expr | None) -> set[str]:
    if annotation is None:
        return set()
    return {node.id for node in ast.walk(annotation) if isinstance(node, ast.Name)}


def _policy_variables(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    variables = {
        argument.arg
        for argument in (*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs)
        if "SourcePolicyDecision" in _annotation_names(argument.annotation)
    }
    for node in ast.walk(function):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if "SourcePolicyDecision" in _annotation_names(node.annotation):
                variables.add(node.target.id)
        if not isinstance(node, (ast.Assign, ast.NamedExpr)):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "SourcePolicyDecision"
        ):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        variables.update(target.id for target in targets if isinstance(target, ast.Name))
    return variables


def _writer_order_violations(
    path: Path,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
    lock_positions = [
        (call.lineno, call.col_offset)
        for call in calls
        if isinstance(call.func, ast.Name) and call.func.id == "lock_source_policy_scope"
    ]
    add_positions = [
        (call.lineno, call.col_offset)
        for call in calls
        if isinstance(call.func, ast.Attribute)
        and call.func.attr == "add"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "session"
    ]
    flush_positions = [
        (call.lineno, call.col_offset)
        for call in calls
        if isinstance(call.func, ast.Attribute)
        and call.func.attr == "flush"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "session"
    ]
    has_locked_write_order = (
        len(lock_positions) == 1
        and bool(add_positions)
        and bool(flush_positions)
        and lock_positions[0] < min(add_positions)
        and max(add_positions) < min(flush_positions)
    )
    if has_locked_write_order:
        return []
    first_position = min(
        (*lock_positions, *add_positions, *flush_positions),
        default=(function.lineno, function.col_offset),
    )
    return [f"{path.name}:{function.name}:{first_position[0]}:order"]


def _direct_policy_writes(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    allowed_writer = None
    if path.resolve() == PERSISTENCE.resolve():
        allowed_writer = next(
            (
                node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == ALLOWED_WRITER
            ),
            None,
        )
    for function in (
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        if function is allowed_writer:
            violations.extend(_writer_order_violations(path, function))
            continue
        policy_variables = _policy_variables(function)
        writes_policy = False
        for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
            if not isinstance(call.func, ast.Attribute):
                continue
            if call.func.attr in {"add", "add_all"}:
                writes_policy = any(
                    (isinstance(node, ast.Name) and node.id in policy_variables)
                    or (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "SourcePolicyDecision"
                    )
                    for argument in call.args
                    for node in ast.walk(argument)
                )
                if writes_policy:
                    violations.append(f"{path.name}:{function.name}:{call.lineno}:add")
            elif call.func.attr == "flush" and writes_policy:
                violations.append(f"{path.name}:{function.name}:{call.lineno}:flush")
    return violations


def test_source_policy_writer_boundary_locks_before_add_and_flush() -> None:
    tree = ast.parse(PERSISTENCE.read_text(encoding="utf-8"), filename=str(PERSISTENCE))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "lock_source_policy_scope" in functions
    assert ALLOWED_WRITER in functions

    lock_calls = [
        call
        for call in ast.walk(functions["lock_source_policy_scope"])
        if isinstance(call, ast.Call)
    ]
    assert any(
        isinstance(call.func, ast.Attribute) and call.func.attr == "with_for_update"
        for call in lock_calls
    )

    assert _writer_order_violations(PERSISTENCE, functions[ALLOWED_WRITER]) == []


def test_production_source_policy_writes_do_not_bypass_locked_boundary() -> None:
    violations = [
        violation for path in SOURCE_ROOT.rglob("*.py") for violation in _direct_policy_writes(path)
    ]
    assert violations == []


def test_policy_writer_audit_allows_construction_but_rejects_direct_persistence(
    tmp_path: Path,
) -> None:
    transient = tmp_path / "transient.py"
    transient.write_text(
        "def build():\n    return SourcePolicyDecision()\n",
        encoding="utf-8",
    )
    assert _direct_policy_writes(transient) == []

    bypass = tmp_path / "bypass.py"
    bypass.write_text(
        "def write(session, decision: SourcePolicyDecision):\n"
        "    session.add(decision)\n"
        "    session.flush()\n",
        encoding="utf-8",
    )
    assert _direct_policy_writes(bypass) == [
        "bypass.py:write:2:add",
        "bypass.py:write:3:flush",
    ]


def test_policy_writer_audit_rejects_same_named_writer_outside_persistence(
    tmp_path: Path,
) -> None:
    same_named_writer = tmp_path / "same_named_writer.py"
    same_named_writer.write_text(
        "def append_source_policy_decision(session, decision: SourcePolicyDecision):\n"
        "    session.add(decision)\n"
        "    session.flush()\n",
        encoding="utf-8",
    )

    assert _direct_policy_writes(same_named_writer) == [
        "same_named_writer.py:append_source_policy_decision:2:add",
        "same_named_writer.py:append_source_policy_decision:3:flush",
    ]


def test_policy_writer_audit_rejects_add_before_lock_in_allowed_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed_writer = tmp_path / "persistence.py"
    allowed_writer.write_text(
        "def append_source_policy_decision(session, decision: SourcePolicyDecision):\n"
        "    session.add(decision)\n"
        "    lock_source_policy_scope(session, decision.source_id)\n"
        "    session.flush()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "PERSISTENCE", allowed_writer)

    assert _direct_policy_writes(allowed_writer) == [
        "persistence.py:append_source_policy_decision:2:order",
    ]


def test_source_policy_scope_refreshes_the_locked_source_row() -> None:
    """FINALIZE and policy append must share a fresh Source-row serialization boundary."""

    module = ast.parse(PERSISTENCE.read_text(encoding="utf-8"))
    scope = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "lock_source_policy_scope"
    )

    def has_source_lock_and_fresh_read(call: ast.Call) -> bool:
        nested = list(ast.walk(call))
        has_source = any(isinstance(node, ast.Name) and node.id == "Source" for node in nested)
        has_for_update = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "with_for_update"
            for node in nested
        )
        has_fresh_read = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execution_options"
            and any(
                keyword.arg == "populate_existing"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in node.keywords
            )
            for node in nested
        )
        return has_source and has_for_update and has_fresh_read

    assert any(
        has_source_lock_and_fresh_read(call)
        for call in ast.walk(scope)
        if isinstance(call, ast.Call)
    )
