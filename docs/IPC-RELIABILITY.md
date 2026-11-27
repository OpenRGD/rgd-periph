# PERIPH-EXTRACT-01C — IPC reliability and failure semantics

Status: source candidate; non-actuating, local-only; not approved for commissioning.
Baseline: standalone candidate 454eb0590068813f6b7c6daf1033fea00617814b.
Original Console source remains immutable at b331dd32415eeda0d107676920a6dea91170cc4f.
Seven existing candidate JSON schemas are byte-identical. No new IPC operation, privilege, endpoint, hardware driver or runtime dependency.

## Bounded request/reply

- Single request and single newline-terminated JSON reply per Unix socket connection; 2048-byte maximum including newline, strict UTF-8, no duplicate keys or non-finite numeric values. No half-close or pipelining in the supported client protocol.
- Monotonic absolute deadline across partial reads rather than a renewed timeout for each fragment.
- Broker read budget 2 s, reply budget 2 s, individual systemctl/udevadm invocation timeout 4 s. Status may use two probes; acquisition may use two initial probes, one command and two final probes (nominal subprocess budget 20 s).
- Client transaction deadline 30 s covers connect, send and receive; a confirmed CLI action uses separate status and action transactions. Not a hard real-time bound: queueing, OS scheduling, subprocess creation/cleanup and filesystem operations can exceed nominal budgets. The broker is sequential and the backlog is bounded, not an authenticated freshness/TTL mechanism.
- Same UID peer validation on both endpoints. A detected lost peer before dispatch rejects the action; this is not a guarantee of cancellation after dispatch or proof of physical safety.
- Read, parse and send failures are isolated to the individual connection; do not resend a command. Socket cleanup checks ownership of the socket inode.

## Outcome semantics

| Situation | Result | What it proves |
|---|---|---|
| status | status | A host observation; no acquisition command |
| already at target | no_op | A matching observed lifecycle, not sensor freshness |
| command returns success and target lifecycle observed | accepted | Acknowledged lifecycle operation, not physical data or motion authority |
| rejected | rejected, reason | Request not dispatched by this processing attempt; historical UUID results are not fresh observations |
| timeout, command nonzero, or target not confirmed after dispatch | failed / systemd_outcome_unknown | Outcome unconfirmed; side effects may have occurred |
| transport failure during CLI action | local OUTCOME_UNKNOWN, exit 3 and request ID | No trustworthy acknowledgement; may have executed |
| quarantined service | status / reconciliation_required; further actions rejected | Only observations available until independent reconciliation |

The CLI first obtains a read-only status with a separate UUID; without explicit confirmation and actionable status it sends no action. It never automatically retries. Status failure exits 1, missing confirmation exits 2; uncertain action exits 3. Reply validation checks identity, revision, timestamp, nested shape, allowed disposition and action consistency. Full Draft 2020-12 and cross-language policy conformance remain 01D.

The broker consumes the current revision before dispatch, enters an in-memory quarantine before invoking systemctl, and clears that quarantine only for a successful command with the target lifecycle observed. Any unconfirmed dispatch leaves the service quarantined. Subsequent observations may show a target state yet cannot clear quarantine. Reconciliation requires independent operator verification of the actual service and pending systemd jobs, with documented review before any recovery or restart; there is deliberately no IPC reset/unlock operation. Restarting a broker is not reconciliation.

## Bounded replay semantics

Within one broker instance the request ledger retains at most 64 action decisions. UUID keys are case-insensitive for lookup; the complete original request must match exactly for a historical result. Reusing an ID with another scope, action or revision is rejected. Copies prevent mutation of prior acknowledgements. Every dispatch consumes a revision even if unsuccessful. A formerly dispatched request whose ledger entry has been evicted cannot dispatch again with the original revision within the same epoch.

Initial revisions use independent random 48-bit seeds; this is probabilistic restart separation, not durable exactly-once processing or a persisted epoch. Ledger/quarantine are volatile. Persistent restart/recovery and explicit authorized reconciliation remain open release/authorization gates. Status UUIDs without action decisions are not a persistent audit log. No request TTL, operator authentication or distributed fencing is claimed.

## Test scope and outstanding gates

Mock-only systemctl/device functions and temporary local Unix sockets. Cover lost reply, observed late effect, nonzero/target mismatch, failed post-probe, quarantine, changed UUID payload, eviction/replay, distinct simulated restart seeds, peer disconnect before dispatch, per-client reply failure followed by a successful new connection, monotonic deadline, oversized/partial frames, malformed JSON and invalid response identity/shape.

No real service operation, installation, USB open, CAN, audio/video capture or hardware acceptance. Keep PR Draft. PERIPH-EXTRACT-01D (strict schemas and Rust/Python conformance), 01E (reproducible packaging/recovery/compatibility), PERIPH-CONSOLE-01 (read-only consumer) and PERIPH-0.3 authorization are separate. No Console or robot changes.
