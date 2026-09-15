from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.identity import AuthIdentity, IdempotencyRecord, User


class IdentityRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_user(self, user: User) -> None:
        self.session.add(user)

    def add_identity(self, identity: AuthIdentity) -> None:
        self.session.add(identity)

    def find_idempotency_record(
        self, owner_user_id: UUID, method: str, path_scope: str, idempotency_key: str
    ) -> IdempotencyRecord | None:
        statement = (
            select(IdempotencyRecord)
            .where(
                IdempotencyRecord.owner_user_id == owner_user_id,
                IdempotencyRecord.method == method,
                IdempotencyRecord.path_scope == path_scope,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
            .with_for_update()
        )
        return self.session.scalar(statement)

    def add_idempotency_record(self, record: IdempotencyRecord) -> None:
        self.session.add(record)
