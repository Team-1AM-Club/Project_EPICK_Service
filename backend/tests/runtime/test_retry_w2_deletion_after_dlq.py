from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest

from scripts import retry_w2_deletion_after_dlq as cli


def _arguments() -> list[str]:
    return [
        "--deletion-id",
        str(uuid4()),
        "--original-outbox-id",
        str(uuid4()),
        "--expected-epoch",
        "1",
        "--dlq-body-sha256",
        "sha256:" + "a" * 64,
        "--ack-code",
        "W2_DELETION_V2_BINDING_INVALID",
        "--w2-receipt-outcome",
        "APPLIED",
        "--incident-id",
        "INC-T050-409-001",
        "--confirm-route-paused",
    ]


def test_manual_retry_refuses_when_integration_disabled(capsys) -> None:
    with (
        patch.object(cli, "validate_integration_startup", return_value={"status": "disabled"}),
        patch.object(cli, "require_w2_deletion_activation_proof") as proof,
        patch.object(cli, "create_deletion_worker_session_factory") as factory,
    ):
        assert cli.main(_arguments()) == 1
    assert capsys.readouterr().out.strip() == '{"status": "RETRY_REFUSED"}'
    proof.assert_not_called()
    factory.assert_not_called()


def test_manual_retry_refuses_missing_activation_proof(capsys) -> None:
    with (
        patch.object(cli, "validate_integration_startup", return_value={"status": "ready"}),
        patch.object(cli.settings, "w2_deletion_activation_proof_path", None),
        patch.object(cli.settings, "w2_deletion_image_digest", None),
        patch.object(cli, "create_deletion_worker_session_factory") as factory,
    ):
        assert cli.main(_arguments()) == 1
    assert capsys.readouterr().out.strip() == '{"status": "RETRY_REFUSED"}'
    factory.assert_not_called()


def test_manual_retry_does_not_create_sqs_client() -> None:
    assert "boto3" not in cli.__dict__
    assert "SqsPort" not in cli.__dict__


def test_manual_retry_evidence_cannot_be_omitted() -> None:
    # argparse itself exits before any DB or queue interaction.
    with patch.object(cli, "create_deletion_worker_session_factory") as factory:
        with pytest.raises(SystemExit) as error:
            cli.main(["--deletion-id", str(uuid4())])
        assert error.value.code == 2
    factory.assert_not_called()
