from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    database_url: str = "postgresql+psycopg://epick:epick_2026_local@localhost:5432/epick_local"
    migration_database_url: str | None = None
    test_database_url: str = "postgresql+psycopg://epick:epick_2026_local@localhost:5432/epick_test"
    # Cursor signing is intentionally deployment-provided. API-0 has no list
    # endpoint yet, so local/test callers pass a key explicitly to CursorCodec.
    api_cursor_signing_key: str | None = None
    # Retention is deployment-configurable rather than scattered over mutation routers.
    api_idempotency_ttl_seconds: int = 86_400
    # Runtime relay configuration is deliberately separate from API request handling.  The
    # queue URL is an IaC output, never a repository constant.
    # `W1_JOB_EXECUTION_QUEUE_URL` is the runtime/IaC canonical spelling.  Keep the previous
    # relay-only name as a read-compatible fallback until no pre-R-2 environment remains.
    w1_job_execution_queue_url: str | None = None
    w1_sqs_execution_queue_url: str | None = None
    w1_outbox_relay_instance_id: str | None = None
    w1_outbox_relay_batch_size: int = 10
    w1_outbox_relay_lease_seconds: int = 120
    w1_outbox_relay_poll_seconds: float = 2.0
    w1_outbox_relay_retry_base_seconds: int = 5
    w1_outbox_relay_retry_max_seconds: int = 300
    # Commit-gate recovery is a W1-only control-plane worker.  It runs independently
    # from the public API and never parses a raw W2 ACK payload.
    w1_commit_gate_recovery_batch_size: int = 100
    w1_commit_gate_recovery_poll_seconds: float = 5.0
    # Runtime processes never inherit the API database login.  These values are provided from
    # runtime-only Secrets Manager entries once R-3 provisions the private host.
    worker_database_url: str | None = None
    lookup_database_url: str | None = None
    w2_collection_command_queue_url: str | None = None
    w2_collection_result_queue_url: str | None = None
    # W2 commit-gate ingress is deliberately separate from the legacy collection
    # result route. It is only injected into the private worker profile.
    w2_commit_gate_inbound_queue_url: str | None = None
    w2_commit_gate_inbound_dlq_url: str | None = None
    w2_commit_gate_expected_producer: str = "w2"
    w2_commit_gate_expected_sender_id: str | None = None
    w2_commit_gate_batch_size: int = 10
    w2_commit_gate_wait_seconds: int = 20
    w2_commit_gate_visibility_seconds: int = 120
    w1_worker_id: str | None = None
    w1_sqs_visibility_seconds: int = 120
    w1_sqs_long_poll_seconds: int = 20
    w1_lease_heartbeat_seconds: int = 60
    w1_worker_poll_seconds: float = 2.0
    w1_w2_lookup_bearer: str | None = None
    # W3 Core Decision is consumed only by the private W1 runtime. SenderId is
    # SQS-authenticated system metadata; it is never accepted from the JSON body.
    w3_core_decision_queue_url: str | None = None
    w3_core_decision_dlq_url: str | None = None
    w3_core_decision_expected_producer: str = "w3"
    w3_core_decision_expected_sender_id: str | None = None
    w3_core_decision_batch_size: int = 10
    w3_core_decision_wait_seconds: int = 20
    w3_core_decision_visibility_seconds: int = 120
    # W4 Question Core decisions have a distinct queue and authenticated sender.
    # Body ``producer=w4`` is a contract check only; the consumer trusts SQS
    # SenderId metadata after normalising its session suffix.
    w4_question_core_decision_queue_url: str | None = None
    w4_question_core_decision_dlq_url: str | None = None
    w4_question_core_decision_expected_producer: str = "w4"
    w4_question_core_decision_expected_sender_id: str | None = None
    w4_question_core_decision_batch_size: int = 10
    w4_question_core_decision_wait_seconds: int = 20
    w4_question_core_decision_visibility_seconds: int = 120

    @property
    def w1_execution_queue_url(self) -> str | None:
        return self.w1_job_execution_queue_url or self.w1_sqs_execution_queue_url

    @model_validator(mode="after")
    def validate_w2_commit_gate_runtime(self) -> "Settings":
        queue_url = self.w2_commit_gate_inbound_queue_url
        dlq_url = self.w2_commit_gate_inbound_dlq_url
        if (queue_url is None) != (dlq_url is None):
            raise ValueError("W2 commit-gate queue and DLQ must be configured together")
        if queue_url is not None and queue_url == dlq_url:
            raise ValueError("W2 commit-gate queue and DLQ must be distinct")
        if queue_url is not None and not self.w2_commit_gate_expected_sender_id:
            raise ValueError("W2 commit-gate expected sender id must be configured with the queue")
        if not self.w2_commit_gate_expected_producer.strip():
            raise ValueError("W2 commit-gate expected producer must be non-empty")
        if self.w2_commit_gate_expected_producer != "w2":
            raise ValueError("W2 commit-gate expected producer must be w2")
        if not 1 <= self.w2_commit_gate_batch_size <= 10:
            raise ValueError("W2 commit-gate batch size must be between 1 and 10")
        if not 0 <= self.w2_commit_gate_wait_seconds <= 20:
            raise ValueError("W2 commit-gate wait seconds must be between 0 and 20")
        if not 1 <= self.w2_commit_gate_visibility_seconds <= 43_200:
            raise ValueError("W2 commit-gate visibility seconds must be between 1 and 43200")
        queue_url = self.w4_question_core_decision_queue_url
        dlq_url = self.w4_question_core_decision_dlq_url
        if (queue_url is None) != (dlq_url is None):
            raise ValueError("W4 Question Core queue and DLQ must be configured together")
        if queue_url is not None and queue_url == dlq_url:
            raise ValueError("W4 Question Core queue and DLQ must be distinct")
        if queue_url is not None and not self.w4_question_core_decision_expected_sender_id:
            raise ValueError(
                "W4 Question Core expected sender id must be configured with the queue"
            )
        if queue_url is not None and ":" in self.w4_question_core_decision_expected_sender_id:
            raise ValueError(
                "W4 Question Core expected sender id must be a stable principal id without session"
            )
        if not self.w4_question_core_decision_expected_producer.strip():
            raise ValueError("W4 Question Core expected producer must be non-empty")
        if self.w4_question_core_decision_expected_producer != "w4":
            raise ValueError("W4 Question Core expected producer must be w4")
        if not 1 <= self.w4_question_core_decision_batch_size <= 10:
            raise ValueError("W4 Question Core batch size must be between 1 and 10")
        if not 0 <= self.w4_question_core_decision_wait_seconds <= 20:
            raise ValueError("W4 Question Core wait seconds must be between 0 and 20")
        if not 1 <= self.w4_question_core_decision_visibility_seconds <= 43_200:
            raise ValueError("W4 Question Core visibility seconds must be between 1 and 43200")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
