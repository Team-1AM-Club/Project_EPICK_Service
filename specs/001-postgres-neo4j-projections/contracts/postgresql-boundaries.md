# PostgreSQL Contract Boundaries

This document records only the contract facts that shape PostgreSQL persistence. It does not define
a new public API, W3 ACK schema, or queue message payload.

## Accepted persistence boundaries

| Boundary | PostgreSQL rule |
|---|---|
| Identity | Internal owner is UUID. An external issuer/subject maps to exactly one owner. |
| Job acceptance | Job, typed Command, and Outbox record commit atomically. 202 means acceptance only. |
| Job state | Job status, completeness, required actions, and dispatch status are distinct axes. |
| Delivery | SQS Standard is at-least-once. Outbox relay occurs after commit; Inbox dedups consumer/event. |
| Execution authority | Private message/result carries Job ID, Command ID, fence, deletion epoch, and schema version. Result/checkpoint commit rechecks current values in its transaction. |
| Public event | Immutable envelope has schema version, event ID/type, producer, time, aggregate type/ID/revision, and safe payload. It contains no private correlation or private content. |
| Private correlation | job_source_links connects a user Job to public Source/Version in PostgreSQL only. |
| Deletion | Deletion advances owner epoch, invalidates fences, emits private work, and waits for all target acknowledgements. |
| Projection | PostgreSQL source change plus Outbox is atomic. Projection failure records retryable state without changing source truth. |

## Required v1 fixtures before Job/Outbox integration

- W1: Job, Job Command, Checkpoint, decision and deletion/fence behavior.
- W2: one-Source collection command, result, and public Source event.
- Common: public event envelope and replay/dedup examples.

Each schema/fixture pair is immutable within v1. A breaking change receives a new major directory
and requires producer/consumer compatibility testing.

## Explicit W3 hold

The following are not persisted or exposed until D-05 joint adoption: W3 ACK/event name, JSON
fields, state enum, IndexKey, generation/history/replay values, W3-to-W4 usability DTO, and cache
condition. Projection sync state and Inbox receipt do not authorize W4 use or substitute for a W3
ledger.
