# Joint CT-12 Scenario Contract

M5 may start only after M1–M4 are complete and W3-F identifies the actual W3 executor and inspect
evidence path. Every scenario uses synthetic private data but the actual W3 caller/supplier/outbox/
relay, AWS SQS, W1 consumer and PostgreSQL.

| ID | Scenario | Required evidence | Pass condition |
| --- | --- | --- | --- |
| CT12-01 | current Core plan | W3 event/digest count; W1 receipt/decision/binding/action counts | one durable apply; command/outbox 0 before explicit retry |
| CT12-02 | same-body replay/duplicate | same event ID/digest and repeated delivery | W1 duplicate only; no extra decision/binding |
| CT12-03 | same ID, modified body | isolated mutation injection | terminal conflict/reject; authority rows unchanged |
| CT12-04 | Non-Core plan | actual optional-source plan and W1 result | safe waiting; no Core command |
| CT12-05 | wrong transport principal | isolated unauthorized sender | W1 rejects before domain mutation |
| CT12-06 | W3 restart/SQLite persistence | inspect before/after container recreation | counters/delivery/tombstone retained |
| CT12-07 | SQS/W1 DB failure and ACK-loss | queue visibility, retry and DB counts | retryable delivery retained; applied once after recovery |
| CT12-08 | cancel before relay/apply | Authority and W1 state before/after | no new W1 effect; no auto-resume |
| CT12-09 | owner deletion race | W1 deletion target, W3 tombstone and late delivery | deletion-first sends none; send-first rejected by W1; other owner unchanged |
| CT12-10 | W3 HELD boundary | bounded failures and inspect | HELD alerted; no automatic replay |
| CT12-11 | explicit user retry | W1 action/command/fence/outbox counts | exactly one new-fence command after action |
| CT12-12 | stale late result and teardown | old-fence outcome plus cleanup manifest | stale effect 0; disposable DB/queue/state/env removed or explicitly retained by approval |

The final evidence manifest must reconcile W3 delivery states, SQS message states and W1
receipt/decision/binding/action/command/outbox counts. `TRANSPORT_HANDOFF` alone is not a pass.
