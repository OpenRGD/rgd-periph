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
python3 scripts/verify_provenance.py
node --test scripts/peripheral-contract.test.mjs scripts/peripheral-host-contract.test.mjs
cargo test --workspace
cargo fmt --all -- --check
python3 -m unittest discover -s services/peripheral-manager/tests -p 'test_*.py' -v
```

Tests use fixture/mock service ownership and a temporary Unix socket. They do not install systemd units or access real hardware. The schema checks inherited from Console are not complete JSON Schema validation. Cross-language conformance, strict validation, hardening, packaging and deployment approval remain open.

## Provenance and license

Derived from [OpenRGD Console consolidation](https://github.com/OpenRGD/rgd-console/commit/b331dd32415eeda0d107676920a6dea91170cc4f). See [extraction record](docs/EXTRACTION.md) and [immutable source manifest](provenance/source-snapshot.json). Original code MIT; third-party rights remain unaffected.

No operational commissioning instructions are included at this stage.
