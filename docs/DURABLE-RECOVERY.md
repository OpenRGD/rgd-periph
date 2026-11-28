# PERIPH-EXTRACT-01E.1 — Durable recovery

Source-only candidate. Seven wire contracts unchanged. Never install or start a service based on this document alone.

## Write-ahead intent and recovery

Production serve requires an explicitly initialized private state directory (0700), owner lock and journal (0600). Initialization is creation-only; existing, missing or malformed state is not overwritten or reset. Use the same reviewed state directory across broker restarts. One process holds an exclusive nonblocking filesystem lock for the lifetime of the broker or offline maintenance. The socket boundary remains same-UID and local. A mock/preflight Broker without a store is not a production service.

Journal contents are bounded, schema/version and exact allowlist digest bound, checksummed, duplicate-key rejecting and scoped to registered identities. Changes are serialized to a private temporary file, fsynced, atomically replaced and directory-fsynced. Store errors poison that process and prohibit subsequent commands; the broker does not silently recreate the journal. State is not a tamper-proof audit log or protection against the same Linux UID. Use a local filesystem that supports advisory locking and durable atomic replacement; no network/shared/rollback filesystem claim.

On a new serve, persist allocation of a revision epoch before any request. Epoch blocks are disjoint (2^32 revisions each, 21-bit epoch), stay within the existing safe integer profile and cannot wrap. A former request's revision cannot be used in a later epoch of the same continuous state lineage. Actual host state still determines status changes. If an epoch is exhausted, stop admission rather than wrap. The bounded historical reply ledger is still volatile; no durable exactly-once guarantee, no replay across a restored older journal or independently initialized state directory. Never roll back the recovery journal with a source rollback.

Before the only external dispatch site, persist the exact request as a pending intent. If the write fails, do not dispatch. After a successful systemctl acknowledgement and an observed target lifecycle, persist removal of the intent before returning accepted. A crash/timeout/failed probe after the intent is written leaves the intent pending; subsequent observations cannot clear it. On restart, all pending scopes load as quarantined and reject both acquisition actions. An interrupted completion fsync is uncertain and fails closed in the current process; a recovered journal may contain the original intent or an already confirmed completion, depending on the durable atomic replacement.

## Offline recovery, not operator authorization

- Production CLI requires an explicit state directory. Initialization additionally requires typed confirmation, does not run systemctl, and creates a new directory only.
- Offline inspection acquires the same owner lock; it reports pending request identity and the last external review acknowledgement per scope, no physical evidence.
- Offline acknowledgement requires the exact scope, pending request UUID, bounded external review reference and exact confirmation. It is refused while another process holds the state lock. The maintainer must independently reconcile actual lifecycle, ownership and pending systemd jobs, and document that review before acknowledging. The recorded kind is external_review_asserted, not verified evidence. Acknowledgement never runs systemctl, starts a broker, authorizes an operator or automatically retries the original request.
- A fresh status and a fresh explicitly authorized action are required after recovery. Do not replace a damaged/missing or rolled-back journal to bypass quarantine. A config change requires separate migration/review; not supported here.

No new wire operation/reset/host privilege, browser control, driver, publisher or actuation. Physical commissioning and operator authn/authz remain independent gates. Tests use temporary state directories and fake systemd/device probes; failures and restart are simulated, not a power-loss/fsync guarantee on the deployed filesystem.
