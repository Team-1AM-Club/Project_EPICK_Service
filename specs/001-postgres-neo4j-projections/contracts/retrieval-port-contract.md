# Retrieval Boundary — Deferred From PostgreSQL Implementation

**Status**: intentionally deferred.

The PostgreSQL plan supplies the source-of-truth prerequisites for a future retrieval boundary:
owner-scoped immutable Versions, fixed Snapshots, exclusions, source lineage, generic Outbox
state, deletion epochs, and final relational revalidation.

It does not define a candidate retrieval request or response. The exact W3 Projection and W4
candidate contracts remain unadopted at D-05. In particular, this document does not authorize
Graph/Vector routes, W3 ACK fields/statuses, cache validity values, embedding metadata, or an
Engine capability profile.

When D-05 is jointly adopted, the contract owner must add a versioned schema and fixtures, then
the Service may add a PostgreSQL adapter and an additive migration if typed persistence is needed.
All returned references must still be revalidated in PostgreSQL for owner, Snapshot membership,
exclusion, current use/deletion/sensitivity state, and exact Version before they become a
candidate or user-visible result.
