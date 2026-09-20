"""Explicitly gated synthetic-only bootstrap for the T104 AWS acceptance run.

This module exercises the delivered W4 extraction, judgment, RunStore and
publication code without calling an external model.  It is not a production
model adapter and refuses to start unless the operator opts into the isolated
synthetic acceptance boundary.
"""

from __future__ import annotations

import os
from typing import ClassVar

from .w1_bridge import W1ExecutionAdapter


class SyntheticAcceptanceClient:
    """Deterministic no-network model seam used only by the T104 fixture."""

    provider = "epick-t104"
    model = "fixed-synthetic-v1"
    simulated = True
    content_logging_enabled = False
    generation_config: ClassVar[dict[str, object]] = {
        "fixture": "w1-w4-t104-v1",
        "network_calls": 0,
    }

    def complete_json(self, *, stage: str, system_prompt: str, payload: dict) -> dict:
        del system_prompt
        if stage == "extraction":
            return {
                "units": [
                    {
                        "unit_id": item["unit_id"],
                        "kinds": ["ACTION"],
                        "subject": "SELF",
                        "assertion": "AFFIRMED",
                        "issue": None,
                    }
                    for item in payload["source_units"]
                ]
            }
        if stage == "c01_question":
            return {
                "checks": [
                    {
                        "check_id": item["id"],
                        "status": "NOT_SHOWN",
                        "evidence_ids": [],
                    }
                    for item in payload["question"]["content_checks"]
                ]
            }
        if stage == "c01_claims":
            return {
                "claims": [
                    {"id": item["id"], "status": "NOT_SHOWN", "evidence_ids": []}
                    for item in payload["company_criteria"]
                ]
            }
        if stage == "c01_requirements":
            return {
                "requirement_checks": [
                    {
                        "id": item["id"],
                        "nodes": [
                            {
                                "node_id": node["node_id"],
                                "status": "NOT_SHOWN",
                                "evidence_ids": [],
                            }
                            for node in item["condition_nodes"]
                            if node["operator"] == "LEAF"
                        ],
                    }
                    for item in payload["company_criteria"]
                ]
            }
        raise ValueError("T104_SYNTHETIC_STAGE_NOT_ALLOWED")


class SyntheticAcceptanceC01Consumer:
    """No-cache guard; W1 remains authoritative for Source currentness."""

    @staticmethod
    def check_current(knowledge: dict) -> None:
        sources = knowledge.get("sources") if isinstance(knowledge, dict) else None
        if not isinstance(sources, list) or not sources:
            raise ValueError("T104_SYNTHETIC_SOURCE_CONTEXT_INVALID")
        for item in sources:
            signal = item.get("signal") if isinstance(item, dict) else None
            metadata = item.get("metadata") if isinstance(item, dict) else None
            if (
                not isinstance(signal, dict)
                or not isinstance(metadata, dict)
                or signal.get("usable") is not True
                or item.get("knowledge_generation") != signal.get("generation")
                or metadata.get("source_id") != signal.get("source_id")
                or metadata.get("index_key") != signal.get("index_key")
            ):
                raise ValueError("T104_SYNTHETIC_SOURCE_CONTEXT_INVALID")

    def get_result(self, owner: str, context: dict, request: dict) -> None:
        del owner, request
        self.check_current(context["company_knowledge"])

    def put_result(self, owner: str, context: dict, request: dict, result: dict) -> None:
        del owner, request, result
        self.check_current(context["company_knowledge"])

    @staticmethod
    def invalidate_scope(owner: str, project: str) -> None:
        del owner, project


def build_acceptance_adapter(*, store) -> W1ExecutionAdapter:
    """Build the W4 adapter only for an explicitly isolated synthetic run."""

    if os.environ.get("W4_RECOMMENDATION_SYNTHETIC_ACCEPTANCE") != "YES":
        raise RuntimeError("W4_RECOMMENDATION_SYNTHETIC_ACCEPTANCE must be YES")
    if os.environ.get("W4_RECOMMENDATION_REAL_DATA_ENABLED", "false").lower() != "false":
        raise RuntimeError("REAL W4 recommendation data is not approved")
    consumer = SyntheticAcceptanceC01Consumer()
    return W1ExecutionAdapter(
        store=store,
        extraction_factory=SyntheticAcceptanceClient,
        judgment_factory=SyntheticAcceptanceClient,
        c01_consumer=consumer,
    )
