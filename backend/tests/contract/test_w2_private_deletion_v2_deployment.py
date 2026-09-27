"""W1's v2 deletion processes must not inherit the general worker boundary."""

from __future__ import annotations

import json
import re
from pathlib import Path

COMPOSE_PATH = Path(__file__).parents[2] / "infra" / "w1-runtime.compose.yml"
INFRA_PATH = COMPOSE_PATH.parent


def _service(compose: str, name: str) -> str:
    return re.split(r"\n  [a-z][a-z0-9-]*:\n", compose.split(f"  {name}:\n", 1)[1], maxsplit=1)[0]


def test_v2_relay_has_isolated_profile_and_activation_proof_mount() -> None:
    section = _service(COMPOSE_PATH.read_text(encoding="utf-8"), "w1-w2-deletion-command-relay")

    assert "W1_W2_DELETION_RELAY_ENV_FILE" in section
    assert '["python", "scripts/run_outbox_relay.py", "--scope", "w2-deletion"]' in section
    assert "W2_DELETION_ACTIVATION_PROOF_PATH" in section
    assert "target: /run/epick/w2-deletion-activation.json" in section
    assert 'profiles: ["w2-deletion"]' in section
    assert "W1_RUNTIME_ENV_FILE" not in section
    assert "ports:" not in section
    assert "read_only: true" in section
    assert "- ALL" in section


def test_v2_callback_is_private_and_uses_separate_env() -> None:
    section = _service(COMPOSE_PATH.read_text(encoding="utf-8"), "w1-w2-deletion-callback")

    assert "W1_W2_DELETION_CALLBACK_ENV_FILE" in section
    assert '["python", "scripts/run_w2_deletion_callback.py"]' in section
    assert 'expose:\n      - "8081"' in section
    assert 'profiles: ["w2-deletion"]' in section
    assert "W1_RUNTIME_ENV_FILE" not in section
    assert 'ports:\n      - "${W1_W2_DELETION_CALLBACK_BIND_IP:?' in section
    assert '}:8081:8081"' in section
    assert '"0.0.0.0:8081:8081"' not in section
    assert "read_only: true" in section
    assert "- ALL" in section


def test_v2_deletion_queue_policies_are_dedicated_and_names_only() -> None:
    consumer = json.loads(
        (INFRA_PATH / "w2-deletion-consumer-policy.template.json").read_text(encoding="utf-8")
    )
    queue = json.loads(
        (INFRA_PATH / "w2-deletion-queue-policy.template.json").read_text(encoding="utf-8")
    )
    relay = json.loads(
        (INFRA_PATH / "w1-w2-deletion-relay-policy.template.json").read_text(encoding="utf-8")
    )
    consumer_statements = consumer["Statement"]
    assert len(consumer_statements) == 2
    assert consumer_statements[0]["Resource"] == "${W2_DELETION_COMMAND_QUEUE_ARN}"
    assert set(consumer_statements[0]["Action"]) == {
        "sqs:GetQueueAttributes",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:ChangeMessageVisibility",
    }
    assert consumer_statements[1]["Resource"] == "${W2_DELETION_COMMAND_DLQ_ARN}"
    assert consumer_statements[1]["Action"] == "sqs:GetQueueAttributes"

    queue_statements = queue["Statement"]
    assert len(queue_statements) == 2
    assert queue_statements[0]["Principal"]["AWS"] == "${W1_DELETION_RELAY_ROLE_ARN}"
    assert queue_statements[0]["Action"] == "sqs:SendMessage"
    assert queue_statements[1]["Principal"]["AWS"] == "${W2_DELETION_CONSUMER_ROLE_ARN}"
    assert set(queue_statements[1]["Action"]) == set(consumer_statements[0]["Action"])
    assert all(
        statement["Resource"] == "${W2_DELETION_COMMAND_QUEUE_ARN}"
        for statement in queue_statements
    )
    assert "*" not in json.dumps((consumer, queue))
    assert "sqs:PurgeQueue" not in json.dumps((consumer, queue))
    assert relay["Statement"] == [
        {
            "Sid": "PublishOnlyW2DeletionCommandMain",
            "Effect": "Allow",
            "Action": ["sqs:GetQueueAttributes", "sqs:SendMessage"],
            "Resource": "${W2_DELETION_COMMAND_QUEUE_ARN}",
        }
    ]


def test_v2_deletion_runtime_config_read_is_limited_to_named_inputs() -> None:
    w2 = json.loads(
        (INFRA_PATH / "w2-deletion-runtime-config-policy.template.json").read_text(encoding="utf-8")
    )
    w1 = json.loads(
        (INFRA_PATH / "w1-w2-deletion-callback-secret-policy.template.json").read_text(
            encoding="utf-8"
        )
    )
    assert w2["Statement"] == [
        {
            "Sid": "ReadOnlyW2DeletionRuntimeParameters",
            "Effect": "Allow",
            "Action": "ssm:GetParameter",
            "Resource": [
                "${W2_DELETION_COMMAND_MAIN_URL_PARAMETER_ARN}",
                "${W2_DELETION_COMMAND_DLQ_ARN_PARAMETER_ARN}",
                "${W1_W2_DELETION_CALLBACK_ORIGIN_PARAMETER_ARN}",
                "${W1_W2_DELETION_CALLBACK_CA_PARAMETER_ARN}",
            ],
        },
        {
            "Sid": "ReadOnlyW2DeletionCallbackBearer",
            "Effect": "Allow",
            "Action": "secretsmanager:GetSecretValue",
            "Resource": "${W1_W2_DELETION_CALLBACK_BEARER_SECRET_ARN}",
        },
    ]
    assert w1["Statement"] == [
        {
            "Sid": "ReadOnlyW1DeletionCommandQueueUrl",
            "Effect": "Allow",
            "Action": "ssm:GetParameter",
            "Resource": "${W2_DELETION_COMMAND_MAIN_URL_PARAMETER_ARN}",
        },
        {
            "Sid": "ReadOnlyW1DeletionCallbackBearer",
            "Effect": "Allow",
            "Action": "secretsmanager:GetSecretValue",
            "Resource": "${W1_W2_DELETION_CALLBACK_BEARER_SECRET_ARN}",
        },
    ]
    assert "*" not in json.dumps((w1, w2))


def test_v2_deletion_runtime_inputs_define_private_injection_and_inspection() -> None:
    raw = (INFRA_PATH / "w2-deletion-runtime-inputs.template.json").read_text(encoding="utf-8")
    inputs = json.loads(raw)

    assert inputs["environment"] == "${ENVIRONMENT}"
    assert inputs["resource_owner"] == "w1"
    assert inputs["queue"]["main_name"] != inputs["queue"]["dlq_name"]
    assert inputs["queue"]["main_url_parameter"].startswith("/epick/${ENVIRONMENT}/")
    assert inputs["queue"]["dlq_arn_parameter"].startswith("/epick/${ENVIRONMENT}/")
    assert inputs["queue"]["queue_parameter_store"] == "aws_ssm_parameter_store"
    assert inputs["queue"]["identity_policy_owner"] == "w1"
    assert inputs["queue"]["queue_policy_owner"] == "w1"
    assert inputs["queue"]["consumer_role_ref"] == "${W2_DELETION_CONSUMER_ROLE_ARN}"
    assert inputs["callback"]["path"] == "/internal/v1/w2-private/deletion/ack"
    assert inputs["callback"]["alb_health_path"] == "/internal/health/ready"
    assert inputs["callback"]["private_origin_parameter"].startswith("/epick/${ENVIRONMENT}/")
    assert inputs["callback"]["ca_pem_parameter"] == "/epick/${ENVIRONMENT}/w1/lookup-ca-pem"
    assert inputs["callback"]["bearer_secret_id"].startswith("epick/${ENVIRONMENT}/")
    assert inputs["callback"]["origin_ca_parameter_store"] == "aws_ssm_parameter_store"
    assert inputs["callback"]["bearer_secret_store"] == "aws_secrets_manager"
    assert inputs["callback"]["rotation_owner"] == "w1"
    assert inputs["callback"]["rotation_mode"] == (
        "pause_w2_consumer_update_secret_restart_w1_callback_restart_w2_consumer"
    )
    assert inputs["callback"]["service_principal"] == "w2"
    assert inputs["callback"]["ingress_source"] == "${W2_DELETION_CONSUMER_SECURITY_GROUP}"
    assert inputs["callback"]["tls_boundary"] == "private_https"
    assert inputs["health_inspect"]["http_endpoint_required"] is False
    assert inputs["health_inspect"]["interface"] == "count_only_cli_and_process_queue_readiness"
    assert inputs["ack_responses"]["200:ACKNOWLEDGED"] == "DELETE_AFTER_DURABLE_SUCCESS"
    assert inputs["ack_responses"]["non_acknowledged_sqs_delete_allowed"] is False
    assert inputs["ack_responses"]["409"] == "DLQ_MANUAL_INVESTIGATION"
    callback_source = (
        Path(__file__).parents[2] / "app" / "runtime" / "w2_deletion_callback.py"
    ).read_text(encoding="utf-8")
    emitted_409_codes = set(
        re.findall(r'_error\("(W2_DELETION_V2_[A-Z_]+)", 409\)', callback_source)
    )
    assert set(inputs["ack_responses"]["409_codes"]) == emitted_409_codes
    assert set(inputs["ack_responses"]["409_codes"].values()) == {"DLQ_MANUAL_INVESTIGATION"}
    assert inputs["ack_responses"]["503"] == "RETRY_PRESERVE_MESSAGE"
    assert inputs["ack_responses"]["timeout"] == "RETRY_PRESERVE_MESSAGE"
    operations = inputs["repeated_409_operations"]
    assert operations["queue_and_w1_authority_owner"] == "w1"
    assert operations["w2_consumer_and_receipt_owner"] == "w2"
    assert operations["automatic_dlq_redrive_allowed"] is False
    assert operations["blind_sqs_republish_allowed"] is False
    assert operations["reissue_mechanism"] == "w1_deletion_service_retry_target_not_dlq_redrive"
    assert operations["same_deletion_id_required"] is True
    assert operations["same_v2_payload_required"] is True
    assert operations["terminal_or_unreconciled_action"] == "quarantine_and_escalate_no_reissue"
    assert operations["steps"][0].startswith("pause_only_the_affected_deletion_route")
    assert (
        "w1_and_w2_reconcile_durable_receipt_and_ack_before_deciding_reissue" in operations["steps"]
    )
    assert inputs["health_inspect"]["current_w2_operator"] == "preflight_consume-once_run_only"
    assert inputs["health_inspect"]["additional_w2_interface_required"] == "count_only_inspect"
    assert inputs["health_inspect"]["output_fields"] == [
        "pending_deletion_count",
        "pending_ack_count",
    ]
    assert inputs["health_inspect"]["no_owner_ids_payloads_tokens_or_urls"] is True
    assert "arn:aws:" not in raw
    assert "https://" not in raw
