import json
from ipaddress import ip_address
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    # End-to-end integration is opt-in. REAL remains closed until an approved,
    # versioned policy manifest has a separately verified deployment path.
    integration_enabled: bool = False
    integration_real_data_enabled: bool = False
    integration_policy_revision: str | None = None
    integration_w2_private_origin: str | None = None
    integration_w3_private_origin: str | None = None
    integration_w4_private_origin: str | None = None
    integration_contract_pins: dict[str, str] = Field(default_factory=dict)
    database_url: str = "postgresql+psycopg://epick:epick_2026_local@localhost:5432/epick_local"
    migration_database_url: str | None = None
    test_database_url: str = "postgresql+psycopg://epick:epick_2026_local@localhost:5432/epick_test"
    # Cursor signing is intentionally deployment-provided. API-0 has no list
    # endpoint yet, so local/test callers pass a key explicitly to CursorCodec.
    api_cursor_signing_key: str | None = None
    # Retention is deployment-configurable rather than scattered over mutation routers.
    api_idempotency_ttl_seconds: int = 86_400
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_oidc_redirect_uri: str = "http://localhost:8000/api/v1/auth/google/callback"
    google_oidc_discovery_url: str = "https://accounts.google.com/.well-known/openid-configuration"
    google_oidc_authorization_endpoint: str = "https://accounts.google.com/o/oauth2/v2/auth"
    epick_auth_issuer: str = "http://localhost:8000"
    epick_auth_audience: str = "epick-public-api"
    epick_auth_signing_key: SecretStr | None = None
    epick_refresh_token_pepper: SecretStr | None = None
    epick_access_token_ttl_seconds: int = 900
    epick_refresh_token_ttl_seconds: int = 2_592_000
    epick_oidc_transaction_ttl_seconds: int = 600
    epick_frontend_url: str = "http://localhost:3001"
    epick_allowed_frontend_origins: list[str] = ["http://localhost:3001"]
    epick_auth_cookie_secure: bool = False
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
    deletion_worker_database_url: str | None = None
    lookup_database_url: str | None = None
    # W4's pre-send currentness adapter has a distinct, read-only database
    # login and bearer.  It is not the W2 lookup bearer or worker database URL.
    w4_context_database_url: str | None = None
    w1_w4_context_bearer: str | None = None
    w2_collection_command_queue_url: str | None = None
    w2_collection_result_queue_url: str | None = None
    # CT15 uses a disposable gate-only outbound queue.  It is intentionally
    # opt-in: normal collection and direct-registration traffic must keep using
    # the regular W2 command queue until a joint run is explicitly approved.
    w2_ct15_gate_only_queue_approved: bool = False
    w2_ct15_gate_only_command_queue_url: str | None = None
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
    # W1 -> W3 retention commands and W3 -> W1 receipts use distinct private
    # Standard queues. Stable Role IDs are deployment secrets, never body fields.
    w3_retention_command_queue_url: str | None = None
    w3_retention_receipt_queue_url: str | None = None
    w3_retention_receipt_dlq_url: str | None = None
    w3_retention_w1_stable_role_id: str | None = None
    w3_retention_expected_w3_sender_id: str | None = None
    w3_retention_receipt_batch_size: int = 10
    w3_retention_receipt_wait_seconds: int = 20
    w3_retention_receipt_visibility_seconds: int = 120
    # W1's private currentness boundary for W3. These values are injected only
    # into the isolated Authority process/W3 workload after W3-B is verified.
    w3_authority_private_url: str | None = None
    w3_authority_database_url: str | None = None
    w3_authority_private_bearer: SecretStr | None = None
    w3_authority_expected_principal: str = "epick-w3-core-runtime"
    w3_authority_connect_timeout_seconds: float = 1.0
    w3_authority_read_timeout_seconds: float = 2.0
    w3_authority_total_timeout_seconds: float = 5.0
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
    # Recommendation execution is a separate W1 -> W4 transport. It does not
    # reuse the W4 Question Core queue, sender identity, bearer, or evidence.
    w1_recommendation_execution_mode: str = "SYNTHETIC"
    w4_recommendation_execution_queue_url: str | None = None
    w4_recommendation_execution_dlq_url: str | None = None
    w4_recommendation_private_base_url: str | None = None
    w4_recommendation_private_bearer: SecretStr | None = None
    w4_recommendation_private_audience: str = "epick-w4-recommendation-worker"
    w4_recommendation_engine_source_revision: str = "df41433218918e4167784243dc9b88e5a858278d"
    w4_recommendation_schema_manifest_sha256: str = (
        "sha256:bf69d0f6b10bbeeb2cded79788758fa800b148782e19347edcae44ad12a460d3"
    )
    w4_recommendation_lease_seconds: int = 300
    w4_recommendation_request_timeout_seconds: int = 30
    w4_recommendation_visibility_seconds: int = 600
    w4_recommendation_heartbeat_seconds: int = 60
    w4_recommendation_max_receives: int = 5
    w4_recommendation_real_data_enabled: bool = False

    @property
    def w1_execution_queue_url(self) -> str | None:
        return self.w1_job_execution_queue_url or self.w1_sqs_execution_queue_url

    @property
    def w2_commit_gate_outbound_queue_url(self) -> str | None:
        if self.w2_ct15_gate_only_queue_approved:
            return self.w2_ct15_gate_only_command_queue_url
        return self.w2_collection_command_queue_url

    @field_validator("epick_allowed_frontend_origins", mode="before")
    @classmethod
    def parse_frontend_origins(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                return json.loads(stripped)
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def validate_w2_commit_gate_runtime(self) -> "Settings":
        authority_values = (
            self.w3_authority_private_url,
            self.w3_authority_database_url,
            self.w3_authority_private_bearer,
        )
        if any(value is not None for value in authority_values) and not all(authority_values):
            raise ValueError(
                "W3 Authority URL, database URL, and bearer must be configured together"
            )
        if not self.w3_authority_expected_principal.strip():
            raise ValueError("W3 Authority expected principal must be non-empty")
        receipt_values = (
            self.w3_retention_receipt_queue_url,
            self.w3_retention_receipt_dlq_url,
            self.w3_retention_expected_w3_sender_id,
            self.deletion_worker_database_url,
        )
        if any(value is not None for value in receipt_values) and not all(receipt_values):
            raise ValueError(
                "W3 retention receipt queue, DLQ, expected W3 Role ID, and deletion worker "
                "database must be configured together"
            )
        retention_queues = [
            queue
            for queue in (
                self.w3_retention_command_queue_url,
                self.w3_retention_receipt_queue_url,
                self.w3_retention_receipt_dlq_url,
            )
            if queue is not None
        ]
        if len(retention_queues) != len(set(retention_queues)):
            raise ValueError("configured W3 retention command, receipt, and DLQ queues must differ")
        for role_id in (
            self.w3_retention_w1_stable_role_id,
            self.w3_retention_expected_w3_sender_id,
        ):
            if role_id is not None and (not role_id.strip() or ":" in role_id):
                raise ValueError("W3 retention Role IDs must be stable IDs without session suffix")
        if not 1 <= self.w3_retention_receipt_batch_size <= 10:
            raise ValueError("W3 retention receipt batch size must be between 1 and 10")
        if not 0 <= self.w3_retention_receipt_wait_seconds <= 20:
            raise ValueError("W3 retention receipt wait seconds must be between 0 and 20")
        if not 1 <= self.w3_retention_receipt_visibility_seconds <= 43_200:
            raise ValueError("W3 retention receipt visibility seconds must be between 1 and 43200")
        if not (
            0 < self.w3_authority_connect_timeout_seconds
            <= self.w3_authority_read_timeout_seconds
            < self.w3_authority_total_timeout_seconds
            <= 30
        ):
            raise ValueError("W3 Authority timeout bounds are unsafe")
        if self.w3_authority_private_bearer is not None and not (
            self.w3_authority_private_bearer.get_secret_value().strip()
        ):
            raise ValueError("W3 Authority bearer must be non-empty")
        if self.w3_authority_database_url == self.database_url:
            raise ValueError("W3 Authority must use its dedicated read-only database login")
        if self.w3_authority_private_url is not None:
            parsed_authority_url = urlsplit(self.w3_authority_private_url)
            authority_host = parsed_authority_url.hostname
            if (
                parsed_authority_url.scheme not in {"http", "https"}
                or authority_host is None
                or parsed_authority_url.username is not None
                or parsed_authority_url.password is not None
                or parsed_authority_url.query
                or parsed_authority_url.fragment
            ):
                raise ValueError("W3 Authority URL must be a credential-free private HTTP origin")
            try:
                private_ip = ip_address(authority_host).is_private
            except ValueError:
                private_ip = False
            private_dns = (
                authority_host in {"localhost", "w1-authority"}
                or "." not in authority_host
                or authority_host.endswith((".internal", ".local"))
            )
            if not (private_ip or private_dns):
                raise ValueError("W3 Authority URL must resolve through a private network name")
        ct15_queue_url = self.w2_ct15_gate_only_command_queue_url
        if self.w2_ct15_gate_only_queue_approved and not ct15_queue_url:
            raise ValueError("CT15 gate-only queue must be configured when approved")
        if ct15_queue_url and not self.w2_ct15_gate_only_queue_approved:
            raise ValueError("CT15 gate-only queue requires explicit approval")
        if (
            ct15_queue_url
            and self.w2_collection_command_queue_url
            and ct15_queue_url == self.w2_collection_command_queue_url
        ):
            raise ValueError("CT15 gate-only queue must differ from the regular W2 command queue")
        if self.w2_ct15_gate_only_queue_approved and (
            not ct15_queue_url or "ct15" not in ct15_queue_url.lower()
        ):
            raise ValueError("CT15 gate-only queue URL must identify the CT15 environment")
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
        if self.w1_recommendation_execution_mode not in {"SYNTHETIC", "ENGINE"}:
            raise ValueError("W1 recommendation execution mode must be SYNTHETIC or ENGINE")
        recommendation_queue = self.w4_recommendation_execution_queue_url
        recommendation_dlq = self.w4_recommendation_execution_dlq_url
        if (recommendation_queue is None) != (recommendation_dlq is None):
            raise ValueError("W4 recommendation queue and DLQ must be configured together")
        if recommendation_queue is not None and recommendation_queue == recommendation_dlq:
            raise ValueError("W4 recommendation queue and DLQ must be distinct")
        if self.w1_recommendation_execution_mode == "ENGINE" and not all(
            (
                recommendation_queue,
                recommendation_dlq,
                self.w4_recommendation_private_base_url,
                self.w4_recommendation_private_bearer,
            )
        ):
            raise ValueError(
                "ENGINE recommendation mode requires its private queue and HTTP settings"
            )
        if self.w4_recommendation_real_data_enabled:
            raise ValueError("W4 recommendation REAL data is not approved in Stage 4.5")
        if not 30 <= self.w4_recommendation_lease_seconds <= 3_600:
            raise ValueError("W4 recommendation lease must be between 30 and 3600 seconds")
        if (
            not 1
            <= self.w4_recommendation_request_timeout_seconds
            < self.w4_recommendation_lease_seconds
        ):
            raise ValueError("W4 recommendation request timeout must be shorter than its lease")
        if (
            not self.w4_recommendation_request_timeout_seconds
            < self.w4_recommendation_visibility_seconds
        ):
            raise ValueError("W4 recommendation visibility must exceed the HTTP timeout")
        if (
            not 1
            <= self.w4_recommendation_heartbeat_seconds
            < self.w4_recommendation_visibility_seconds
        ):
            raise ValueError("W4 recommendation heartbeat must be shorter than visibility")
        if not 1 <= self.w4_recommendation_max_receives <= 20:
            raise ValueError("W4 recommendation max receives must be between 1 and 20")
        if not 60 <= self.epick_access_token_ttl_seconds <= 3_600:
            raise ValueError("EPICK access token TTL must be between 60 and 3600 seconds")
        if self.epick_refresh_token_ttl_seconds <= self.epick_access_token_ttl_seconds:
            raise ValueError("EPICK refresh token TTL must exceed access token TTL")
        if not 60 <= self.epick_oidc_transaction_ttl_seconds <= 600:
            raise ValueError("OIDC transaction TTL must be between 60 and 600 seconds")
        if not self.epick_allowed_frontend_origins:
            raise ValueError("At least one frontend origin must be configured")
        for origin in self.epick_allowed_frontend_origins:
            if origin == "*" or not origin.startswith(("http://", "https://")):
                raise ValueError("Frontend origins must be explicit HTTP(S) origins")
            if origin.endswith("/"):
                raise ValueError("Frontend origins must not have a trailing slash")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


def validate_api_auth_settings(api_settings: Settings) -> None:
    """Reject an insecure staging/production API without constraining private workers."""

    if api_settings.app_env.lower() not in {"staging", "production", "prod"}:
        return
    if not all(
        (
            api_settings.google_client_id,
            api_settings.google_client_secret,
            api_settings.epick_auth_signing_key,
            api_settings.epick_refresh_token_pepper,
        )
    ):
        raise ValueError("OIDC and EPICK auth secrets are required outside local/test")
    auth_urls = (
        api_settings.google_oidc_redirect_uri,
        api_settings.epick_auth_issuer,
        api_settings.epick_frontend_url,
        *api_settings.epick_allowed_frontend_origins,
    )
    if any(not item.startswith("https://") for item in auth_urls):
        raise ValueError("Authentication URLs must use HTTPS outside local/test")
    if not api_settings.epick_auth_cookie_secure:
        raise ValueError("Authentication cookies must be Secure outside local/test")


settings = Settings()
