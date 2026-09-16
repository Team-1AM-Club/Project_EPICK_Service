"""Explicit C01 cache adapter. Use a dedicated database for this wire version."""

import json

from ..restriction.w4 import W4Cache as BaseCache, request
from .contracts import DeliveryResponse, Signal, Status, VERSION
from ..models import StructureResponse, ProcessingStatus
from ..requirements import validate_requirement_candidate


class W4Cache(BaseCache):
    signal_model = Signal
    status_model = Status

    def __init__(self, path):
        super().__init__(path)
        for (payload,) in self.db.execute("SELECT payload FROM state"):
            if json.loads(payload).get("schema_version") != VERSION:
                self.db.close()
                raise ValueError("CACHE_DATABASE_VERSION_MISMATCH")

    def put(self, source, generation, payload):
        return super().put(source.lower(), generation, payload)

    def get(self, source, current_status):
        return super().get(source.lower(), current_status)

    def put_knowledge(self, source, generation, payload):
        """Bind an existing W3 StructureResponse to one acknowledged Source generation.

        This validates identity and references, not the truth of claims. Consumers
        must still honor individual verification/usage status and W1 authorization.
        """
        source = source.lower()
        response = StructureResponse.model_validate_json(json.dumps(payload))
        if response.status not in {ProcessingStatus.COMPLETED, ProcessingStatus.LIMITED}:
            raise ValueError("KNOWLEDGE_NOT_COMPLETED")
        bundle = response.bundle
        ids = [e.evidence_id for e in bundle.evidences]
        if (
            not ids
            or len(ids) != len(set(ids))
            or any(
                not set(candidate.evidence_ids) <= set(ids)
                for candidate in (*bundle.claims, *bundle.requirements)
            )
        ):
            raise ValueError("KNOWLEDGE_EVIDENCE_REFERENCE_INVALID")
        value = response.model_dump(mode="json")
        for requirement in bundle.requirements:
            validate_requirement_candidate(requirement)

        def references(node):
            if isinstance(node, dict):
                for key, child in node.items():
                    if key == "source_ref" and child is not None:
                        yield child
                    else:
                        yield from references(child)
            elif isinstance(node, list):
                for child in node:
                    yield from references(child)

        with self.transaction():
            row = self.db.execute("SELECT payload FROM state WHERE source=?", (source,)).fetchone()
            if not row:
                raise ValueError("KNOWLEDGE_SIGNAL_REQUIRED")
            signal = self.signal_model.model_validate_json(row[0])
            if signal.index_key is None or any(
                ref["source_id"] != source
                or ref["source_version_id"] != signal.index_key.source_version_id
                for ref in references(value)
            ):
                raise ValueError("KNOWLEDGE_SOURCE_VERSION_MISMATCH")
            self.put(source, generation, value)


def drain(base, token, cache):
    count = 0
    for signal in request(base, token, "/c01/v1/signals")["signals"]:
        if cache.apply(signal) == "CONFLICT":
            raise ValueError("SIGNAL_CONFLICT_OPERATOR_REQUIRED")
        DeliveryResponse.model_validate(
            request(base, token, "/c01/v1/signals/ack", {"signal_id": signal["signal_id"]})
        )
        count += 1
    return count
