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
    w1_sqs_execution_queue_url: str | None = None
    w1_outbox_relay_instance_id: str | None = None
    w1_outbox_relay_batch_size: int = 10
    w1_outbox_relay_lease_seconds: int = 120
    w1_outbox_relay_poll_seconds: float = 2.0
    w1_outbox_relay_retry_base_seconds: int = 5
    w1_outbox_relay_retry_max_seconds: int = 300

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
