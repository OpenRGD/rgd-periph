"""Installed-wheel smoke test; no host command, no device or service."""
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from rgd_periph_host import host_broker as broker
from rgd_periph_host import control_cli as cli
from rgd_periph_host.contract_validation import NAMES, validator
from rgd_periph_host.recovery_state import RecoveryStore


def main():
    assert version("rgd-periph-host") == "0.1.0a1"
    assert len(NAMES) == 7
    for name in NAMES:
        validator(name)
    service = {"robot_id": "wheel-fixture", "peripheral_id": "imu-a",
               "service_ref": "openrgd-wheel-fixture.service",
               "device_by_path": "/dev/serial/by-path/fixture-only",
               "usb_vid_pid": "1a86:7523", "resource_lock": "wheel:imu"}
    config = {"schema_version": broker.ALLOWLIST_SCHEMA, "services": [service]}
    with TemporaryDirectory() as tmp:
        root = Path(tmp) / "state"
        RecoveryStore.initialize(root, config)
        with RecoveryStore(root, config) as store:
            def forbidden(*args, **kwargs):
                raise AssertionError("external subprocess")
            manager = broker.Broker(config, runner=forbidden, recovery=store)
            req = {"schema_version": broker.SCHEMA, "request_id": str(uuid4()),
                   "robot_id": service["robot_id"], "peripheral_id": service["peripheral_id"],
                   "action": "status"}
            with patch.object(broker, "systemd_show", return_value=("stopped", False, True)), \
                 patch.object(broker, "device_identity", return_value=("absent", False)):
                response = manager.process(req)
            assert cli.validate_reply(response, req) == response
            assert response["actuation_enabled"] is False
    print("PASS installed package, all seven schema resources, private recovery, read-only mock IPC")


if __name__ == "__main__":
    main()
