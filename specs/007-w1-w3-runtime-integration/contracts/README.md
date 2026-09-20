# W1–W3 Runtime Integration Contracts

These contracts design the M1–M5 integration boundary without changing the adopted
`w3.private.core-decision/0.1-candidate` event.

| File | Owner | Purpose | Adoption gate |
| --- | --- | --- | --- |
| `authority-request.schema.json` | W1 | Minimal W3→W1 currentness lookup input | W3-B |
| `authority-response.schema.json` | W1 | W1-derived fields matching W3 `Authorization` | W3-B |
| `deletion-command.schema.json` | W1 | Durable owner/epoch deletion delivery to W3 | W3-C |
| `runtime-readiness.schema.json` | Joint | Non-secret M1–M4 readiness record | W3-D/E |
| `joint-ct12-scenarios.md` | Joint | M5 execution and evidence matrix | W3-F |

The JSON Schemas are candidate private contracts until their listed W3 gate is closed. They must
not be labeled production READY merely because schema validation passes. Secret values and actual
owner/context payloads are transferred only through the approved runtime channel.
