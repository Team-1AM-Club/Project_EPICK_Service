# W2 Source Runtime operator contract

Status: implemented local W2 operator contract.  It defines the runtime boundary
implemented by `epick_engine.source_collection.source_runtime_operator`; it does
not certify W1 infrastructure, IAM, DNS, CA delivery, or an end-to-end deployment.

## Entry points and bounded actions

The installed-console entry point is:

```toml
epick-w2-source-runtime = "epick_engine.source_collection.source_runtime_operator:main"
```

The same operator is usable in the runtime image without package installation:

```text
python -m epick_engine.source_collection.source_runtime_operator \
  preflight|consume-once|relay-once|run
```

Actions have the following boundary:

| Action | Contract |
| --- | --- |
| `preflight` | Loads and validates local configuration and CA material, opens the PostgreSQL connection to check the migration head, and reads SQS queue metadata.  It does not receive, send, delete, or migrate. |
| `consume-once` | Receives at most one W1-authenticated input delivery from the permitted collection/gate input.  It routes one collection/direct-registration command or one commit-gate command and deletes the SQS receipt only after the corresponding durable work succeeds. |
| `relay-once` | Attempts one durable W2 staged-result or commit-gate ACK outbox relay to the W1 private inbound queue. |
| `run` | Alternates bounded input work and bounded relay work.  It does not start a new input action after `SIGINT` or `SIGTERM`; durable unsent relay work remains in the database for a later relay attempt. |

`consume-once` and `relay-once` print only their status.  They return zero for
`APPLIED`, `SENT`, or `EMPTY`, and nonzero for every other result.  `run` exits
nonzero when its bounded loop sees `RECEIVE_FAILED`, `DELETE_FAILED`, or
`SEND_FAILED`; initial configuration/preflight failures also exit nonzero with a
fixed, non-secret diagnostic.

## Fixed in-container inputs

The application accepts exactly these two mounted-file paths:

```text
W2_SOURCE_RUNTIME_CONFIG_FILE=/run/epick/source-runtime/config.json
W1_LOOKUP_CA_FILE=/run/epick/source-runtime/w1-ca.pem
```

`W2_SOURCE_RUNTIME_CONFIG_HOST_FILE` and `W1_LOOKUP_CA_HOST_FILE` are
Compose-only interpolation variables.  They are not application configuration
and no Windows/host path is accepted as the corresponding in-container setting.
The Compose configuration must use long-form, read-only bind mounts for both
files.  The runtime configuration follows
[`source-runtime-config.schema.json`](source-runtime-config.schema.json), must
use `w2.source-runtime-config.v1`, and must contain at least one Source.  On
non-Windows hosts, a group- or world-writable config file is rejected.

The configuration's `claim_lease_seconds` is positive and must exceed each
configured collector hard deadline plus its connect and read timeouts.  The
runtime derives a single heartbeat/visibility cadence of one third of that
claim lease; it does not accept an independent heartbeat override.

## Required settings and secret boundary

All of the following settings must be non-empty before any action starts:

```text
EPICK_DATABASE_URL
W2_SOURCE_RUNTIME_CONFIG_FILE
W1_LOOKUP_ENDPOINT
W1_LOOKUP_BEARER
W1_LOOKUP_CA_FILE
W1_COLLECTION_COMMAND_QUEUE_URL
W1_COMMIT_GATE_COMMAND_QUEUE_URL
W1_PRIVATE_INBOUND_QUEUE_URL
W1_EXPECTED_SYSTEM_SENDER_ID
```

The database URL is limited to `postgresql+psycopg`.  The W1 lookup endpoint
must be HTTPS, and the mounted CA bundle must contain currently valid CA
certificates with a verifiable issuing chain.  The expected SQS sender is a
W1 role identifier; an inbound delivery whose `SenderId` does not match is not
routed or deleted.

The lookup client uses verified TLS and its finite 5-second default request
timeout.  The PostgreSQL engine uses a 5-second connect timeout together with
a 5-second lock timeout and 15-second statement timeout.  The SQS client uses
a 5-second connect timeout, 15-second read timeout, and one total SDK attempt.
These timeouts are implementation safeguards, not permission to extend a
Source's approved execution limits.

Only workload-role credential providers are permitted (web identity,
container role, or IAM role).  Static credential/profile selectors, including
AWS access-key, session-token, profile, shared-credentials, and config-file
environment variables, fail closed.  W1 owns the workload-role delivery and
private network connectivity; neither static AWS credentials nor host
credential mounts belong in this contract.

## Queue identity, topology, and security gate

Each of the three queue URLs must be a valid HTTPS SQS URL with one canonical
AWS region.  Preflight then reads `QueueArn`, `RedrivePolicy`,
`SqsManagedSseEnabled`, and `KmsMasterKeyId` for every queue.  It rejects a
missing or malformed QueueArn, a non-local/cross-account/cross-region DLQ, an
invalid DLQ receive count, and a queue that has neither SQS-managed SSE nor a
non-empty KMS key identifier.

Topology is decided from physical `QueueArn`, never URL spelling:

- Collection and commit-gate inputs with the same QueueArn use one `mixed`
  consumer.
- Different input QueueArns use dedicated `collection` and `gate` consumers.
- The W1 private inbound QueueArn must differ from both input QueueArns.  A
  collision fails preflight, so W2 cannot consume its own staged-result or ACK
  delivery as general runtime input.

The input adapter receives one message with a visibility timeout equal to the
approved claim lease and records both receipt handle and sender ID.  A receipt
visibility heartbeat renews that same lease while collection work runs.  It is
stopped and joined before a successful collection flow proceeds to delete the
receipt.  A visibility-renewal, handler, parsing, authorization, gate, or
database failure does not confirm deletion; the delivery remains available to
SQS retry/redrive.  A delete failure is reported as `DELETE_FAILED` even if
the preceding durable database transition has already committed.

## Durable processing and relay behavior

General collection input is wired explicitly to the configured input provider,
the verified W1 lookup client, `StaticScrapyCollector`,
`extract_static_candidate`, and `handle_collection_dispatch`.  General
commit-gate input is wired to `apply_collection_commit_gate`; it persists the
private ACK as undelivered before receipt deletion.  This operator does not
route general input through the CT15-only operator.

`relay-once` uses the W1 private inbound queue only.  The Task 7 relay claims
one undelivered staged-result or ACK from durable storage for the configured
lease, performs the configured W1 authorization lookup before send, sends one
wire payload, and records delivery only after its database transition.  A send
or delivery-record failure reports `SEND_FAILED`; an unconfirmed relay claim
is released when possible and otherwise expires for a later recovery attempt.
Callers must therefore treat SQS delivery as at-least-once and rely on the
existing command/message identifiers for idempotent handling.

## Readiness, health, shutdown, and observability

Successful `preflight` is the side-effect-free readiness gate.  The module
also exposes `readiness(daemon_active)` and `health(daemon_active)` helpers:
only an explicit active daemon yields `READY` and `HEALTHY`.  The supplied
Compose service liveness check is process-based (`kill -0 1`); it is not a
substitute for preflight, queue permissions, or an end-to-end W1 check.

For `run`, `SIGINT` and `SIGTERM` set a stop event.  The current bounded unit
may finish, but intake stops before the next receive.  The operator restores
the prior signal handlers and disposes its database engine on exit.  It never
claims that a failed or interrupted action was successfully processed.

Do not write a DSN, bearer, receipt handle, full lookup URL/query, CA contents,
or AWS credential to logs, health/readiness output, fixtures, image layers,
or Compose files.  Runtime settings are excluded from their object
representation, and the top-level failure output is intentionally static.
Operational logging must preserve those redaction boundaries.

## Deployment boundary

The runtime image is deliberately independent of packaging metadata at
container build time: it installs the hash-locked runtime requirements, uses
`PYTHONPATH=/app/src`, and invokes the module directly.  W1 supplies the
approved immutable image reference, deployment location, registry access,
workload role, SQS policies, private network/TLS material, and live joint-test
evidence.  No local preflight result proves those external permissions or a
successful W1/W2 deployment.
