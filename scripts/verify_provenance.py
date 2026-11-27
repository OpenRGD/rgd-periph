"""Verify exact source snapshot and immutable extracted files, without hardware."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = "3b55bca50570497f22f3eadab3752f0f3e8fada1"
MANIFEST = "provenance/source-snapshot.json"
# CLI is intentionally adapted in the following standalone commit.
ADAPTED = {"services/peripheral-manager/control_cli.py"}


def blob_at(path):
    return subprocess.check_output(
        ["git", "show", f"{SNAPSHOT}:{path}"], cwd=ROOT
    )


def verify(data, entry):
    assert len(data) == entry["bytes"], entry["destination_path"]
    assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["destination_path"]
    git_blob = f"blob {len(data)}\0".encode() + data
    assert hashlib.sha1(git_blob).hexdigest() == entry["git_blob_sha"], entry["destination_path"]


def main():
    manifest_bytes = (ROOT / MANIFEST).read_bytes()
    assert manifest_bytes == blob_at(MANIFEST), "origin manifest changed"
    manifest = json.loads(manifest_bytes)
    assert manifest["source_commit"] == "b331dd32415eeda0d107676920a6dea91170cc4f"
    entries = manifest["entries"]
    paths = [entry["destination_path"] for entry in entries]
    assert len(paths) == len(set(paths)) == 16
    assert sum(path.endswith(".schema.json") for path in paths) == 7
    for entry in entries:
        path = entry["destination_path"]
        verify(blob_at(path), entry)
        if path not in ADAPTED:
            verify((ROOT / path).read_bytes(), entry)
    print("PASS: 16 exact origin blobs, 15 unchanged working files; CLI adaptation explicitly recorded")


if __name__ == "__main__":
    main()
