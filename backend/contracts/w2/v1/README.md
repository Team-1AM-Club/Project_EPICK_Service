# W2 private commit-gate contract snapshot

## Source provenance

- **W2 delivery SHA**: `16a7bd2653873a20a563e6d2f54c24c6dc18c373`
- **Manifest**: W2 `contracts/w2-private/artifacts.sha256`, 81 Git-blob entries
- **Verifier**: `python scripts/verify_w2_commit_gate_provenance.py`
- **Snapshot rule**: schemas and fixtures in this directory tree are copied from the pinned Git
  blobs. The verifier hashes `git cat-file blob <SHA>:<path>` output, not platform-normalized
  worktree bytes.

| Artifact | Git blob SHA-256 |
| --- | --- |
| `source-collection.staged-result.schema.json` | `96a820dd3c40746aa8e7a2ac6cb34b3ee73efa5f748e3149e68584ecca08d77d` |
| `source-collection.commit-gate-ack.schema.json` | `c7470349720e217a090798000f9e2ead34b96bd7c00db33050405c2f151d5d99` |
| proposal fixture manifest | `6c759fd6d5b11f84965609311da62adfc0084bea1b3a43ddc6dc4621a355f428` |

## W1 adoption decisions

1. **Digest**: SHA-256 of canonical compact UTF-8 JSON for exactly
   `{"command": ..., "result": ...}`, with sorted keys, `ensure_ascii=False`,
   `allow_nan=False`, preserved Unicode and array order. Envelope, operation and lease identifiers
   are excluded. The W1 implementation rejects duplicate JSON keys and non-finite values before
   calculating this digest; it does not apply Unicode normalization.
2. **Lease**: W1 resolves the active lease only from current locked W1 Job/command state; W2 does
   not create, infer or supply it.
3. **ACK outcome**: only a matching `APPLIED` ACK advances W1 state. `DUPLICATE`, `REJECTED`, stale
   and same-ID/different-digest events are never normalized to success.
4. **Routing**: staged result and gate ACK use a dedicated W2→W1 inbound queue and strict parser.
   The legacy W2 collection-result queue and union remain unchanged.
5. **Result/checkpoint**: W1 retains a staged result only until its ordered local finalizer writes
   the W1-owned result/checkpoint and FINALIZE outbox transaction.

## Compatibility and status

- These artifacts do not amend `w2.collection.v1` or the legacy `CollectionResult` union.
- Unknown schema versions/message types are terminal; they do not fall back to the legacy parser.
- The W1 codec validates the copied Draft 2020-12 schema before immutable Pydantic parsing, then
  separately checks staged command/result binding, digest equality and the newer PURGE epoch.
- `APPLIED`, `DUPLICATE` and `REJECTED` are distinct wire outcomes; current W2 normally replays the
  original APPLIED ACK identity/timestamp/outcome for an exact replay.
- **Joint status**: `JOINT_CT15_PENDING`. W1 has no authority to claim W2's private-store
  STAGED/PREPARED/FINALIZED/ABORTED/PURGED state without the actual W2 relay and inspection hook.
- W2 account/project deletion T067 failures remain W2-owned and unresolved.
