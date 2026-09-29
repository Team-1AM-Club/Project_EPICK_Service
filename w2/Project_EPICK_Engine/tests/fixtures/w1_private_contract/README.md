# W1 private transport — pinned offline snapshots

These are unmodified synthetic JSON contracts and fixtures from
`Team-1AM-Club/Project_EPICK_Service` at commit
`afec08a9602132e5e433b523b0b6804850440524` (2026-09-18 adoption).
`manifest.json` records each upstream path, Git blob SHA, and UTF-8/LF SHA-256.
Tests use these snapshots without a GitHub checkout, credentials, or network.
They are test inputs, not a second authoritative contract maintained by W2.

The same-command Core dispatch and AVAILABLE lookup now contain the same command,
including the canonical uppercase `core_source_decision.decided_by: "W3"`.
Derived test cases copy an input and state what changed; they are not upstream
runtime acceptance evidence. The original snapshots are not silently repaired.

The pinned partial fixture uses the existing `core_failure_decision` action, with
the ordered context choices `continue_limited` / `stop` / `retry`. A standalone
`continue_limited` action is rejected by both the pinned schema and runtime union.
The pinned policy-failure fixture permits `policy_revision: null` only when
`completion_kind: "none"` and every failure has `stage: "policy"`. Invalid null,
zero, and negative revisions are tested without changing the Domain models.

Core and direct registration have separate wrappers and pins. Core commands must
match pin company/Source, core flag, uppercase scope owner, reason, and numeric
decision revision. Direct registration retains W1 ownership, non-core status, and
an explicitly present null `question_version_id`; it cannot become a Core wrapper.
The pin's opaque `analysis_input_version` / `registration_input_version` is
preserved separately from `decision_version`, which binds all three numeric W2
command revisions. AVAILABLE lookup comparison covers the entire command, not
only command ID/fence/deletion epoch. Lookup success remains no execution permit.

QUESTION_MATCHING is not implemented as a successful collection dispatch at this
pin. W1's `backend/app/runtime/core_decision_binding.py` pure validator requires
`w2_command.company_id == pin.company_id` for both Core scopes; QUESTION_MATCHING
requires a null pin company, while the pinned command schema and CollectionCommand
require a UUID company. The codec fails closed instead of changing either rule.
Supporting this path needs a W1 contract correction/agreement, not a W2 coercion.

Binding rules were checked against the pinned upstream
`backend/contracts/w1/v1/README.md` sections 1.1 and 3.1,
`backend/app/runtime/core_decision_binding.py`, and
`backend/app/runtime/workers.py` direct-registration dispatch construction.
All 24 snapshots were checked against their upstream Git blob SHA after UTF-8/LF
normalization during adoption; the manifest keeps that provenance and SHA-256.

The snapshot set covers Core/direct dispatch, lookup request and all seven semantic
statuses, private errors, and complete/partial/failure/policy-failure serialization.
Only the W2 result subset of the shared private envelope is consumed here.
The schemas and payload versions are unchanged; W1 owns their canonical transport.

Offline validation proves local wire pin-to-command and lookup binding only. It
does **not** prove service authentication, W1 database/Job type checks, actual
standalone registration/CT-13 execution, slot acquisition, atomic
commit versus cancellation/deletion, durable deduplication, or Linux queue recovery.
No fixture is permission to execute a command. The codec is not a
`W1WorkerControl` or `ExecutionAuthorityLocker` implementation.

See `specs/001-official-source-collection/contracts/README.md` in the parent
workspace and `W1_Followup_Contract_Request_2026-09-16_r2.md` in its `.agents/docs`
for the integration gates. T059/T060 remain actual-runtime integration tasks.

The pinned gate command schema, upstream `commit-gate-manifest.json`, four action
fixtures and six structural rejection fixtures are also snapshotted verbatim.
The upstream gate manifest pins W1 commands only; it does not adopt W2 ACKs.
PURGE requires a newer epoch semantically, while non-PURGE forbids the purge epoch
field entirely, including null. Parsing validates syntax/semantics only; comparing
valid bindings/revisions against persisted operations belongs to the private store.
The W2 proposals remain separate in `../w2_commit_gate_proposal/`, unadopted and
queue-disconnected. No gate snapshot authorizes public Source deletion.
