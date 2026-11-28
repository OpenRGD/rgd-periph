# Changelog

## 0.1.0-alpha.1 (unreleased candidate)

- Extract immutable PERIPH source from consolidated Console #22.
- Retain seven wire schemas and five historical inert fixtures.
- Separate standalone Rust/Python checks from Console and instance tests.
- Require explicit CLI identity; transport outcome remains uncertain on failure.
- No installation, runtime acceptance or canonical contract promotion.

### IPC reliability candidate (01C)

- Bound local IPC reads/writes and client transactions with monotonic deadlines; isolate disconnected clients.
- Unconfirmed dispatch quarantines the service; never retry blindly. Consume revisions on dispatch and bind retained UUIDs to complete requests.
- Strict local JSON/response guards, mock failure/replay tests; schema bytes preserved. Volatile quarantine and restart recovery limitations documented.

### Schema and cross-language conformance candidate (01D)

- Validate all seven frozen Draft 2020-12 schemas with RFC 3339 checking; enforce host request, response and allowlist schemas at Python boundaries.
- Add synthetic corpus: 60 wire cases and 32 managed-host policy vectors executed against actual Rust admission and mocked Python Broker.process.
- Align local safe revision range and exhaustion gate; preserve candidate wire bytes and original immutable snapshot.
- Direct Python schema-validation dependencies pinned; packaging, full runtime recovery, authorization and consumer integration remain separate.

### Durable recovery candidate (01E.1)

- Require an explicitly initialized private state directory for production broker serving.
- Persist a write-ahead pending intent before dispatch; clear only after acknowledged target or explicit offline external-review acknowledgement. Fail closed on state corruption, ownership/configuration mismatch or storage failure.
- Reserve a persisted, bounded revision epoch before serving. Quarantine survives restart; no implicit reset or replay.
