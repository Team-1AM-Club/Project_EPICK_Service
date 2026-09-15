# W2 C-01 contract import

These artifacts were supplied by W2 as the authoritative C-01 contract
package. The package did not include a source-repository commit identifier, so
the byte hashes in `import-manifest.json` are the reproducible import pin. They
do not define a backend-specific W2 DTO.

The private Command/Result contract is adopted for W1↔W2 validation. The public
Source Events use the W1 common event envelope together with W2's
`source-event-payload.schema.json`; the payload schema does not replace the
common envelope. W1/W3 consumer compatibility is recorded separately in
`import-manifest.json`. W3 ACK/usability and W4 input contracts are not part of
this import.

Schema validation checks the static boundary. W2 Pydantic runtime validation
remains required for cross-field invariants such as event/payload matching,
command input-version equality, and Result completion consistency.
