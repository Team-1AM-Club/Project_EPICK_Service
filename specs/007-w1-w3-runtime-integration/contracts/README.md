# W1–W3 Runtime Integration Contracts

These contracts design the M1–M5 integration boundary without changing the adopted
`w3.private.core-decision/0.1-candidate` event.

| File | Owner | Purpose | Adoption gate |
| --- | --- | --- | --- |
| `authority-request.schema.json` | W1 | Minimal W3→W1 currentness lookup input | W3-B |
| `authority-response.schema.json` | W1 | W1-derived fields matching W3 `Authorization` | W3-B |
| `deletion-command.schema.json` | W1/W3 adopted | Durable owner/epoch deletion delivery to W3 | W3-C closed at `66a0e3e1...` |
| `source-retirement-command.schema.json` | W1/W3 adopted | Durable permanent Source-retirement delivery to W3 | W3-C closed at `66a0e3e1...` |
| `lifecycle-receipt.schema.json` | W3 adopted | APPLIED/DUPLICATE/STALE lifecycle result delivery to W1 | W3-C closed at `66a0e3e1...` |
| `runtime-readiness.schema.json` | Joint | Non-secret M1–M4 readiness record | W3-D/E |
| `joint-ct12-scenarios.md` | Joint | M5 execution and evidence matrix | W3-F |

The lifecycle command and receipt schemas are exact copies of the W3 contracts pinned at
implementation `66a0e3e1b087bf7f9d1b6d7730934ea27f55e94b` and receipt HEAD
`bad8671b2ea8d02bdce2157120b94d2b7edf09d8`. Authority schemas remain candidates until W3-B closes.
No contract is production READY merely because schema validation passes. Secret values and actual
owner/context payloads are transferred only through the approved runtime channel.
