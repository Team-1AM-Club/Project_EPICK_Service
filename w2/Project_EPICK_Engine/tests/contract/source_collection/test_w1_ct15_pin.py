"""Reproduce pinned W1 fixture incompatibility, without importing W1 services.

Passing these checks proves fail-closed detection, NOT joint CT15 readiness.
The original remote file is preserved; no fix-up of its exported command occurs.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from epick_engine.source_collection.contracts import CollectionCommand

PIN = Path(__file__).resolve().parents[2] / "fixtures/w1_ct15_pin"


def test_pinned_w1_source_matches_recorded_git_blob():
    manifest = json.loads((PIN / "manifest.json").read_text(encoding="utf-8"))
    data = (PIN / "w1_w2_ct15_harness.py.txt").read_bytes()
    blob = b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    assert hashlib.sha1(blob, usedforsecurity=False).hexdigest() == manifest["git_blob_sha"]


def test_w1_exported_command_fails_existing_w2_resume_policy_contract():
    tree = ast.parse((PIN / "w1_w2_ct15_harness.py.txt").read_text(encoding="utf-8"))
    binding = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Ct15CommandBinding"
    )
    # Execute only the fixed pure DTO, not the module imports, DB helpers or CLI.
    namespace = {"dataclass": dataclass, "UUID": UUID}
    exec(compile(ast.Module(body=[binding], type_ignores=[]), "pinned_ct15_dto", "exec"), namespace)
    dto = namespace["Ct15CommandBinding"](
        **{
            key: UUID(int=index)
            for index, key in enumerate(
                (
                    "owner_id",
                    "job_id",
                    "command_id",
                    "lease_id",
                    "source_id",
                    "company_id",
                    "source_link_id",
                ),
                start=1,
            )
        },
        execution_fence=1,
        owner_deletion_epoch=0,
    )
    command = dto.as_safe_dict()["w2_collection_command"]
    assert command["resume_stage"] == "fetch"
    assert command["policy_revision"] is None
    with pytest.raises(ValidationError, match="non-policy resume requires policy_revision"):
        CollectionCommand.model_validate(command)
