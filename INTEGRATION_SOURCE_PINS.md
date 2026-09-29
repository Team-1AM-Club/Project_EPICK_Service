# Integration source pins

This Service branch contains isolated W2, W3, and W4 source snapshots. The upstream repositories are read-only inputs; integration changes are committed only here.

| Component | Upstream branch | Upstream full SHA |
| --- | --- | --- |
| W2 Source Runtime | `Project_EPICK_Engine/feat/crawler` | `536e31ccfb632c1285507eb7dbbfe0e7c8d3df81` |
| W3 Knowledge Runtime | `Project_EPICK_Service/feat/w3-knowledge-validation` | `1d5d29063e828cc473b1b8975175b93a440ca8b7` |
| W4 Question Core | `Project_EPICK_Engine/feat/w4-question-core-runtime` | `9121219e06c83a29251ee60c6c48849bd902c4c7` |
| W4 Recommendation | `Project_EPICK_Engine/feat/w4-recommendation-runtime` | `26aad7f173fee8b6348d1705ea07dea29806a3c1` |
| W4 Service Handoff | `Project_EPICK_Engine/feat/w4-service-handoff` | `e336a6e619730cd5d3cb6d0e13f7209f69adb1bf` |

Each snapshot excludes nested `.git` metadata. This file records upstream sources, not a claim that Phase 4 or the full service is complete.

## Phase 4 Service-copy changes and verification

- W1's protected lookup now exposes authenticated, column-limited Source and Company metadata for the exact live W2 command; this does not grant collection permission.
- The W2 copy resolves an unpinned W1 Source through that lookup, checks an explicit W2-approved site policy, and inserts the exact W1 Company/Source IDs idempotently into W2 PostgreSQL. A mismatched host, path, identity, or policy fails closed.
- W2 outbox replay normalizes persisted timestamps to UTC, so a PostgreSQL session in `Asia/Seoul` does not change the signed event representation.
- W1 v2 private-deletion command serialization now also canonicalizes timestamps to UTC. The same command no longer becomes a binding mismatch when PostgreSQL returns its timestamp under a different session timezone. The ACK-response contract test now reads the actual three-argument callback error helper.
- Isolated local PostgreSQL: W1 lookup/runtime-worker tests 34 passed; W1 v2 deletion/dispatch contract and DB tests 66 passed; W1 Source-collection acceptance/CT15 routing tests 14 passed; W2 Source-onboarding tests 2 passed; one same-run W1 direct-registration → protected lookup → W2 PostgreSQL onboarding test passed with exact IDs and no manual row seeding; W2 non-rendering Source-collection integration tests 473 passed; W2 Source-collection unit tests 1,014 passed; W3 C-01 joint/runtime tests 18 passed. These are local copy tests, not an AWS qualification or a claim of a fully working browser-rendered collector.
- The pinned W2 source has no `RenderedCollector` implementation or production browser runtime. `test_rendering_safety.py` fails at this missing class. Phase 4 T039, T049, T050 and T058 remain open until a real, SSRF-safe renderer and same-run W1/W2/W3 evidence pass.
- W2 contract-test collection expects a separate W2 design/reference bundle at `w2/specs/001-official-source-collection/contracts/`, absent from the pinned Engine clone. Service canonical contracts remain under `backend/contracts/`; they must not be substituted for that W2 reference bundle.
