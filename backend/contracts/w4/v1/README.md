# W4 → W1 Question Core Decision contract

This directory records the W1-adopted **wire shape** for a private W4
`QUESTION_MATCHING` decision. It is a contract and persistence input only: no
W4 SQS sender, queue, IAM identity, consumer or production policy is enabled by
this directory.

## Provenance and adoption boundary

- Candidate schema SHA-256: `1eb0506d9b13e22198ea11fb5fccd628096de2903b0fe5b1da3d383928d8407a`
- Candidate adoption record SHA-256: `6b4bfe3b0796c376b9b5f2831e66aaf526c3f27f94e80f5112f2df0d559b537f`
- W4 producer full SHA/image digest: **not supplied**
- Runtime transport principal/SenderId: **not supplied**

The source-byte-preserving candidate is retained as
`w4-question-core-decision.event.candidate.schema.json`. The adjacent
`question-core-decision.event.schema.json` is the W1-owned adopted validation
schema with the same required wire semantics.

`job_id` is mandatory and W1 never infers it from any other field. A W4
payload has `company_id: null`; W1 derives company only under its own locked
currentness checks at a later explicit retry boundary. `message_id` identifies
delivery; `decision_id` identifies the W4 decision and is retained separately.

## W1 inbound boundary

Before mutation W1 independently verifies the configured transport principal;
the body value `producer: "w4"` is only a schema assertion. W1 then locks the
User, exact Job, current Project/ProjectVersion, active Question/QuestionVersion,
JobSourceLink and Source. A missing or stale relation is terminal and W1 never
substitutes a "recent" or otherwise inferred Job.

One accepted event atomically creates the W1-owned inbox receipt, immutable
`AnalysisSourceDecision`, and producer-scoped binding. It resolves the existing
required-decision action and exposes `RETRY` (Core) or `STOP` (Non-Core), while
the Job remains `WAITING_USER`. It creates **zero** W2 `JobCommand` and
`OutboxMessage` rows. Only a later explicit user retry may create a new fence
and W2 work.

The W3 `COMPANY_KNOWLEDGE` contract remains separate at `../../w3/v1/`. The
same W1 canonical JSON digest algorithm is used for both producer namespaces.
