from __future__ import annotations

from datetime import datetime
from hmac import compare_digest
from typing import Any
from uuid import UUID

from app.models.identity import IdempotencyRecord
from app.repo.identity import IdentityRepository


class IdempotencyConflictError(Exception):
    """The caller reused an idempotency key with a different request hash."""


class IdempotencyService:
    def __init__(self, repository: IdentityRepository) -> None:
        self.repository = repository

    def reserve(
        self,
        *,
        owner_user_id: UUID,
        method: str,
        path_scope: str,
        idempotency_key: str,
        request_hash: str,
        expires_at: datetime,
    ) -> tuple[IdempotencyRecord, bool]:
        existing = self.repository.find_idempotency_record(
            owner_user_id, method, path_scope, idempotency_key
        )
        if existing is not None:
            if not compare_digest(existing.request_hash, request_hash):
                raise IdempotencyConflictError(
                    "idempotency key was reused with a different request"
                )
            return existing, True

        record = IdempotencyRecord(
            owner_user_id=owner_user_id,
            method=method,
            path_scope=path_scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            expires_at=expires_at,
        )
        self.repository.add_idempotency_record(record)
        self.repository.session.flush()
        return record, False

    @staticmethod
    def complete(
        record: IdempotencyRecord,
        *,
        response_status: int,
        response_ref: str,
        response_body: dict[str, Any],
    ) -> None:
        record.response_status = response_status
        record.response_ref = response_ref
        record.response_body = response_body
