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
