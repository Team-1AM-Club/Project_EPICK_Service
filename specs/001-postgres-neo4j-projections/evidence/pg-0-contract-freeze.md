# PG-0 contract freeze status

> Recorded: 2026-09-14  
> Status: W1/common and private W2 contracts frozen; public W2 Source Event consumer compatibility pending.

## Implemented locally

- common/v1/event-envelope.schema.json defines the immutable public envelope and rejects named
  private correlation/content fields.
- w1/v1 contains Job, private Job Command, Checkpoint, and private deletion-command schemas.
- Synthetic v1 fixtures cover accepted Job, cancel-requested Job, private command, checkpoint,
  deletion command, public restriction event, replay, revision gap, and invalid boundary cases.
- W2 `w2.collection.v1` Command/Result and `w2.source.v1` Source Event schemas and fixtures are
  pinned to W2 `feat/crawler` commit `0865ecd`. The static files preserve the W2 model boundary;
  W2 Pydantic validators remain the authority for cross-field invariants.
- Contract tests validate Draft 2020-12 schemas and valid/invalid local fixtures: 21 passed
  (`tests/contract/test_platform_contract_fixtures.py`, 2026-09-14).
- The eight valid W2 fixtures passed the W2 runtime models; an event/payload mismatch and a Command
  analysis-input-version mismatch were rejected by the W2 runtime.

## Pending public-event compatibility record

- W1 and W3 must record that they accept the pinned `w2.source.v1` public payload shape.
- W3 ACK name/status/payload and W4 usability input remain D-05 work; they are not fields in the
  adopted W2 Source Event.

`backend/contracts/w2/v1/import-manifest.json` records the source commit plus Schema/fixture
checksums. This static contract import does not authorize live dispatch.
