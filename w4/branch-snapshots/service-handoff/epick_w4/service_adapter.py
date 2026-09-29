"""Backend-owned context and policy boundary; request bodies contain no Episode data."""

from copy import deepcopy
import json
from pathlib import Path
from typing import Literal, Protocol

from pydantic import ValidationError

from .company_context import consume_company
from .detailed_recommendation import prepare_request, recommend_from_raw
from .handoff_contract import ServerContext, ServiceOutput, ServiceRequest
from .llm_contract import LLMError
from .synthetic_policy import content_hash

Action = Literal["PROCESS", "SEND_TO_PROVIDER", "RETURN_TO_CALLER"]


class Backend(Protocol):
    def load_context(self, *, user_id: str, project_id: str) -> dict | None:
        """Atomic user-scoped read. Forbidden and absent projects both return None."""
        ...

    def authorize(self, *, user_id: str, project_id: str, context_version: str,
                  action: Action, provider: str | None, model: str | None) -> bool:
        """Check current consent/access/deletion against this exact revision."""
        ...


class ServiceError(RuntimeError):
    def __init__(self, code, status):
        self.code, self.status = code, status
        super().__init__(code)


def _load(backend, user_id, project_id):
    try:
        value = backend.load_context(user_id=user_id, project_id=project_id)
    except Exception:
        raise ServiceError("BACKEND_UNAVAILABLE", 503) from None
    if value is None:
        raise ServiceError("PROJECT_NOT_FOUND", 404)
    try:
        value = ServerContext.model_validate(deepcopy(value)).model_dump()
    except (ValidationError, TypeError, ValueError):
        raise ServiceError("BACKEND_CONTEXT_INVALID", 503) from None
    if value["project"] != {"owner_id": user_id, "project_id": project_id}:
        raise ServiceError("PROJECT_NOT_FOUND", 404)
    episodes, snapshot = value["episodes"], value["snapshot"]["episode_versions"]
    ids = [e["episode_id"] for e in episodes]
    if (len(ids) != len(set(ids)) or any(e["owner_id"] != user_id for e in episodes)
            or any(e["episode_id"] in value["excluded_episode_ids"] for e in episodes)
            or any(snapshot.get(e["episode_id"]) != e["version"] for e in episodes)):
        raise ServiceError("BACKEND_CONTEXT_INVALID", 503)
    return value


class _Guard:
    def __init__(self, backend, user_id, context):
        self.backend, self.user_id, self.context = backend, user_id, deepcopy(context)
        self.fingerprint = content_hash(context)

    def check(self, action, client=None):
        current = _load(self.backend, self.user_id, self.context["project"]["project_id"])
        if content_hash(current) != self.fingerprint:
            raise ServiceError("CONTEXT_CHANGED", 409)
        try:
            allowed = self.backend.authorize(
                user_id=self.user_id, project_id=self.context["project"]["project_id"],
                context_version=self.context["context_version"], action=action,
                provider=client.provider if client else None, model=client.model if client else None)
        except Exception:
            raise ServiceError("POLICY_SERVICE_UNAVAILABLE", 503) from None
        if allowed is not True:
            raise ServiceError("POLICY_DENIED", 403)


class _GuardedClient:
    def __init__(self, client, guard):
        # Evaluation clients retaining request/response text must not be used here.
        if getattr(client, "content_logging_enabled", None) is not False:
            raise ServiceError("MODEL_CONTENT_LOGGING_NOT_DISABLED", 503)
        if type(client.simulated) is not bool or not all(
                isinstance(v, str) and v.strip() for v in (client.provider, client.model)):
            raise ServiceError("MODEL_CONFIGURATION_INVALID", 503)
        self._client, self._guard = client, guard
        self.provider, self.model, self.simulated = client.provider, client.model, client.simulated

    def complete_json(self, **kwargs):
        self._guard.check("PROCESS")
        self._guard.check("SEND_TO_PROVIDER", self)
        return self._client.complete_json(**kwargs)


def execute_service(payload, *, user_id, backend, extraction_factory, judgment_factory):
    request = ServiceRequest.model_validate(payload).model_dump()
    context = _load(backend, user_id, request["project_id"])
    guard = _Guard(backend, user_id, context)
    guard.check("PROCESS")
    if context["data_kind"] != "SYNTHETIC" or context["company_knowledge"].get("data_kind") == "REAL":
        raise LLMError("LLM_REAL_DATA_NOT_ENABLED", "input")
    if context["question_scope_id"] != request["question"]["scope_id"]:
        raise ServiceError("PROJECT_QUESTION_SCOPE_MISMATCH", 422)
    company = consume_company(context["company_knowledge"], context["question_scope_id"])
    internal = {"schema_version": "w4-detailed-input/0.1", "request_id": request["request_id"],
                **{k: deepcopy(context[k]) for k in ("data_kind", "project", "snapshot", "episodes", "excluded_episode_ids")},
                "question": request["question"], "top_k": request["top_k"]}
    prepare_request(internal, user_id)
    try:
        extractor = _GuardedClient(extraction_factory(), guard)
        judge = _GuardedClient(judgment_factory(), guard)
    except ServiceError:
        raise
    except Exception:
        raise ServiceError("MODEL_CONFIGURATION_INVALID", 503) from None
    if not judge.simulated:
        allowlist = json.loads(Path(__file__).with_name("company_allowlist.json").read_text(encoding="utf-8"))
        if content_hash(list(company.criteria)) not in allowlist["criteria_sha256"]:
            raise LLMError("LLM_SYNTHETIC_SAMPLE_REQUIRED", "input")
    result = recommend_from_raw(internal, user_id=user_id, extraction_llm=extractor,
                                judgment_llm=judge, company_context=company)
    result["server_context_version"] = context["context_version"]
    try:
        result = ServiceOutput.model_validate(result).model_dump()
    except ValidationError:
        raise ServiceError("ENGINE_OUTPUT_INVALID", 502) from None
    guard.check("PROCESS")
    guard.check("RETURN_TO_CALLER")
    return result
