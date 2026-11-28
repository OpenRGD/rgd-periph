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
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
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

if __package__:
    from .contract_validation import validate_instance
    from .recovery_state import RecoveryError, RecoveryStore
else:
    from contract_validation import validate_instance
    from recovery_state import RecoveryError, RecoveryStore

SCHEMA = "openrgd.peripheral-host-request.v0.1-candidate"
RESULT_SCHEMA = "openrgd.peripheral-host-response.v0.1-candidate"
ALLOWLIST_SCHEMA = "openrgd.peripheral-host-allowlist.v0.1-candidate"
MAX_MESSAGE = 2048
MAX_SERVICES = 12
MAX_HISTORY = 64
MAX_SAFE_REVISION = (1 << 53) - 1
SYSTEMCTL = "/usr/bin/systemctl"
UDEVADM = "/usr/bin/udevadm"
SAFE_UNIT = re.compile(r"^openrgd-[a-z0-9-]{1,62}\.service$")
SAFE_ID = re.compile(r"^[a-z][a-z0-9-]{0,79}$")
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
VALID_ACTIONS = frozenset(("status", "acquisition.start", "acquisition.stop"))
SUBPROCESS_TIMEOUT_SEC = 4
READ_TIMEOUT_SEC = 2
WRITE_TIMEOUT_SEC = 2
# 2 initial probes + 1 operation + 2 final probes, plus IPC margin.
CLIENT_TIMEOUT_SEC = 30


def strict_json(raw: bytes):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("nonfinite_json_constant")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("nonfinite_json_number")
        return number

    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                      parse_constant=invalid_constant, parse_float=finite_float)


def remaining(deadline: float) -> float:
    budget = deadline - time.monotonic()
    if budget <= 0:
        raise TimeoutError("ipc_deadline")
    return budget


def read_frame(conn: socket.socket, deadline: float) -> bytes:
    data = bytearray()
    while len(data) < MAX_MESSAGE:
        conn.settimeout(remaining(deadline))
        part = conn.recv(min(512, MAX_MESSAGE - len(data)))
        if not part:
            raise ValueError("incomplete_ipc_frame")
        data.extend(part)
        if b"\n" in part:
            if data.count(b"\n") != 1 or not data.endswith(b"\n"):
                raise ValueError("unexpected_trailing_frame")
            return bytes(data)
    raise ValueError("ipc_frame_too_large")


def send_frame(conn: socket.socket, data: bytes, deadline: float) -> None:
    if not data or len(data) > MAX_MESSAGE or not data.endswith(b"\n"):
        raise ValueError("invalid_outgoing_frame")
    conn.settimeout(remaining(deadline))
    conn.sendall(data)


def peer_can_receive(conn: socket.socket) -> bool:
    # The local protocol uses one request per connection, no half-close.
    # A known lost client is not a reason to dispatch a queued action.
    try:
        conn.setblocking(False)
        conn.recv(1, socket.MSG_PEEK)
    except BlockingIOError:
        return True
    except OSError:
        return False
    return False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_allowlist(path: Path, uid: int | None = None) -> dict:
    uid = os.getuid() if uid is None else uid
    if path.is_symlink():
        raise ValueError("allowlist_symlink_forbidden")
    mode = path.stat()
    if not stat.S_ISREG(mode.st_mode) or mode.st_uid != uid or mode.st_mode & 0o077:
        raise ValueError("allowlist_owner_or_mode_invalid")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        actual = os.fstat(stream.fileno())
        if (not stat.S_ISREG(actual.st_mode) or actual.st_uid != uid
                or actual.st_mode & 0o077 or (actual.st_dev, actual.st_ino) != (mode.st_dev, mode.st_ino)):
            raise ValueError("allowlist_changed")
        raw = stream.read(16 * 1024 + 1)
    if len(raw) > 16 * 1024:
        raise ValueError("allowlist_too_large")
    parsed = strict_json(raw)
    validate_instance("host-allowlist", parsed)
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
        if any(not isinstance(row[name], str) for name in expected):
            raise ValueError("allowlist_field_type")
        if not SAFE_ID.fullmatch(row["robot_id"]) or not SAFE_ID.fullmatch(row["peripheral_id"]):
            raise ValueError("invalid_peripheral_identity")
        if not SAFE_UNIT.fullmatch(row["service_ref"]):
            raise ValueError("invalid_unit")
        dev = row["device_by_path"]
        if (not isinstance(dev, str) or not re.fullmatch(r"/dev/serial/by-path/[A-Za-z0-9_.:-]+", dev)
                or ".." in dev):
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


def run_unit(action: str, unit: str, runner=subprocess.run) -> bool | None:
    # No user-supplied command, PID, signal, endpoint, or device path.
    if action not in ("start", "stop") or not SAFE_UNIT.fullmatch(unit):
        return False
    try:
        result = runner([SYSTEMCTL, "--user", action, unit, "--no-pager"],
                        capture_output=True, text=True,
                        timeout=SUBPROCESS_TIMEOUT_SEC, check=False)
    except (OSError, subprocess.TimeoutExpired):
        # Killing the systemctl client does not prove the systemd job stopped.
        return None
    return result.returncode == 0


class Broker:
    """Single-threaded manager; an independent host broker instance is the
    sole state-revision allocator. No state or credentials go into Console."""
    def __init__(self, config: dict, runner=subprocess.run, *, recovery=None):
        self.services = {(s["robot_id"], s["peripheral_id"]): s
                         for s in config["services"]}
        self.runner = runner
        self.recovery = recovery
        self.revision_ceiling = MAX_SAFE_REVISION
        if recovery is not None:
            low, self.revision_ceiling = recovery.allocate_epoch()
            self.revisions = {key: low for key in self.services}
        else:
            # Legacy mock/preflight mode only. Production serve requires state.
            self.revisions = {key: secrets.randbelow((1 << 48) - 1) + 1
                              for key in self.services}
        self.signatures: dict[tuple[str, str], tuple] = {}
        self.history: OrderedDict[str, tuple[dict, dict]] = OrderedDict()
        self.uncertain: set[tuple[str, str]] = (
            recovery.pending_keys() if recovery is not None else set())

    def _status(self, key: tuple[str, str]) -> dict:
        svc = self.services[key]
        lifecycle, enabled, available = systemd_show(svc["service_ref"], self.runner)
        presence, binding_verified = device_identity(svc, self.runner)
        signature = lifecycle, enabled, available, presence, binding_verified
        if key in self.signatures and signature != self.signatures[key]:
            if self.revisions[key] >= self.revision_ceiling:
                raise RecoveryError("revision_epoch_exhausted")
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
            "request_id": req["request_id"] if isinstance(req.get("request_id"), str) and UUID.fullmatch(req["request_id"]) else "",
            "robot_id": req["robot_id"] if isinstance(req.get("robot_id"), str) and SAFE_ID.fullmatch(req["robot_id"]) else "",
            "peripheral_id": req["peripheral_id"] if isinstance(req.get("peripheral_id"), str) and SAFE_ID.fullmatch(req["peripheral_id"]) else "",
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

    def _record(self, req: dict, result: dict) -> dict:
        ledger_id = req["request_id"].lower()
        self.history[ledger_id] = (dict(req), deepcopy(result))
        if len(self.history) > MAX_HISTORY:
            self.history.popitem(last=False)
        return result

    def process(self, req: object, *, may_dispatch=None) -> dict:
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
                or not 0 <= req["expected_status_revision"] <= MAX_SAFE_REVISION):
            return self._reply(req, None, "rejected", "expected_revision_required")
        try:
            validate_instance("host-request", req)
        except ValueError:
            return self._reply(req, None, "rejected", "invalid_request_schema")

        ledger_id = req["request_id"].lower()
        if ledger_id in self.history:
            prior_request, past = self.history[ledger_id]
            if prior_request != req:
                return self._reply(req, None, "rejected", "request_id_reused")
            # Historical acknowledgement, not a fresh observation.
            return deepcopy(past)

        state = self._status(key)
        recovery_failed = self.recovery is not None and not self.recovery.healthy
        if req["action"] == "status":
            reason = ("recovery_unavailable" if recovery_failed else
                      "reconciliation_required" if key in self.uncertain else None)
            return self._reply(req, state, "status", reason)

        def reject(reason: str) -> dict:
            return self._record(req, self._reply(req, state, "rejected", reason))

        if recovery_failed or (self.recovery is None and self.runner is subprocess.run):
            return reject("recovery_unavailable")
        if key in self.uncertain:
            return reject("reconciliation_required")
        if req["expected_status_revision"] != state["status_revision"]:
            return reject("stale_status_revision")
        if not state["publisher"]["manager_available"]:
            return reject("manager_unavailable")
        if state["publisher"]["lifecycle"] in ("not_installed", "unknown"):
            return reject("service_unavailable")
        if state["publisher"]["lifecycle"] in ("starting", "stopping", "busy"):
            return reject("transition_in_progress")

        start = req["action"] == "acquisition.start"
        present = state["device"]["presence"] == "present"
        verified = state["device"]["binding_verified"]
        if start and state["publisher"]["lifecycle"] == "running":
            result = self._reply(req, state, "no_op")
        elif not start and state["publisher"]["lifecycle"] == "stopped":
            result = self._reply(req, state, "no_op")
        elif start and (not present or not verified):
            return reject("device_missing_or_unverified")
        elif state["status_revision"] >= self.revision_ceiling - 1:
            return reject("revision_exhausted")
        elif may_dispatch is not None and not may_dispatch():
            return reject("client_disconnected")
        else:
            # Consume the revision before every dispatch, even failed/uncertain
            # attempts. Eviction from the bounded ledger cannot replay that
            # dispatch with its old revision within this broker epoch.
            self.revisions[key] += 1
            self.uncertain.add(key)
            if self.recovery is not None:
                # The intent must be durable BEFORE the only dispatch site.
                # On write failure the store is poisoned and no command is sent.
                self.recovery.begin(key, req)
            action = "start" if start else "stop"
            success = run_unit(action, self.services[key]["service_ref"], self.runner)
            after = self._status(key)
            target = "running" if start else "stopped"
            if (success is True and after["publisher"]["manager_available"]
                    and after["publisher"]["lifecycle"] == target):
                if self.recovery is not None:
                    self.recovery.complete(key, req["request_id"])
                self.uncertain.remove(key)
                result = self._reply(req, after, "accepted")
            else:
                # The systemd job may have effects after our client returns.
                # Do not unlock by observing a single snapshot or auto-retry.
                result = self._reply(req, after, "failed", "systemd_outcome_unknown")
        return self._record(req, result)


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


def handle_connection(manager: Broker, conn: socket.socket) -> None:
    if not peer_allowed(conn):
        return
    try:
        request = strict_json(read_frame(conn, time.monotonic() + READ_TIMEOUT_SEC))
        answer = manager.process(request, may_dispatch=lambda: peer_can_receive(conn))
        validate_instance("host-response", answer)
        reply = json.dumps(answer, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
        send_frame(conn, reply, time.monotonic() + WRITE_TIMEOUT_SEC)
    except (ValueError, UnicodeError, OSError, RecursionError):
        # A disconnected/malformed client never takes down the listener.
        # Never retry a previously dispatched command here.
        return


def serve(manager: Broker, path: Path, *, stop_event=None, register_handlers=True) -> None:
    if manager.recovery is None and manager.runner is subprocess.run:
        raise RecoveryError("durable_recovery_required")
    ensure_socket_dir(path)
    if path.exists() or path.is_symlink():
        raise FileExistsError("existing broker socket: refuse to replace a live/unknown socket")
    os.umask(0o077)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    owned_socket = None
    try:
        listener.bind(str(path))
        owned_socket = path.lstat()
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
                    handle_connection(manager, conn)
        finally:
            if register_handlers:
                signal.signal(signal.SIGTERM, previous_term)
                signal.signal(signal.SIGINT, previous_int)
    finally:
        listener.close()
        if owned_socket is not None:
            try:
                current = path.lstat()
                if stat.S_ISSOCK(current.st_mode) and (current.st_dev, current.st_ino) == (owned_socket.st_dev, owned_socket.st_ino):
                    path.unlink()
            except FileNotFoundError:
                pass


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allowlist", type=Path, required=True)
    p.add_argument("--state-dir", type=Path)
    p.add_argument("--confirm", default="")
    p.add_argument("--robot-id")
    p.add_argument("--peripheral-id")
    p.add_argument("--request-id")
    p.add_argument("--review-ref")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--serve", action="store_true")
    group.add_argument("--preflight", action="store_true")
    group.add_argument("--init-state", action="store_true")
    group.add_argument("--inspect-recovery", action="store_true")
    group.add_argument("--ack-reconciled", action="store_true")
    args = p.parse_args(argv)
    if not args.preflight and args.state_dir is None:
        p.error("--state-dir is required; no automatic initialization")
    config = read_allowlist(args.allowlist)
    if args.preflight:
        manager = Broker(config)
        for key in manager.services:
            status = manager._status(key)
            print(json.dumps({"robot_id": key[0], "peripheral_id": key[1], **status,
                              "actuation_enabled": False}, separators=(",", ":")))
        return 0
    if args.init_state:
        if args.confirm != "INITIALIZE PERIPH RECOVERY":
            p.error("explicit initialization confirmation required")
        RecoveryStore.initialize(args.state_dir, config)
        return 0
    with RecoveryStore(args.state_dir, config) as store:
        if args.inspect_recovery:
            print(json.dumps(store.inspect(), indent=2))
            return 0
        if args.ack_reconciled:
            if not all((args.robot_id, args.peripheral_id, args.request_id, args.review_ref)):
                p.error("explicit scope, pending request ID and external review reference required")
            store.acknowledge((args.robot_id, args.peripheral_id), args.request_id,
                              args.review_ref, args.confirm)
            print("External reconciliation asserted; no service command executed")
            return 0
        manager = Broker(config, recovery=store)
        serve(manager, socket_path())
    return 0


def entrypoint():
    try:
        return main()
    except (OSError, ValueError, RecursionError) as error:
        # Do not leak raw auth tokens, service env or user-supplied JSON.
        print(f"Peripheral broker unavailable: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(entrypoint())
