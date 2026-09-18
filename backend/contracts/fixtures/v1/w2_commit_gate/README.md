# W2 unadopted private wire proposals

Synthetic fixtures only. These proposals are **not adopted by W1** and are
disconnected from every queue, runtime worker, Job/checkpoint callback and network.
They do not amend the existing CollectionResult union or normalize W1 success ACKs.

`digest-vector.json` pins inputs and an independently derived Node crypto digest
before production digest implementation. Unicode NFC/NFD text and array order are
preserved. Envelope identity/time, operation ID and lease ID are not digest inputs.
Staged submission contains the original command and result, with no generated lease.

`staged-result.json` and the twelve `ack-{action}-{outcome}.json` files are normal
single-message examples. Each ACK example is an independent scenario, not a combined
delivery stream. Caller-persisted message ID/time must be reused on redelivery.
APPLIED, DUPLICATE and REJECTED remain distinct; no W1 normalized mapping is provided.

`invalid-*.json` must fail both structural JSON Schema and strict model parsing.
`semantic-invalid-*.json` passes JSON Schema but fails model binding, digest or newer
PURGE epoch validation. Schema alone cannot express these cross-field checks.
Structurally valid changes to a gate's binding/revision need stateful store comparison,
not comparison with fixture constants in the context-free codec.

`manifest.json` pins UTF-8/LF SHA-256 for every fixture and both proposal schemas.
No commit SHA is invented for these uncommitted W2 artifacts. The manifest records
the W1 Service command-validation pin separately from proposal authority/status.
See parent-workspace `specs/001-official-source-collection/contracts/w2-commit-gate-proposal.md`.
