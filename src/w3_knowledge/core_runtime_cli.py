"""Bounded operator commands. Credentials use the AWS workload provider chain."""

import argparse
import importlib
import json
import os
from pathlib import Path
import time
from uuid import UUID

from .core_runtime import AnalysisPlan, CoreRuntime, SqsTransport
from .retention import POLICY_REVISION


def load_authority(spec):
    if not spec or ":" not in spec:
        raise ValueError("TRUSTED_AUTHORITY_FACTORY_REQUIRED")
    module, factory = spec.split(":", 1)
    authority = getattr(importlib.import_module(module), factory)()
    if not callable(getattr(authority, "current", None)):
        raise ValueError("INVALID_AUTHORITY_FACTORY")
    return authority


def aws_transport():
    import boto3
    from botocore.config import Config

    region = os.environ["AWS_DEFAULT_REGION"]
    queue = os.environ["W3_CORE_DECISION_QUEUE_URL"]
    role_id = os.environ["W3_CORE_DECISION_EXPECTED_ROLE_ID"]
    if not role_id:
        raise ValueError("WORKLOAD_ROLE_REQUIRED")
    config = Config(connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 1})
    session = boto3.Session(region_name=region)
    identity = session.client("sts", config=config).get_caller_identity()
    if identity.get("UserId", "").split(":", 1)[
        0
    ] != role_id or ":assumed-role/" not in identity.get("Arn", ""):
        raise ValueError("WORKLOAD_ROLE_MISMATCH")
    return SqsTransport(session.client("sqs", config=config), queue)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=[
            "init",
            "supply",
            "relay-once",
            "replay",
            "delete-owner",
            "expire",
            "inspect",
            "backup",
            "smoke",
        ],
    )
    parser.add_argument("--db", type=Path)
    parser.add_argument("--retention-seconds", type=int)
    parser.add_argument("--max-attempts", type=int, default=8)
    parser.add_argument(
        "--authority", help="Trusted module:factory; must fetch fresh authorization"
    )
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--idempotency-key")
    parser.add_argument("--owner-id", type=UUID)
    parser.add_argument("--deletion-epoch", type=int)
    parser.add_argument("--event-id", type=UUID)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--send", action="store_true", help="Explicitly enable one live SQS send")
    args = parser.parse_args()
    try:
        if args.command == "smoke":
            from .core_runtime_smoke import run

            if args.directory is None:
                raise ValueError("DIRECTORY_REQUIRED")
            result = run(args.directory)
        else:
            if args.db is None or args.retention_seconds is None:
                raise ValueError("DB_AND_RETENTION_REQUIRED")
            if args.command == "init":
                if args.db.exists():
                    raise ValueError("DB_ALREADY_EXISTS")
            elif not args.db.is_file():
                raise ValueError("EXISTING_DB_REQUIRED")
            runtime = CoreRuntime(
                args.db, retention_seconds=args.retention_seconds, max_attempts=args.max_attempts
            )
            now = time.time()
            if args.command == "init":
                result = {"status": "INITIALIZED_NOT_DEPLOYED"}
            elif args.command == "supply":
                if args.plan is None:
                    raise ValueError("PLAN_REQUIRED")
                plan = AnalysisPlan.model_validate_json(args.plan.read_text(encoding="utf-8"))
                event = runtime.supply(
                    plan, load_authority(args.authority), args.idempotency_key, now=now
                )
                result = {
                    "event_id": str(event.message_id),
                    "revision": event.decision_version,
                    "status": "PENDING",
                }
            elif args.command == "relay-once":
                if not args.send:
                    raise ValueError("EXPLICIT_SEND_REQUIRED")
                result = {
                    "status": runtime.relay_once(
                        load_authority(args.authority), aws_transport(), now=now
                    )
                }
            elif args.command == "replay":
                if args.event_id is None:
                    raise ValueError("EVENT_ID_REQUIRED")
                runtime.replay(args.event_id, load_authority(args.authority), now=now)
                result = {"status": "PENDING"}
            elif args.command == "delete-owner":
                if args.owner_id is None or args.deletion_epoch is None:
                    raise ValueError("DELETION_BINDING_REQUIRED")
                result = {
                    "deleted": runtime.delete_owner(
                        args.owner_id, deletion_epoch=args.deletion_epoch, now=now
                    )
                }
            elif args.command == "expire":
                result = {"policy_revision": POLICY_REVISION, "expired": runtime.expire(now=now)}
            elif args.command == "inspect":
                result = runtime.inspect_report()
            else:
                if args.destination is None:
                    raise ValueError("DESTINATION_REQUIRED")
                lifecycle = runtime.backup(args.destination, now=now)
                result = {
                    "status": "REDACTED_QUARANTINED_BACKUP",
                    "policy_revision": POLICY_REVISION,
                    **lifecycle,
                }
        print(json.dumps(result))
    except Exception:
        # SDK/validation errors may contain endpoint, credentials or submitted private input.
        print(json.dumps({"status": "FAILED", "code": "CORE_RUNTIME_COMMAND_FAILED"}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
