# W2 C-01 contract import

These artifacts were supplied by W2 as the authoritative C-01 contract
package. The byte hashes in `import-manifest.json` are the reproducible import
pin; they do not define a backend-specific W2 DTO.

For the private CollectionResult contract, W1 additionally verified the W2
Pydantic `CollectionResult` parser at the crawler commit recorded in the
manifest. This verification is limited to the result schema and the listed
result fixtures; it does not claim verification of the public Source Event
consumer boundary.

The private Command/Result contract is adopted for W1↔W2 validation. The public
Source Events use the W1 common event envelope together with W2's
`source-event-payload.schema.json`; the payload schema does not replace the
common envelope. W1/W3 consumer compatibility is recorded separately in
`import-manifest.json`. W3 ACK/usability and W4 input contracts are not part of
this import.

Schema validation checks the static boundary. W2 Pydantic runtime validation
remains required for cross-field invariants such as event/payload matching,
command input-version equality, and Result completion consistency.

## W2 CollectionResult runtime verification

`continue_limited` is not a W2 `required_actions[].code`. It is the first
choice of `core_failure_decision.context.choices`. The adopted result Schema
therefore accepts only W2's four discriminated action variants:
`core_failure_decision`, `user_retry`, `correct_input`, and
`find_alternative_source`.

`policy_revision: null` is accepted only for `completion_kind: "none"` where
every failure is at the `policy` stage. It is not replaced with a fabricated
positive revision.

Run the actual W2 parser at the exact commit pinned in the manifest before
claiming runtime compatibility:

```bash
cd backend
python scripts/verify_w2_collection_result_contract.py \
  --crawler-root C:/dev/EPICK_Engine/crawler
```

The command rejects a standalone `continue_limited`, a non-policy failure with
`policy_revision: null`, and a zero policy revision. It is separate from
`pytest tests/contract -q`, which only verifies the Service-side static mirror.
