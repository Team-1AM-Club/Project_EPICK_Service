"""One-shot W2 public outbox delivery to the pinned W3 C-01 HTTP endpoint.

W1 owns process scheduling, private configuration injection, and the network/TLS
path. Running this module never treats a W3 receipt as an index ACK.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
from collections.abc import Sequence
from uuid import UUID

from epick_engine.source_collection.persistence import (
    create_database_engine,
    create_session_factory,
    database_url_from_environment,
)
from epick_engine.source_collection.w3_public_transport import W3PublicEventPublisher
from epick_engine.source_collection.worker import (
    SourceOutboxDeliveryWorker,
    SqlAlchemyOutboxDeliveryStore,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deliver committed W2 public events to W3")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--replay-event-id", type=UUID)
    args = parser.parse_args(argv)
    if args.limit < 1 or args.limit > 1000:
        parser.error("--limit must be between 1 and 1000")

    engine = None
    try:
        ca_file = os.environ.get("EPICK_W3_CA_FILE")
        ssl_context = ssl.create_default_context(cafile=ca_file) if ca_file else None
        publisher = W3PublicEventPublisher(
            endpoint=os.environ.get("EPICK_W3_EVENT_ENDPOINT", ""),
            bearer_token=os.environ.get("EPICK_W3_W2_TOKEN", ""),
            ssl_context=ssl_context,
        )
        engine = create_database_engine(database_url_from_environment())
        worker = SourceOutboxDeliveryWorker(
            publisher=publisher,
            store=SqlAlchemyOutboxDeliveryStore(session_factory=create_session_factory(engine)),
        )
        if args.replay_event_id is not None:
            worker.replay(event_id=args.replay_event_id)
            status, count = "REPLAYED", 1
        else:
            count = worker.deliver_pending(limit=args.limit)
            status = "DELIVERED" if count else "EMPTY"
        print(json.dumps({"status": status, "count": count}, sort_keys=True))
        return 0
    except Exception:
        print('{"status":"W3_OUTBOX_FAILED"}')
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
