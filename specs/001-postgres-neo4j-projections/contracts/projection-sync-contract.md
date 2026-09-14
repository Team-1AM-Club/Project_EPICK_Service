# Projection Sync Boundary — PostgreSQL Planning Status

**Status**: not an adopted Service-to-W3 contract.

PostgreSQL may implement generic Outbox and projection_sync_states because the W1 platform contract
has fixed their source-of-truth and at-least-once behavior. That does not approve W3 message names,
ACK JSON, status enum, Graph schema, Projection DTO, result API, or W4 use condition.

## PostgreSQL commitments

- A source mutation and an outbox_messages insert are one PostgreSQL transaction.
- A relay sends only committed work and treats SQS Standard delivery as at-least-once.
- Public events use the common v1 envelope and contain no owner, private Job, subject, private
  text, Checkpoint, credential, or secret.
- Private work carries a command reference and current Job fence/deletion epoch on a protected
  route. A consumer must reread and validate PostgreSQL current state.
- projection_sync_states can record PENDING, STALE, ERROR, and SYNCED without changing the source
  record. Lower revisions cannot regress current state.
- PostgreSQL success remains visible if a derived projection fails.

## Explicit hold

W3 restriction/index/usability details are governed by W3_INTEGRATION_CONTRACT_HOLD.md. No typed
W3 ACK table, enum, SQS payload, public endpoint, Graph activation, or W4 eligibility state may be
implemented from a draft. After D-05 adoption, an additive versioned schema, shared fixtures, an
adapter, and then an additive migration are required.
