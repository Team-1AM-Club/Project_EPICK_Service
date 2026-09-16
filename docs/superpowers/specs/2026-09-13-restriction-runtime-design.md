# W3 restriction integration candidate design

User authorized implementation of the PM follow-up on 2026-09-13. This is a new
subsystem alongside the existing knowledge module; the lab remains independent.

## Scope and decision

Build a real local HTTP consumer, durable SQLite state/outbox, SQLite FTS5 search,
retention enforcement, and a durable W4 signal-consumer adapter. Use Python >=3.12
and existing Pydantic dependencies. No broker/search cluster is assumed available.
An in-memory simulator would not satisfy the request; introducing an unagreed
Kafka/Elasticsearch stack would invent deployment requirements. SQLite plus HTTP
gives a reproducible implementation boundary without claiming production adoption.

The versioned contract is `w3-restriction/0.1-draft`, NOT team-approved. Unknown
fields and versions fail closed. Source-wide restriction applies to all versions
and derived knowledge at current use time; audit metadata remains immutable.
Only public opaque references and enumerated reasons appear in events/signals.

## State and recovery

Ordering uses a Source-wide restriction aggregate revision, not event time or an
extraction revision. Persist receipt/history/state/outbox in one transaction.
Duplicate IDs cannot alter payloads. Stale revisions do not roll back state.
Conflicting revisions quarantine current use until a newer trusted snapshot.
Gaps block use. Replay batches carry a high watermark and retention boundary;
missing expired history requires snapshot recovery, preserving an explicit history
limitation. Snapshot may not be older than any observed revision.

Actual FTS writes and readback of the complete version/extraction/representation/
normalization key precede index ACK. Failure or mismatch retains authoritative
state and prevents use. Restriction immediately removes visible index documents;
release needs explicit reindexing, not resurrection of cached text. Body/FTS rows
expire on access and via an explicit purge endpoint using the real UTC clock.

W4 notification delivery is pull-based durable outbox in this profile. A per-Source
generation increases on every readiness change, including same-revision index
failures. The W4 adapter persists generation, clears cached values on a newer
signal, rejects collisions, and checks current W3 generation before returning a
cache hit. Actual W4 application wiring is a separate team integration step.

## Availability and limits

Bind only to 127.0.0.1; role tokens come from environment, never committed.
Use separate W2 ingest, operator index/recovery, W4 read credentials. This is a
local integration candidate, not a production HTTP server or authorization policy.
Actual W2 replay/snapshot producer, W4 application, W1 authorization, production
TLS/broker endpoints, approval flow and retention durations require team decisions.
Tests must exercise real HTTP, SQLite and FTS; SQL failure can be induced by a real
SQLite trigger, not a success/failure flag in production code.
