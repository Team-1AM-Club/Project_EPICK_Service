# W1–W3 Runtime Integration Evidence

This directory stores non-secret, synthetic, count-only evidence for M1–M5.

## Allowed

- W1 and W3 full source SHAs
- public contract version and canonical document SHA-256
- immutable image manifest digests
- policy/document hashes
- test command names, exit codes, totals, and safe reason codes
- W3 delivery-state counts and W1 receipt/decision/binding/action/command/outbox counts
- SQS visible, in-flight, delayed, and DLQ counts without Queue URLs
- redacted or one-way fingerprints when an identity comparison must be demonstrated
- milestone/gate state and repository-relative evidence references

## Forbidden

- access keys, secret keys, session tokens, bearer tokens, passwords, or complete environment files
- database URLs, Queue URLs, private endpoints, or signed URLs
- raw IAM role ARN, STS UserId, stable Role ID, or SQS SenderId
- owner/user UUID, deletion epoch tied to an actual user, or authentication session identifier
- actual AnalysisPlan, prompt, Source body, canonical URL, or private event body
- raw SQLite database, Docker volume, filesystem/EBS snapshot, or unredacted backup
- stack traces or SDK errors that may contain endpoints, credentials, or submitted content

Evidence generation must use the allowlist serializer in
`backend/app/runtime/w1_w3_evidence.py`. A field not explicitly allowed is rejected rather than
silently copied. Runtime values stay in root-owned mode `600` files or the approved secret channel.
