# EPICK versioned contracts

This directory is the canonical Service-repository location for versioned
cross-workstream contract artifacts. A producer must add its schema, valid
fixtures, invalid fixtures, and the corresponding contract-test registration
in one pull request.

## Public event boundary owned by W1

Every public event is first validated against
`common/v1/event-envelope.schema.json`. The outer record is owned by W1 and
uses `schema_version: "1.0"`.

```json
{
  "schema_version": "1.0",
  "event_id": "<uuid>",
  "event_type": "source.version.available",
  "producer": "w2",
  "occurred_at": "<RFC 3339 timestamp>",
  "aggregate_type": "source",
  "aggregate_id": "<uuid>",
  "revision": 1,
  "payload": {
    "schema_version": "w2.source.v1"
  }
}
```

`event_id` and `revision` belong only to the outer envelope. The domain
version belongs only to `payload.schema_version`. Public payloads must not
contain private owner, Job, Project, authentication, checkpoint, prompt, or
secret fields.

## W2 C-01 contribution boundary

W2 adds only its Source-collection domain artifacts:

- `contracts/w2/v1/source-collection.command.schema.json`
- `contracts/w2/v1/source-collection.result.schema.json`
- `contracts/w2/v1/source-event-payload.schema.json`
- `contracts/fixtures/v1/w2/*.json`

The public Source-event payload schema must require
`payload.schema_version: "w2.source.v1"`. W1 consumes a public Source event
by validating the outer common envelope first, then selecting and validating
the payload schema from `payload.schema_version`. It must not reinterpret the
W2 domain fields or use a private Job/owner correlation in the public event.

W2 adds valid and invalid fixture registrations to
`tests/contract/test_platform_contract_fixtures.py`. CI runs those tests with
`python -m pytest -q`; fixture files that are not registered are not treated
as contract evidence.

W3 ACK/adapter DTOs, W4 usability contracts, database migrations, queue
workers, API endpoints, and Neo4j projections are outside this C-01 boundary.

## W3 → W1 Core Decision inbound boundary

The selectively adopted W3 `COMPANY_KNOWLEDGE` event is versioned separately at
`w3/v1/core-decision.event.schema.json`; it is not the W1 private message envelope and does not
replace the W1/W2 contract. Its provenance is pinned to W3 full SHA
`5afbf9917eb1e02a4e6be08c4569886ca6c10d7b` in `w3/v1/README.md`.

The body assertion `producer: "w3"` is never authentication. A private transport adapter must
independently authenticate the W3 principal before invoking the W1 consumer. W1 remains
authoritative for Job ownership, Source binding, deletion epoch, revision acceptance, execution
fence, and explicit retry.

W1 extends `w1/v1/private-delivery-receipt.schema.json` additively for Core Decision receipts with
a canonical payload digest, W1 decision ID, binding rejection, and same-ID body conflict. Existing
W2 collection-result receipt fixtures remain valid without those Core-only fields.

The local outcome DTO does not imply a deployed ACK transport. W3 outbox deletion, the W3 relay,
and joint CT-12 remain gated until both sides adopt and execute the runtime contract.
