"""One-message relay and SQS send-only adapter. No worker/ACK/receive API."""

import hashlib
import re
from dataclasses import dataclass

from .question_core_contract import QuestionCoreError, require
from .question_core_outbox import SAFE_ERRORS
from .question_core_producer import utc_now


@dataclass(frozen=True)
class SqsSendSettings:
    main_queue_url: str
    main_queue_arn: str
    region: str
    enabled: bool = False

    def validate(self):
        require(type(self.enabled) is bool and self.enabled, "CORE_SEND_DISABLED")
        require(
            all(
                isinstance(value, str) and value
                for value in (self.main_queue_url, self.main_queue_arn, self.region)
            ),
            "CORE_QUEUE_CONFIG_INVALID",
        )
        match = re.fullmatch(
            r"https://sqs\.([a-z0-9-]+)\.amazonaws\.com/([0-9]{12})/([A-Za-z0-9_-]{1,80})",
            self.main_queue_url,
        )
        require(match is not None, "CORE_QUEUE_CONFIG_INVALID")
        region, account, name = match.groups()
        require(
            region == self.region
            and self.main_queue_arn == f"arn:aws:sqs:{region}:{account}:{name}",
            "CORE_QUEUE_CONFIG_INVALID",
        )


class SqsSendOnly:
    """Only SendMessage is used. IAM enforcement belongs to the deployed role.

    SDK retries are disabled so every application retry goes through fresh W1 and
    policy checks. A caller-supplied SDK client must have the same retry setting.
    """

    def __init__(self, *, contract, settings: SqsSendSettings, client=None):
        settings.validate()
        contract.require_adopted()
        self.contract, self.settings = contract, settings
        if client is None:
            try:
                import boto3
                from botocore.config import Config

                client = boto3.client(
                    "sqs",
                    region_name=settings.region,
                    config=Config(
                        connect_timeout=3,
                        read_timeout=10,
                        ignore_configured_endpoint_urls=True,
                        retries={"total_max_attempts": 1, "mode": "standard"},
                    ),
                )
            except Exception:  # noqa: BLE001 - sanitize credential/SDK initialization errors.
                raise QuestionCoreError("CORE_SQS_CLIENT_UNAVAILABLE") from None
        try:
            attempts = client.meta.config.retries.get("total_max_attempts")
            service = client.meta.service_model.service_name
            region = client.meta.region_name
            endpoint = client.meta.endpoint_url
        except (AttributeError, TypeError):
            raise QuestionCoreError("CORE_SQS_CLIENT_INVALID") from None
        require(
            attempts == 1
            and service == "sqs"
            and region == settings.region
            and endpoint == f"https://sqs.{settings.region}.amazonaws.com",
            "CORE_SQS_CLIENT_INVALID",
        )
        self._client = client

    def send(self, body):
        self.settings.validate()
        self.contract.require_adopted()
        self.contract.validate(body)
        try:
            response = self._client.send_message(
                QueueUrl=self.settings.main_queue_url, MessageBody=body
            )
            message_id = response.get("MessageId")
            require(
                response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200
                and isinstance(message_id, str)
                and 0 < len(message_id) <= 100
                and all(c.isalnum() or c in "-_" for c in message_id)
                and response.get("MD5OfMessageBody")
                == hashlib.md5(body.encode("utf-8"), usedforsecurity=False).hexdigest(),
                "CORE_SEND_UNCONFIRMED",
            )
        except Exception:  # noqa: BLE001 - SDK error details can contain private data.
            # Do not expose SDK errors, URLs, credentials or payload snippets.
            raise QuestionCoreError("CORE_SEND_UNCONFIRMED") from None
        return message_id


class QuestionCoreRelay:
    def __init__(self, *, producer, sender, clock=utc_now, lease_seconds=60):
        self.producer, self.sender, self.clock = producer, sender, clock
        self.store = producer.store
        self.lease_seconds = lease_seconds

    def run_once(self):
        claim = self.store.claim(now=self.clock().timestamp(), lease_seconds=self.lease_seconds)
        if claim is None:
            return {"status": "IDLE"}
        try:
            # Must be after restart/claim and immediately before EACH send.
            body = self.producer.check_current(claim.prepared)
        except QuestionCoreError as error:
            if error.code == "CORE_CURRENTNESS_UNAVAILABLE":
                self.store.release(claim, now=self.clock().timestamp(), error_code=error.code)
                return {"status": "RETRY", "error_code": error.code}
            code = error.code if error.code in SAFE_ERRORS else "CORE_RECHECK_REJECTED"
            self.store.block(claim, error_code=code)
            return {"status": "BLOCKED", "error_code": code}
        except Exception:  # noqa: BLE001 - fail closed at the trusted host adapter boundary.
            code = "CORE_CURRENTNESS_UNAVAILABLE"
            self.store.release(claim, now=self.clock().timestamp(), error_code=code)
            return {"status": "RETRY", "error_code": code}
        if not self.store.owns(claim, now=self.clock().timestamp()):
            return {"status": "LEASE_LOST"}
        try:
            broker_id = self.sender.send(body)
            require(
                isinstance(broker_id, str)
                and 0 < len(broker_id) <= 100
                and all(c.isalnum() or c in "-_" for c in broker_id),
                "CORE_SEND_UNCONFIRMED",
            )
        except Exception:  # noqa: BLE001 - preserve the body after every uncertain send.
            code = "CORE_SEND_UNCONFIRMED"
            self.store.release(claim, now=self.clock().timestamp(), error_code=code)
            return {"status": "RETRY", "error_code": code}
        # A crash here leaves the committed body and lease intact for exact retry.
        self.store.mark_sent(claim, now=self.clock().timestamp(), broker_message_id=broker_id)
        return {
            "status": "SENT",
            "message_id": claim.prepared.message_id,
            "meaning": "TRANSPORT_ACCEPTED_ONLY",
        }
