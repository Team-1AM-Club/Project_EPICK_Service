# W3 → W1 Core Decision contract

This directory selectively adopts only the W3 `COMPANY_KNOWLEDGE` Core Decision wire contract.
It does not import the W3 implementation, SQLite state, Neo4j integration, C-01 artifacts, or
restriction runtime.

## Provenance

- Producer repository full SHA: `5afbf9917eb1e02a4e6be08c4569886ca6c10d7b`
- Producer contract version: `w3.private.core-decision/0.1-candidate`
- Source path: `contracts/core-decision/v0.1-candidate/`
- Adopted schema SHA-256: `92e9ac40fb7ae337c43f3a669a933e6490bf8cad64e9ad8a4953e542b708fa5e`
- Consumer scenarios SHA-256: `104ac7d48e37fa1c3db4dcdd766a8785333e1da97ea242188362c7a08f23e524`

The valid and invalid fixtures under `../../fixtures/v1/w3/` preserve the producer values and are
traceable to the source hashes in the handoff manifest. W1 mints its own `decision_id`; W3 never
does.

## Trust boundary

The event is a flat external W3 wire object, not the W1 `w1.private.v1` envelope. The body value
`producer: "w3"` is only a schema assertion and is never authentication. The transport adapter
must independently provide an authenticated W3 principal before domain mutation.

W1 owns current User, Job, Source binding, deletion epoch, revision acceptance, execution fence,
and explicit retry. Receiving a valid event never auto-runs a Job.

## Gated integration

The receipt DTO can be produced locally, but an external W3 ACK route, W3 outbox deletion,
operational relay, and joint CT-12 completion remain gated until both sides adopt and execute the
runtime contract.
