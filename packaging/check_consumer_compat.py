"""Offline public consumer compatibility and registry boundary; no service calls."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/peripheral-manager"))
from contract_validation import validate_instance

PINS = {
    "console": "b331dd32415eeda0d107676920a6dea91170cc4f",
    "ri": "5372f758df79c93fc887f3858d878635447f4a4d",
    "modules": "0268b3748042d04fd3561553616388368881e04a",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in PINS:
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    for name, pin in PINS.items():
        actual = subprocess.check_output(
            ["git", "-C", str(getattr(args, name)), "rev-parse", "HEAD"],
            text=True).strip()
        assert actual == pin, (name, actual)
    source = read_json(ROOT / "provenance/source-snapshot.json")
    entries = [entry for entry in source["entries"]
               if entry["destination_path"].endswith(".schema.json")
               or entry["destination_path"].startswith("examples/peripherals/")]
    assert len(entries) == 12
    for entry in entries:
        actual = (ROOT / entry["destination_path"]).read_bytes()
        original = (args.console / entry["source_path"]).read_bytes()
        assert actual == original
        assert hashlib.sha256(actual).hexdigest() == entry["sha256"]
    imu = read_json(args.console / "contracts/console/v1/imu-observation.v0.1.schema.json")
    version = imu["properties"]["schema_version"]["const"]
    assert version == "console.imu.v0.1-candidate"
    fixture = read_json(ROOT / "examples/peripherals/paky01-imu.descriptor.json")
    validate_instance("descriptor", fixture)
    assert any(stream["sample_schema"] == version for stream in fixture["streams"])
    registry = read_json(args.modules / "registry/schemas/module.v0.1.schema.json")
    distribution = registry["properties"]["distribution"]
    assert distribution["properties"]["kind"]["const"] == "pinned_git_cargo"
    assert "binary" in distribution["required"]
    manifest = read_json(ROOT / "packaging/module-manifest.candidate.json")
    assert manifest["registry"]["registration_status"] == "not_registered"
    assert manifest["safety"]["non_actuating"] is True
    assert manifest["safety"]["auto_start"] is False
    print("PASS 7 exact Console contracts, 5 archived fixtures, IMU read schema, public pins")
    print("Registry enrollment explicitly deferred: Cargo binary-only registry")


if __name__ == "__main__":
    main()
