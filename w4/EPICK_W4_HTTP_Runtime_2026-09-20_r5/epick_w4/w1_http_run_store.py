"""Authenticated HTTP implementation of the delivered W1 RunStore protocol."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from .w1_bridge import BridgeError, EpisodeVersionBinding, RunBinding


class W1HttpRunStore:
    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str,
        service_principal: str,
        timeout_seconds: float = 30,
    ):
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("W1 private base URL must be HTTP(S)")
        if not bearer_token or not service_principal or timeout_seconds <= 0:
            raise ValueError("W1 private workload settings are incomplete")
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.service_principal = service_principal
        self.timeout_seconds = timeout_seconds

    def acquire(self, *, owner_user_id: UUID, run_id: UUID) -> RunBinding | None:
        value = self._post(
            "acquire",
            {
                "schema_version": "w1.private.w4.recommendation.acquire.v1",
                "owner_user_id": str(owner_user_id),
                "run_id": str(run_id),
            },
        )
        if value.get("completed") is True:
            return None
        binding = value.get("binding")
        if not isinstance(binding, dict):
            raise BridgeError("W1_PRIVATE_RESPONSE_INVALID")
        try:
            episodes = tuple(
                EpisodeVersionBinding(
                    episode_id=str(item["episode_id"]),
                    version=int(item["version"]),
                    episode_version_id=UUID(str(item["episode_version_id"])),
                )
                for item in binding["episode_versions"]
            )
            return RunBinding(
                owner_user_id=UUID(str(binding["owner_user_id"])),
                run_id=UUID(str(binding["run_id"])),
                project_id=UUID(str(binding["project_id"])),
                question_id=UUID(str(binding["question_id"])),
                question_version_id=UUID(str(binding["question_version_id"])),
                snapshot_id=UUID(str(binding["snapshot_id"])),
                lease_token=UUID(str(binding["lease_token"])),
                context_sha256=str(binding["context_sha256"]),
                request=dict(binding["request"]),
                episode_versions=episodes,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise BridgeError("W1_PRIVATE_RESPONSE_INVALID") from error

    def load_context(self, binding: RunBinding) -> dict | None:
        value = self._lease_post("context", binding)
        context = value.get("context")
        return dict(context) if isinstance(context, dict) else None

    def authorize(self, binding: RunBinding, **action) -> bool:
        value = self._lease_post(
            "authorize",
            binding,
            extra={
                "action": action.get("action"),
                "user_id": action.get("user_id"),
                "project_id": action.get("project_id"),
                "context_version": action.get("context_version"),
            },
        )
        return value.get("allowed") is True

    def publish(self, binding: RunBinding, publication: dict) -> bool:
        value = self._lease_post("publish", binding, extra={"publication": publication})
        return value.get("published") is True

    def fail(self, binding: RunBinding, code: str) -> None:
        self._lease_post("fail", binding, extra={"code": code})

    def _lease_post(
        self, operation: str, binding: RunBinding, *, extra: dict | None = None
    ) -> dict:
        body = {
            "schema_version": "w1.private.w4.recommendation.lease.v1",
            "run_id": str(binding.run_id),
            "lease_token": str(binding.lease_token),
        }
        if extra:
            body.update(extra)
        return self._post(operation, body)

    def _post(self, operation: str, body: dict) -> dict:
        request = Request(
            f"{self.base_url}/internal/v1/w4/recommendations/{operation}",
            data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.bearer_token}",
                "Content-Type": "application/json",
                "X-EPICK-Service-Principal": self.service_principal,
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                value = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                payload = json.loads(error.read().decode("utf-8"))
                code = payload.get("code", "W1_PRIVATE_HTTP_ERROR")
            except (UnicodeDecodeError, json.JSONDecodeError):
                code = "W1_PRIVATE_HTTP_ERROR"
            raise BridgeError(str(code)) from error
        except (OSError, TimeoutError, URLError) as error:
            raise BridgeError("W1_PRIVATE_TRANSPORT_RETRYABLE") from error
        if not isinstance(value, dict):
            raise BridgeError("W1_PRIVATE_RESPONSE_INVALID")
        return value
