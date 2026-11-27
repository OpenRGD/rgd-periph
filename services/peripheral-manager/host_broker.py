#!/usr/bin/env python3
"""OpenRGD Peripheral Host Manager v0.1 candidate (rootless, local-only).

Runs on the Ubuntu host as a systemd *user* service, never inside the
Console Docker container. Owns NO hardware ports and NO subprocess
readers itself. It can ONLY ask systemd --user to start/stop an allowlisted
acquisition service, currently the one Paky-01 IMU unit. No shell=True,
sudo, arbitrary commands, motor/CAN operations or token file reads.

Unix socket is mode 0600 in a 0700 XDG_RUNTIME_DIR child; SO_PEERCRED
requires the exact same host UID. Current Docker UID 10001 CANNOT use
it: cross-boundary authorization requires a separate PERIPH-0.3 review.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import struct
import subprocess
import sys
import time

SCHEMA = "openrgd.peripheral-host-request.v0.1-candidate"
RESULT_SCHEMA = "openrgd.peripheral-host-response.v0.1-candidate"
ALLOWLIST_SCHEMA = "openrgd.peripheral-host-allowlist.v0.1-candidate"
MAX_MESSAGE = 2048
MAX_SERVICES = 12
MAX_HISTORY = 64
SYSTEMCTL = "/usr/bin/systemctl"
UDEVADM = "/usr/bin/udevadm"
SAFE_UNIT = re.compile(r"^openrgd-[a-z0-9-]{1,62}\.service$")
SAFE_ID = re.compile(r"^[a-z][a-z0-9-]{0,79}$")
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
VALID_ACTIONS = frozenset(("status", "acquisition.start", "acquisition.stop"))
SUBPROCESS_TIMEOUT_SEC = 4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_allowlist(path: Path, uid: int | None = None) -> dict:
    uid = os.getuid() if uid is None else uid
    if path.is_symlink():
        raise ValueError("allowlist_symlink_forbidden")
    mode = path.stat()
    if not stat.S_ISREG(mode.st_mode) or mode.st_uid != uid or mode.st_mode & 0o077:
        raise ValueError("allowlist_owner_or_mode_invalid")
    raw = path.read_bytes()
    if len(raw) > 16 * 1024:
        raise ValueError("allowlist_too_large")
    parsed = json.loads(raw)
    if (not isinstance(parsed, dict)
            or set(parsed) != {"schema_version", "services"}
            or parsed["schema_version"] != ALLOWLIST_SCHEMA
            or not isinstance(parsed["services"], list)
            or not 1 <= len(parsed["services"]) <= MAX_SERVICES):
        raise ValueError("allowlist_schema_invalid")
    keys: set[tuple[str, str]] = set()
    units: set[str] = set()
    resources: set[str] = set()
    devices: set[str] = set()
    for row in parsed["services"]:
        expected = {"robot_id", "peripheral_id", "service_ref",
                    "device_by_path", "usb_vid_pid", "resource_lock"}
        if not isinstance(row, dict) or set(row) != expected:
            raise ValueError("allowlist_service_shape")
        if not SAFE_ID.fullmatch(row["robot_id"]) or not SAFE_ID.fullmatch(row["peripheral_id"]):
            raise ValueError("invalid_peripheral_identity")
        if not SAFE_UNIT.fullmatch(row["service_ref"]):
            raise ValueError("invalid_unit")
        dev = row["device_by_path"]
        if (not isinstance(dev, str) or not dev.startswith("/dev/serial/by-path/")
                or ".." in dev or any(ch.isspace() for ch in dev)):
            raise ValueError("invalid_fixed_device_path")
        if not re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{4}", row["usb_vid_pid"]):
            raise ValueError("invalid_usb_vid_pid")
        if not re.fullmatch(r"[a-z][a-z0-9:_-]{0,127}", row["resource_lock"]):
            raise ValueError("invalid_resource_lock")
        key = (row["robot_id"], row["peripheral_id"])
        resource = row["resource_lock"]
        device_path = row["device_by_path"]
        if (key in keys or row["service_ref"] in units
                or resource in resources or device_path in devices):
            raise ValueError("duplicate_exclusive_owner")
        keys.add(key)
        units.add(row["service_ref"])
        resources.add(resource)
        devices.add(device_path)
    return parsed


def systemd_show(unit: str, runner=subprocess.run) -> tuple[str, bool | None, bool]:
    """Returns lifecycle, enabled-on-boot, manager reachable. No shell."""
    args = [SYSTEMCTL, "--user", "show", unit, "--no-pager",
            "--property=LoadState,ActiveState,SubState,UnitFileState"]
    try:
        proc = runner(args, capture_output=True, text=True,
                      timeout=SUBPROCESS_TIMEOUT_SEC, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown", None, False
    if proc.returncode != 0:
        return "unknown", None, False
    fields = dict(line.split("=", 1) for line in proc.stdout.splitlines()
                  if "=" in line and line.split("=", 1)[0] in
                  {"LoadState", "ActiveState", "SubState", "UnitFileState"})
    if fields.get("LoadState") in ("not-found", "error", "masked"):
        return "not_installed", False, True
    if fields.get("LoadState") != "loaded":
        return "unknown", None, True
    active = fields.get("ActiveState", "unknown")
    lifecycle = {
        "active": "running", "inactive": "stopped", "failed": "failed",
        "activating": "starting", "deactivating": "stopping"
    }.get(active, "unknown")
    enabled = fields.get("UnitFileState") in ("enabled", "enabled-runtime")
    return lifecycle, enabled, True


def device_identity(row: dict, runner=subprocess.run) -> tuple[str, bool]:
    """Read-only USB identity check via udevadm; NEVER open IMU serial."""
    path = Path(row["device_by_path"])
    if not path.exists():
        return "absent", False
    try:
        target = path.resolve(strict=True)
        if not str(target).startswith(("/dev/ttyUSB", "/dev/ttyACM")):
            return "unknown", False
        if not stat.S_ISCHR(target.stat().st_mode):
            return "unknown", False
    except OSError:
        return "unknown", False
    try:
        command = [UDEVADM, "info", "--query=property", "--name", str(path)]
        proc = runner(command, capture_output=True, text=True,
                      timeout=SUBPROCESS_TIMEOUT_SEC, check=False)
        if proc.returncode != 0:
            return "present", False
        props = dict(line.split("=", 1) for line in proc.stdout.splitlines()
                     if "=" in line and line.split("=", 1)[0] in
                     {"ID_VENDOR_ID", "ID_MODEL_ID"})
        detected = f"{props.get('ID_VENDOR_ID', '').lower()}:{props.get('ID_MODEL_ID', '').lower()}"
        return "present", detected == row["usb_vid_pid"]
    except (OSError, subprocess.TimeoutExpired):
        return "present", False


def run_unit(action: str, unit: str, runner=subprocess.run) -> bool:
    # No user-supplied command, PID, signal, endpoint, or device path.
    if action not in ("start", "stop") or not SAFE_UNIT.fullmatch(unit):
        return False
    try:
        result = runner([SYSTEMCTL, "--user", action, unit, "--no-pager"],
                        capture_output=True, text=True,
                        timeout=SUBPROCESS_TIMEOUT_SEC, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


class Broker:
    """Single-threaded manager; an independent host broker instance is the
    sole state-revision allocator. No state or credentials go into Console."""
    def __init__(self, config: dict, runner=subprocess.run):
        self.services = {(s["robot_id"], s["peripheral_id"]): s
                         for s in config["services"]}
        self.runner = runner
        self.revisions = {key: secrets.randbelow((1 << 48) - 1) + 1
                          for key in self.services}
        self.signatures: dict[tuple[str, str], tuple] = {}
        self.history: OrderedDict[str, tuple[str, dict]] = OrderedDict()

    def _status(self, key: tuple[str, str]) -> dict:
        svc = self.services[key]
        lifecycle, enabled, available = systemd_show(svc["service_ref"], self.runner)
        presence, binding_verified = device_identity(svc, self.runner)
        signature = lifecycle, enabled, available, presence, binding_verified
        if key in self.signatures and signature != self.signatures[key]:
            self.revisions[key] += 1
        self.signatures[key] = signature
        return {
            "device": {"presence": presence, "binding_verified": binding_verified},
            "publisher": {
                "lifecycle": lifecycle, "manager_available": available,
                "enabled_on_boot": enabled,
                "service_ref": svc["service_ref"]
            },
            "status_revision": self.revisions[key]
        }

    def _reply(self, req: dict, state: dict | None,
               disposition: str, error_code: str | None = None) -> dict:
        return {
            "schema_version": RESULT_SCHEMA,
            "request_id": req.get("request_id", ""),
            "robot_id": req.get("robot_id", ""),
            "peripheral_id": req.get("peripheral_id", ""),
            "observed_at": utc_now(),
            "device": state["device"] if state else {
                "presence": "unknown", "binding_verified": False
            },
            "publisher": state["publisher"] if state else {
                "lifecycle": "unknown", "manager_available": False,
                "enabled_on_boot": None, "service_ref": None
            },
            "status_revision": state["status_revision"] if state else 0,
            "disposition": disposition, "error_code": error_code,
            "actuation_enabled": False
        }

    def process(self, req: object) -> dict:
        if not isinstance(req, dict):
            return self._reply({}, None, "rejected", "invalid_request")
        keys = {"schema_version", "request_id", "robot_id",
                "peripheral_id", "action", "expected_status_revision"}
        if set(req) - keys or not all(k in req for k in keys - {"expected_status_revision"}):
            return self._reply(req, None, "rejected", "invalid_request")
        if (not isinstance(req["schema_version"], str)
                or not isinstance(req["action"], str)
                or req["schema_version"] != SCHEMA
                or req["action"] not in VALID_ACTIONS):
            return self._reply(req, None, "rejected", "invalid_action")
        if (not isinstance(req["robot_id"], str)
                or not isinstance(req["peripheral_id"], str)
                or not SAFE_ID.fullmatch(req["robot_id"])
                or not SAFE_ID.fullmatch(req["peripheral_id"])):
            return self._reply(req, None, "rejected", "invalid_identity")
        if not isinstance(req["request_id"], str) or not UUID.fullmatch(req["request_id"]):
            return self._reply(req, None, "rejected", "invalid_request_id")
        key = req["robot_id"], req["peripheral_id"]
        if key not in self.services:
            return self._reply(req, None, "rejected", "unregistered_peripheral")
        if req["action"] == "status" and "expected_status_revision" in req:
            return self._reply(req, None, "rejected", "unexpected_revision")
        if req["action"] != "status" and (
                type(req.get("expected_status_revision")) is not int
                or req["expected_status_revision"] < 0):
            return self._reply(req, None, "rejected", "expected_revision_required")

        state = self._status(key)
        if req["action"] == "status":
            return self._reply(req, state, "status")
        if req["request_id"] in self.history:
            prior_action, past = self.history[req["request_id"]]
            if (past["robot_id"], past["peripheral_id"]) != key or prior_action != req["action"]:
                return self._reply(req, state, "rejected", "request_id_reused")
            return past.copy()
        if req["expected_status_revision"] != state["status_revision"]:
            return self._reply(req, state, "rejected", "stale_status_revision")
        if not state["publisher"]["manager_available"]:
            return self._reply(req, state, "rejected", "manager_unavailable")
        if state["publisher"]["lifecycle"] in ("not_installed", "unknown"):
            return self._reply(req, state, "rejected", "service_unavailable")
        if state["publisher"]["lifecycle"] in ("starting", "stopping"):
            return self._reply(req, state, "rejected", "transition_in_progress")

        start = req["action"] == "acquisition.start"
        present = state["device"]["presence"] == "present"
        verified = state["device"]["binding_verified"]
        if start and state["publisher"]["lifecycle"] == "running":
            result = self._reply(req, state, "no_op")
        elif not start and state["publisher"]["lifecycle"] == "stopped":
            result = self._reply(req, state, "no_op")
        elif start and (not present or not verified):
            result = self._reply(req, state, "rejected", "device_missing_or_unverified")
        else:
            # Any unmanaged serial reader remains untouched. If serial is
            # busy the publisher will fail acquiring its exclusive lock;
            # this broker never sends signals to unrelated processes.
            action = "start" if start else "stop"
            success = run_unit(action, self.services[key]["service_ref"], self.runner)
            after = self._status(key)
            result = self._reply(req, after, "accepted" if success else "failed",
                                 None if success else "systemd_action_failed")
        self.history[req["request_id"]] = (req["action"], result.copy())
        self.history.move_to_end(req["request_id"])
        if len(self.history) > MAX_HISTORY:
            self.history.popitem(last=False)
        return result


def socket_path() -> Path:
    value = os.environ.get("XDG_RUNTIME_DIR", "")
    if not value:
        raise ValueError("XDG_RUNTIME_DIR missing: systemd --user must be available")
    root = Path(value)
    if root.is_symlink() or not root.is_dir() or root.stat().st_uid != os.getuid():
        raise ValueError("untrusted_runtime_dir")
    return root / "openrgd" / "peripherals.sock"


def ensure_socket_dir(path: Path) -> None:
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        raise ValueError("socket_parent_symlink_forbidden")
    parent.mkdir(mode=0o700, exist_ok=True)
    state = parent.stat()
    if not stat.S_ISDIR(state.st_mode) or state.st_uid != os.getuid() or state.st_mode & 0o077:
        raise ValueError("socket_parent_permissions_invalid")


def peer_allowed(conn: socket.socket, expected_uid: int | None = None) -> bool:
    expected_uid = os.getuid() if expected_uid is None else expected_uid
    if not hasattr(socket, "SO_PEERCRED"):
        return False
    try:
        data = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        pid, uid, gid = struct.unpack("3i", data)
        return uid == expected_uid and pid > 0
    except (OSError, struct.error):
        return False


def serve(manager: Broker, path: Path, *, stop_event=None, register_handlers=True) -> None:
    ensure_socket_dir(path)
    if path.exists() or path.is_symlink():
        raise FileExistsError("existing broker socket: refuse to replace a live/unknown socket")
    os.umask(0o077)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(path))
        os.chmod(path, 0o600)
        listener.listen(4)
        listener.settimeout(0.5)
        stopping = False
        def on_stop(_signum, _frame):
            nonlocal stopping
            stopping = True
        previous_term = previous_int = None
        if register_handlers:
            previous_term = signal.signal(signal.SIGTERM, on_stop)
            previous_int = signal.signal(signal.SIGINT, on_stop)
        try:
            print("Peripheral Host Manager: same-UID unix socket active; no auto-start", flush=True)
            while not stopping and not (stop_event is not None and stop_event.is_set()):
                try:
                    conn, _ = listener.accept()
                except socket.timeout:
                    continue
                with conn:
                    conn.settimeout(2.0)
                    if not peer_allowed(conn):
                        # No response for unauthorized/unknown peers.
                        continue
                    try:
                        message = conn.makefile("rb").readline(MAX_MESSAGE + 1)
                        if not message or len(message) > MAX_MESSAGE or not message.endswith(b"\n"):
                            continue
                        request = json.loads(message)
                        answer = manager.process(request)
                    except (ValueError, UnicodeError, OSError):
                        continue
                    reply = json.dumps(answer, separators=(",", ":")).encode("utf-8") + b"\n"
                    conn.sendall(reply)
        finally:
            if register_handlers:
                signal.signal(signal.SIGTERM, previous_term)
                signal.signal(signal.SIGINT, previous_int)
    finally:
        listener.close()
        if path.exists() and stat.S_ISSOCK(path.lstat().st_mode):
            path.unlink()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allowlist", type=Path, required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--serve", action="store_true")
    group.add_argument("--preflight", action="store_true")
    args = p.parse_args(argv)
    config = read_allowlist(args.allowlist)
    manager = Broker(config)
    if args.preflight:
        for key in manager.services:
            status = manager._status(key)
            print(json.dumps({"robot_id": key[0], "peripheral_id": key[1], **status,
                              "actuation_enabled": False}, separators=(",", ":")))
        return 0
    serve(manager, socket_path())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        # Do not leak raw auth tokens, service env or user-supplied JSON.
        print(f"Peripheral broker unavailable: {type(error).__name__}", file=sys.stderr)
        sys.exit(1)
