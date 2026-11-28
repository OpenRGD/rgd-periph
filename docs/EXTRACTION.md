# PERIPH-EXTRACT-01A/B — source extraction and standalone candidate

Date: 2026-11-27. Draft only, non-normative, non-actuating.

Source [Console #22](https://github.com/OpenRGD/rgd-console/pull/22), merge `b331dd32415eeda0d107676920a6dea91170cc4f`. [Owner handoff](https://github.com/OpenRGD/rgd-console/blob/b331dd32415eeda0d107676920a6dea91170cc4f/docs/handoffs/CONSOLE-TO-PERIPH-EXTRACT-01.md). Snapshot `3b55bca50570497f22f3eadab3752f0f3e8fada1` preserves 16 source blobs (7 schemas, 5 inert fixtures, Rust domain, broker, CLI, MIT license). Original Git blob and SHA-256 recorded in provenance/source-snapshot.json. No complete Console history or instance configuration imported.

## Adaptations (separate commit)

- Rust source relocated unchanged to crates/periph-core/src/lib.rs; standalone workspace/manifest, no API dependency. Initial dependency ranges are not a reproducible release lock.
- CLI now requires explicit robot and peripheral identity. Transport failure text reports unconfirmed outcome, not certainty of no side effect. Timeout mechanics unchanged; do not operate against a commissioned service.
- Generic broker tests retained; concrete fake device path/IDs replaced with synthetic values. Added two-robot scope and missing-identity CLI tests. Installer tests remain in Console until instance owner handoff. No real host command in the extracted tests.
- JavaScript contract tests relocated to standalone source layout; Console route/Docker and instance installer assertions remain in the original repository, not silently reclassified as PERIPH tests.
- This standalone document and CI replace historical Console-specific architecture and CI for the new owner; original source references below. Historical documents are not installed instructions.
- Exact unchanged schemas/fixtures/library/broker/license checked against snapshot and original SHA-256; CLI adaptation separately committed.

| Original path | Git blob | SHA-256 |
|---|---|---|
| services/peripheral-manager/tests/test_host_manager.py | acc8d55276caa068213460ecd492bc267e1178f1 | d3facc4b99b4c88d5dbb57f9e7945cecc9e59b1f3e56e9d051d79c8c3ad114b1 |
| scripts/peripheral-contract.test.mjs | 2bd024c51ea89366d6f52677e68855c575b969b5 | 8a35d003f1640fa1538f0bd5835d05bd7c510dc7d430c2b410c6d9794982f745 |
| scripts/peripheral-host-contract.test.mjs | 318567329524e01e7abd1b320cceccdaa5f0280b | 75d6fe724165bc404cf8c3f730b68a110f52efe6cf6f8a28c0722b9c201ff4e2 |
| docs/PERIPHERAL-MANAGER-CORE-v01.md | 2f0bb29cbb99de06c5cdf752915369ff545a4b2f | f910dd60a15a84b294157a84729253d3ca566d67eb91dc310c57e08c9baf6bb7 |
| .github/workflows/peripheral-contract.yml | 35eac15782843ef16e67ccb1b8211978b26b5f71 | fb55a40afd312413e6219726d2d13b23d837545bc182ebe4adb26ed6901ec069 |

## Intentionally retained by other owners

Instance: install_paky01_imu.py, peripheral-host-preflight.sh, P01-IMU-PUBLISHER-CONTROL-v01.md, PERIPH-0.2-HOST-MANAGER-BENCH.md. No real bindings, tokens, installation, or publisher duplicated here. Console: main.rs, README, RI correlation helper/tests/workflow and PERIPH-to-RI handoff. RI remains evidence owner; live IMU GET/WS and Pulse unchanged. Original Console code stays in place pending version-pinned client cutover and rollback review.

## Remaining acceptance gates

- PERIPH-EXTRACT-01C: bounded end-to-end timeout, uncertain outcome/reconciliation, disconnect isolation, malformed input and replay/epoch tests; explicit no blind retries.
- PERIPH-EXTRACT-01D: actual Draft 2020-12 validation, shared Rust/Python fixtures and policy parity; response identity, schema and scope verification.
- PERIPH-EXTRACT-01E: reviewed reproducible packaging, standalone release CI, consumer compatibility and license/publication review.
- PERIPH-CONSOLE-01: read-only version-pinned client, unavailable/unknown fallback, existing live telemetry unchanged, verified cutover and rollback before retiring legacy source.
- PERIPH-0.3: authenticated operator/delegation design, capabilities, consent, audit, revocation; same UID not sufficient. No browser control now.
- Physical commissioning requires independent explicit operator approval and actual owner verification; no service installation, start, enable, hardware capture or actuation in this PR.

First extraction is software only, not proof of runtime readiness or independent physical sensor evidence.
