from __future__ import annotations

import json
from typing import Any

from app.main import create_app

FORBIDDEN_PUBLIC_FIELD_NAMES = {
    "ack_status",
    "aggregate_revision",
    "command_id",
    "event_cursor",
    "execution_fence",
    "gap_recovery",
    "index_generation",
    "index_key",
    "knowledge_index_ack",
    "lease_id",
    "outbox_message",
    "owner_deletion_epoch",
    "private_command",
    "private_result",
    "replay_cursor",
    "required_event_cursor",
    "required_restriction_revision",
    "required_revision",
    "restriction_revision",
    "source_event",
    "transport_event",
    "transport_payload",
    "w3_ack",
}


def _component_property_names(value: Any) -> set[str]:
    if isinstance(value, list):
        return set().union(*(_component_property_names(item) for item in value))
    if not isinstance(value, dict):
        return set()

    names = set(value.get("properties", {}))
    return names | set().union(*(_component_property_names(item) for item in value.values()))


def test_public_openapi_excludes_w2_w3_transport_and_private_execution_fields() -> None:
    schema = create_app().openapi()
    public_field_names = _component_property_names(schema["components"]["schemas"])

    assert FORBIDDEN_PUBLIC_FIELD_NAMES.isdisjoint(public_field_names)
    forbidden_path_fragments = ("/events", "/replay", "/restrictions", "/internal")
    assert all(
        all(fragment not in path for fragment in forbidden_path_fragments)
        for path in schema["paths"]
    )


def test_public_openapi_does_not_embed_w2_w3_contract_payload_versions() -> None:
    serialized = json.dumps(create_app().openapi(), sort_keys=True).lower()

    for private_or_transport_identifier in (
        "w2.collection.v1",
        "w2.source.v1",
        "w3-restriction",
        "knowledge_index_acks",
        "private-message-envelope",
    ):
        assert private_or_transport_identifier not in serialized
