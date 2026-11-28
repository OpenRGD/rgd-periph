# PERIPH-0.3A — Operator Identity & Authorization Architecture

**Candidate, non-normative, offline only.** Base: accepted extraction a36c2d7c42b0bb3f4b2e4c78b42b999be0fac6cd. This design is not a new authority service, credential issuer, authentication implementation, transport, permission store, API, or host authorization. Existing seven PERIPH schemas and runtime wheel remain unchanged. No deploy, install, device access or actual grant.

## Authority and alignment

- Core [governance](https://github.com/OpenRGD/openrgd/blob/afe1a831b950a4e6560f0285a0103fc4eaa343b7/GOVERNANCE.md) owns common semantics and promotion. Its draft [Core-C #13](https://github.com/OpenRGD/openrgd/pull/13) (fa5e8ff713a28b69b7d39b2c18e8c9e0f6c72ee0) distinguishes Life, robot, runtime, proof and authorization. [Core-B #11](https://github.com/OpenRGD/openrgd/pull/11) (55a034ceb1cdaab8bed6848fcdde85c9436bc9e0) separates proposals from effect gates and uses scoped monotonic lifetimes; [EXT #18](https://github.com/OpenRGD/openrgd/pull/18) (c8565926c9c4d8c91c916d2c1087205ef80fe531) distinguishes declaration, artifact trust, install and operational permission. All three are candidates, not accepted authority.
- External operator identity/session authority authenticates a human and a client. Policy owner issues grants and resolves revocations; consent authority owns applicable consent. PERIPH owns the lifecycle gate, local scope registration and recovery, not universal operator enrollment or identity. Console/CLI is a client, not an issuer merely because it can display a button. Somatic safety and independent emergency procedures remain outside this gate. Catalog membership, role, robot identity, Life, source integrity, CSRF and SO_PEERCRED do not grant operational authority.
- Do not repurpose Core-C Life/RoleLease or Core-B EffectGateDecision as a credential. No new global identity taxonomy, canonical signature format or module catalog. A future effect-gate adapter requires Core review.

## Candidate records

Three additive closed Draft 2020-12 schemas live in contracts/authorization/v0.1-candidate; each carries grants_authority=false and actuation_enabled=false. Intent identifies a request, selected grant reference, exact host/robot/peripheral scope, broker audience, capability, policy revision, expected status revision and receiver-qualified monotonic lifetime. Policy describes exact active grants and revoked grant references at a revision. Decision reports eligible_for_host_review or deny and stable reason, never a bearer token, executable permit or verified audit receipt. No JWT or proof formats frozen.

The policy snapshot is trusted input only after external verification of exact contents, current revision, authority and freshness; a submitted policy or revision alone never suffices. The evaluator accepts a separate typed TrustedContext populated in tests. Types and fixture copies are not cryptographic verification or a security boundary. Future adapter must verify independently, bind immutable bytes, constrain total input size, authenticate channel/session/audience and check current policy and revocation at effect time, not trust client-supplied booleans or JSON. Time domain is receiver-established monotonic session/boot domain; no unaligned cross-host clock comparisons or Chronograf claims. Candidate maxima: intent 30s, current policy snapshot 60s, upper bound exclusive; exact safe integer range 0..2^53-1. Receiver freshness and revocation resolution must be live at dispatch; short TTL alone does not solve replay or partition.

## Minimal capability profile

| Capability | Meaning | Separate requirements |
|---|---|---|
| peripheral.status.read | Metadata only, project known state | Explicit scoped grant, audit readiness; can observe quarantine; no raw samples, media or RI evidence access |
| acquisition.start | Request existing allowlisted acquisition lifecycle | Explicit grant, descriptor capability, current revision, recovery ready, independently verified confirmation bound to full intent; privacy-sensitive capture requires separate applicable consent |
| acquisition.stop | Request existing allowlisted acquisition lifecycle | Explicit grant, same scope/revision/recovery/confirmation checks; no fresh capture consent required to stop |

Unknown privacy classification denies new acquisition. Stop is not an emergency-stop instruction, power-off or motor command; independent safety procedures must remain usable. Roles can be UX templates later, never direct permissions. Scope is exact host_id + robot_id + peripheral_id; no wildcards, group expansion or role inheritance in 0.3A. A grant selects exactly one capability and one principal tuple (issuer, subject, session, client) and audience. Delegation is explicit one-hop assignment by the trusted policy authority to that actor/client/mode, never a client-minted grant or a transitive delegation chain. No grant creation or revocation service in this phase. Session revocation and grant revocation deny even if the grant's time window is valid. No permission to access raw samples, shell, calibration, devices, recordings, evidence, power or motion.

## Decision sequence

1. Strict closed shape and exact integer profile. Invalid/unknown -> deny.
2. Independently verified session and policy content, scope, audience, current policy revision and compatible receiver clock; active session, bounded lifetimes and revocation.
3. Exact grant lookup; duplicated IDs fail closed; exact principal/client/mode/resource/action binding, no implicit grants.
4. Verified registered resource and declared capability, audit readiness. For lifecycle, recovery readiness and current state revision; confirmation receipt matches exact request including UUID, policy, scope, revision and expiry plus verified principal and issuer. Start privacy-sensitive acquisition additionally needs independent consent receipt from configured authority. No-op at target is not exemption from operator policy.
5. Return non-authoritative eligibility. Next runtime gate must re-evaluate atomically with current policy, authenticated peer, allowlist, binding, revision, quarantine and durable intent; consume authorization/idempotency/confirmation at dispatch. 0.3A does not invoke any of those operations.

## Threat model and gates

| Threat | Candidate response / remaining requirement |
|---|---|
| Forged UI identity/role | Exact verified issuer/subject/session/client; actual credential verification and transport remain open |
| Same-UID confused deputy | No browser grant via UID; reviewed restricted gateway plus broker-side enforcement; legacy raw path must be disabled/fenced before activation |
| Cross-robot, host or action substitution | Exact scope/audience and capability, independent resource binding |
| Stale policy, revoked grant/session | Current snapshot and external current revision; recheck revocation immediately before dispatch |
| Replay or TOCTOU | Bound request and revision, short lifetime; durable consume/replay and race-safe transaction remain open |
| Consent spoof / audit outage | External confirmation and independent consent receipt, deny if audit unavailable; real receipt verification, durable audit and privacy policy remain open |
| Restart / cloned state | Existing 01E recovery; authoritative session/epoch fencing and no state rollback, not proven by this evaluator |
| Legacy direct local IPC | Existing 0.1 candidate behavior not upgraded; do not commission new control plane until bypass and migration review |

Audit production requirements: append minimal decision metadata (opaque principal/session/client/grant references, exact scope/capability, request UUID, policy/status revision, correlation/decision/reason, verified clock domain, receipt references, durable dispatch/recovery outcome), access controls and retention. Never log credentials, raw media or secret tokens. Report != durable audit receipt, audit_ready here is a verified-context test input, not implemented persistence.

## Next phases

0.3B: jointly reviewed identity/session resolver and authenticated restricted transport threat model (compare standard solutions; no homemade crypto); bind audience, principal, expiry and revocation. 0.3C: broker-side enforcement and legacy bypass closure, atomic policy/revision/receipt consumption with durable recovery and audit, negative transport tests. 0.3D: read-only-to-lifecycle Console integration, separate permission controls and UI conformance; operator-approved commissioning separately. Core and Console owners review candidates before implementation promotion; do not merge authorization into active host on the strength of offline tests. Keep original read-only IMU and RI paths independent.
