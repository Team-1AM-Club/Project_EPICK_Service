"""Stage one reconciled W2 deletion retry; never redrive or delete SQS messages.

Run only with a dedicated W1 deletion DB login, a fresh W2 activation proof,
an incident record, and independent W2 receipt/DLQ evidence. The W1 deletion
relay stays paused until both teams review the newly staged outbox.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from uuid import UUID

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.models.registry import load_all_models  # noqa: E402
from app.runtime.integration_preflight import (  # noqa: E402
    require_w2_deletion_activation_proof,
    validate_integration_startup,
)
from app.runtime.session import create_deletion_worker_session_factory  # noqa: E402
from app.runtime.w2_deletion_manual_retry import (  # noqa: E402
    ManualRetryEvidence,
    stage_reconciled_w2_deletion_retry,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deletion-id", type=UUID, required=True)
    parser.add_argument("--original-outbox-id", type=UUID, required=True)
    parser.add_argument("--expected-epoch", type=int, required=True)
    parser.add_argument("--dlq-body-sha256", required=True)
    parser.add_argument("--ack-code", required=True)
    parser.add_argument("--w2-receipt-outcome", choices=("APPLIED", "DUPLICATE"), required=True)
    parser.add_argument("--incident-id", required=True)
    parser.add_argument("--confirm-route-paused", action="store_true", required=True)
    args = parser.parse_args(argv)

    try:
        if validate_integration_startup(settings).get("status") != "ready":
            raise RuntimeError("INTEGRATION_DISABLED")
        proof_path = settings.w2_deletion_activation_proof_path
        image_digest = settings.w2_deletion_image_digest
        if not proof_path or not image_digest:
            raise RuntimeError("ACTIVATION_PROOF_MISSING")
        require_w2_deletion_activation_proof(Path(proof_path), expected_image_digest=image_digest)
        load_all_models()
        factory = create_deletion_worker_session_factory()
        with factory.begin() as session:
            result = stage_reconciled_w2_deletion_retry(
                session,
                evidence=ManualRetryEvidence(
                    deletion_id=args.deletion_id,
                    original_outbox_id=args.original_outbox_id,
                    expected_epoch=args.expected_epoch,
                    expected_body_sha256=args.dlq_body_sha256,
                    ack_code=args.ack_code,
                    w2_receipt_outcome=args.w2_receipt_outcome,
                    incident_id=args.incident_id,
                    route_paused=args.confirm_route_paused,
                ),
            )
    except Exception:
        print(json.dumps({"status": "RETRY_REFUSED"}))
        return 1

    print(
        json.dumps(
            {
                "status": "STAGED_NOT_PUBLISHED",
                "incident_id": args.incident_id,
                "deletion_ref_sha256": hashlib.sha256(result.deletion_id.bytes).hexdigest(),
                "new_outbox_ref_sha256": hashlib.sha256(result.new_outbox_id.bytes).hexdigest(),
                "body_sha256": result.body_sha256,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
