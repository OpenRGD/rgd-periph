# RGD-PERIPH

OpenRGD Peripheral Manager — universal peripheral discovery, lifecycle, ownership and capability management.

**Status: PERIPH-EXTRACT-01 source-only candidate. Not a release or an approved host service. Do not install, start, or commission on a physical robot.**

## Architecture

- Device identity/presence, acquisition owner/lifecycle and stream freshness are independent.
- One exclusive acquisition owner; many read-only consumers. PERIPH does not implement sensor drivers or evidence storage.
- `crates/periph-core`: pure Rust candidate model and admission policy (Serde + standard library).
- `services/peripheral-manager`: existing rootless Linux broker and local CLI. USB serial identity probe only. Same-UID socket is not application operator authentication.
- `contracts/console/v1`: seven historical candidate schemas, unchanged byte-for-byte. Namespaces are compatibility identifiers, not canonical standard promotion.
- `examples/peripherals`: inert historical compatibility fixtures only; not installed bindings or live measurements.

Console is a future version-pinned client, not a required runtime dependency. No browser actions, motor authority, automatic acquisition, actual robot configuration, publisher credentials, RI storage or new privileges.

## Offline developer checks

```sh
python3 -m pip install --require-hashes --only-binary=:all: --no-deps -r requirements-host.lock.txt
python3 -m pip check
python3 scripts/verify_provenance.py
node --test scripts/peripheral-contract.test.mjs scripts/peripheral-host-contract.test.mjs
cargo test --locked --workspace
cargo fmt --all -- --check
python3 -m unittest discover -s services/peripheral-manager/tests -p 'test_*.py' -v
python3 -m unittest discover -s conformance/tests -p 'test_*.py' -v
```

Tests use fixture/mock service ownership and a temporary Unix socket. They do not install systemd units or access real hardware. The original JS regressions remain, alongside [full Draft 2020-12 schema and managed-host cross-language conformance](docs/CONFORMANCE.md). The Python IPC boundary now validates the existing schemas with jsonschema and RFC 3339 format checking; direct dependency versions are pinned in requirements-host.txt. The Rust library stays a typed policy domain, not a wire ingress. [IPC reliability and failure semantics](docs/IPC-RELIABILITY.md) documents IPC failure semantics and durable recovery. See [durable recovery](docs/DURABLE-RECOVERY.md) for the new state journal. Release packaging, wider consumer/authorization conformance and deployment approval remain open.

## Provenance and license

Derived from [OpenRGD Console consolidation](https://github.com/OpenRGD/rgd-console/commit/b331dd32415eeda0d107676920a6dea91170cc4f). See [extraction record](docs/EXTRACTION.md) and [immutable source manifest](provenance/source-snapshot.json). Original code MIT; third-party rights remain unaffected.

No operational commissioning instructions are included at this stage.

## Candidate distribution

See [Packaging & compatibility](docs/DISTRIBUTION.md), [module descriptor](packaging/module-manifest.candidate.json) and [Console integration handoff](docs/handoffs/PERIPH-TO-CONSOLE-01.md). CI prepares a pure Python wheel and full Rust workspace source archive with checksums; no module registry publication, no automatic activation and no approved deployment. Use the exact source commit and CI artifact, not a floating branch.
