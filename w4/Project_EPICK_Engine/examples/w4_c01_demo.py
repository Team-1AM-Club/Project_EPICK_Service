"""Explicit fiction for W4 C01 integration; no company facts or model scoring."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

from epick_w4.c01_adapter import status_of
from epick_w4.c01_contract import COMMIT, PROFILE
from examples.w4_service_demo import DemoBackend, DemoClient, read

ROOT = Path(__file__).resolve().parents[1]


def build_context(now=None, *, source_count=2):
    now = time.time() if now is None else now
    upstream = json.loads(
        (ROOT / "samples/c01/upstream/w4-consumer.json").read_text(encoding="utf-8")
    )
    context = read("server-context.synthetic.json")
    context["schema_version"] = "w4-server-context/0.2"
    context["context_version"] = "synthetic-c01-context-v1"
    company = {
        "schema_version": "w4-c01-knowledge/0.1-proposal",
        "profile": PROFILE,
        "upstream_commit": COMMIT,
        "knowledge_bundle_id": "synthetic-c01-bundle-v1",
        "data_kind": "SYNTHETIC",
        "as_of": "2026-09-17",
        "scope_id": context["question_scope_id"],
        "sources": [],
    }
    for index in range(source_count):

        def uid(number):
            return f"10000000-0000-4000-8000-{index * 100 + number:012d}"

        signal = deepcopy(upstream["ready"])
        signal.update(source_id=uid(5), signal_id=uid(25))
        signal["index_key"].update(source_version_id=uid(7), extraction_revision_id=uid(8))
        source_ref = {
            "source_id": uid(5),
            "source_version_id": uid(7),
            "source_kind": "synthetic_job_posting",
        }
        statements = [
            f"가상 자료 {index + 1}: 꾸준히 기술을 학습한 경험을 참고합니다.",
            f"가상 자료 {index + 1}: 배운 내용을 코드에 적용하고, 입력 검증 또는 문서 작성 경험이 있으면 우대합니다.",
        ]
        evidence = [
            {
                "evidence_id": f"synthetic-evidence-{i}",
                "artifact_id": "synthetic-document",
                "source_ref": source_ref,
                "excerpt": text,
                "native_locator": {
                    "locator_type": "external_reference",
                    "value": f"synthetic:c01:{index}:{i}",
                    "reproducible": True,
                },
                "observed_integrity": {
                    "algorithm": "sha256",
                    "digest": hashlib.sha256(text.encode()).hexdigest(),
                    "scope": "excerpt",
                    "reported_by": "W3_CURRENT",
                },
            }
            for i, text in enumerate(statements)
        ]
        verification = {"verification_status": "VERIFIED", "usage_status": "USABLE", "checks": []}
        knowledge = {
            "status": "COMPLETED",
            "errors": [],
            "bundle": {
                "mode": "SYNTHETIC",
                "purpose": "SYNTHETIC_ACCEPTANCE",
                "requirement_presence": "FOUND",
                "evidences": evidence,
                "claims": [
                    {
                        **verification,
                        "candidate_id": "synthetic-learning",
                        "statement": statements[0],
                        "evidence_ids": ["synthetic-evidence-0"],
                    }
                ],
                "requirements": [
                    {
                        **verification,
                        "candidate_id": "synthetic-application",
                        "original_text": statements[1],
                        "necessity": "PREFERRED",
                        "evidence_ids": ["synthetic-evidence-1"],
                        "root_node_id": "root",
                        "condition_nodes": [
                            {
                                "node_id": "root",
                                "operator": "AND",
                                "text": statements[1],
                                "children": ["apply", "choice"],
                                "same_experience_required": True,
                            },
                            {
                                "node_id": "apply",
                                "operator": "LEAF",
                                "text": "배운 내용을 코드에 적용",
                                "evidence_id": "synthetic-evidence-1",
                            },
                            {
                                "node_id": "choice",
                                "operator": "OR",
                                "text": "입력 검증 또는 문서 작성",
                                "children": ["validation", "documentation"],
                            },
                            {
                                "node_id": "validation",
                                "operator": "LEAF",
                                "text": "입력 검증 경험",
                                "evidence_id": "synthetic-evidence-1",
                            },
                            {
                                "node_id": "documentation",
                                "operator": "LEAF",
                                "text": "문서 작성 경험",
                                "evidence_id": "synthetic-evidence-1",
                            },
                        ],
                    }
                ],
                "source_reviews": [
                    {
                        "source_ref": source_ref,
                        "checks": [],
                        "limitations": [],
                        "process_status": "COMPLETED",
                    }
                ],
                "limitations": [],
            },
        }
        metadata = {
            "source_id": uid(5),
            "index_key": deepcopy(signal["index_key"]),
            "scope_id": company["scope_id"],
            "content_sha256": hashlib.sha256("\n".join(statements).encode()).hexdigest(),
            "published_at": "2026-09-01",
            "valid_from": "2026-09-01",
            "valid_to": "2026-12-31",
            "parse_status": "PARSED",
            "required_for_scope": index == 0,
            "expires_at": datetime.fromtimestamp(now + 1800, timezone.utc).isoformat(),
        }
        company["sources"].append(
            {
                "signal": signal,
                "knowledge_generation": signal["generation"],
                "knowledge": knowledge,
                "metadata": metadata,
            }
        )
    context["company_knowledge"] = company
    return context


class C01DemoBackend(DemoBackend):
    def __init__(self, context=None):
        super().__init__()
        self.context = context or build_context()


class C01DemoClient(DemoClient):
    def complete_json(self, *, stage, system_prompt, payload):
        if stage != "c01_company_details":
            return super().complete_json(stage=stage, system_prompt=system_prompt, payload=payload)
        after, mutate = self.after_call, self.mutate_response
        self.after_call = self.mutate_response = None
        try:
            response = super().complete_json(
                stage=stage, system_prompt=system_prompt, payload=payload
            )
        finally:
            self.after_call, self.mutate_response = after, mutate
        response["company_links"] = [r for r in response["company_links"] if r["kind"] == "CLAIM"]
        response["requirement_checks"] = []
        for criterion in payload["company_criteria"]:
            if criterion["kind"] != "REQUIREMENT":
                continue
            rows = []
            for node in criterion["upstream_record"]["condition_nodes"]:
                if node["operator"] != "LEAF":
                    continue
                supported = payload["episode_id"] == "v4-raw-study" and node["node_id"] in {
                    "apply",
                    "validation",
                }
                rows.append(
                    {
                        "node_id": node["node_id"],
                        "status": "SUPPORTED" if supported else "NOT_SHOWN",
                        "evidence_ids": ["u3"] if supported else [],
                    }
                )
            response["requirement_checks"].append({"id": criterion["id"], "nodes": rows})
        if mutate:
            mutate(payload, response)
        if after:
            after()
        return response


class FixtureTransport:
    """In-memory W3 counterpart for deterministic fault tests only."""

    def __init__(self, context):
        self.current = {
            b["signal"]["source_id"]: status_of(b["signal"])
            for b in context["company_knowledge"]["sources"]
        }
        self.pending = [deepcopy(b["signal"]) for b in context["company_knowledge"]["sources"]]
        self.acked = set()
        self.unavailable = False
        self.ack_failure = False
        self.lose_ack_response = False

    def signals(self):
        return {"signals": deepcopy(self.pending)}

    def status(self, source):
        if self.unavailable:
            raise OSError("synthetic status outage")
        return deepcopy(self.current[source])

    def ack(self, signal_id):
        if self.ack_failure:
            raise OSError("synthetic delivery failure")
        self.pending = [s for s in self.pending if s["signal_id"] != signal_id]
        self.acked.add(signal_id)
        if self.lose_ack_response:
            self.lose_ack_response = False
            raise OSError("synthetic lost response after ACK")
        return {"delivered": True}

    def change(self, source, *, reason="RESTRICTED", generation=None):
        status = deepcopy(self.current[source])
        status["generation"] = status["generation"] + 1 if generation is None else generation
        status.update(reason=reason, index_ack=reason == "READY")
        signal = {
            **status,
            "signal_id": str(uuid4()),
            "event_type": "w3.source.usability.changed",
            "usable": status["index_ack"],
        }
        self.current[source] = status
        self.pending.append(signal)
        return signal
