"""Build the reviewed source allowlist as a pure Python wheel. No install/start."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("host_broker.py", "control_cli.py", "contract_validation.py", "recovery_state.py")


def build(output):
    manifest = json.loads((ROOT / "provenance/source-snapshot.json").read_text())
    schemas = [e for e in manifest["entries"] if e["destination_path"].endswith(".schema.json")]
    assert len(schemas) == 7
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as temporary:
        stage = Path(temporary)
        package = stage / "rgd_periph_host"
        schema_root = package / "schemas"
        schema_root.mkdir(parents=True)
        (package / "__init__.py").write_text('"""RGD-PERIPH host candidate; no auto-start."""\n', encoding="utf-8")
        for name in MODULES:
            shutil.copyfile(ROOT / "services/peripheral-manager" / name, package / name)
        for entry in schemas:
            original = ROOT / entry["destination_path"]
            data = original.read_bytes()
            assert hashlib.sha256(data).hexdigest() == entry["sha256"]
            (schema_root / original.name).write_bytes(data)
        shutil.copyfile(ROOT / "packaging/pyproject.toml", stage / "pyproject.toml")
        shutil.copyfile(ROOT / "LICENSE", stage / "LICENSE")
        env = dict(os.environ, SOURCE_DATE_EPOCH="1795824000", PYTHONHASHSEED="0")
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps",
                        "--no-build-isolation", "--no-index", "--wheel-dir",
                        str(output.resolve()), str(stage)], env=env, check=True)
    wheels = list(output.glob("rgd_periph_host-0.1.0a1-*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as z:
        for e in schemas:
            name = "rgd_periph_host/schemas/" + Path(e["destination_path"]).name
            assert hashlib.sha256(z.read(name)).hexdigest() == e["sha256"]
        for name in MODULES:
            assert z.read("rgd_periph_host/" + name) == (ROOT / "services/peripheral-manager" / name).read_bytes()
        assert not any(n.startswith("rgd_periph_host/tests/") for n in z.namelist())
    print("Wheel SHA256", hashlib.sha256(wheels[0].read_bytes()).hexdigest())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output)
