# W2 C-01 contract import

These artifacts are pinned to W2 `feat/crawler` commit
`0865ecdfe4748dad5679bc82b9f7386dc663675e` and mirror its
`source_collection/contracts.py` runtime models. They do not define a
backend-specific W2 DTO.

The private Command/Result contract is adopted for W1↔W2 validation. The public
Source Event structure is pinned, while W1/W3 consumer compatibility is recorded
separately in `import-manifest.json`. W3 ACK/usability and W4 input contracts are
not part of this import.

Schema validation checks the static boundary. W2 Pydantic runtime validation
remains required for cross-field invariants such as event/payload matching,
command input-version equality, and Result completion consistency.
