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

    @property
    def w1_execution_queue_url(self) -> str | None:
        return self.w1_job_execution_queue_url or self.w1_sqs_execution_queue_url

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
